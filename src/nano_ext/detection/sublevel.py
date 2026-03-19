"""Sub-level analysis for multi-level nanopore events.

Uses changepoint detection (binary segmentation + BIC) to identify
level transitions within individual events, converting single-level
events into multi-level events with classified sub-levels.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from nano_ext.models import Event, EventType, SubLevel
from nano_ext.detection.changepoint import binary_segmentation_bic, segment_signal


def analyze_sublevels(
    event: Event,
    signal: np.ndarray,
    baseline: np.ndarray,
    sampling_rate: float,
    min_segment_samples: int = 50,
    penalty_factor: float = 1.5,
    max_levels: int = 5,
    min_level_diff_sigma: float = 1.0,
    noise_std: Optional[float] = None,
) -> Event:
    """Analyze an event for sub-level structure.

    Applies changepoint detection to the event region of the signal,
    identifies sub-levels, and updates the Event object accordingly.

    Parameters
    ----------
    event : Event
        A previously detected event to analyze for sub-levels.
    signal : np.ndarray
        The full (filtered) signal array.
    baseline : np.ndarray
        The estimated baseline array (same length as signal).
    sampling_rate : float
        Sampling rate in Hz.
    min_segment_samples : int
        Minimum samples per sub-level segment. Passed to the changepoint
        detector.
    penalty_factor : float
        BIC penalty multiplier for changepoint detection. Higher values
        produce fewer (more confident) splits.
    max_levels : int
        Maximum number of sub-levels to keep. If changepoints produce
        more segments, the smallest transitions are pruned.
    min_level_diff_sigma : float
        Minimum difference (in units of noise_std) between adjacent
        sub-level means for the split to be accepted. Merges adjacent
        sub-levels that are too similar.
    noise_std : float, optional
        Estimated noise standard deviation. If None, estimated from
        the event signal using MAD.

    Returns
    -------
    Event
        Updated event with sub-level information populated.
        The event_type is set to MULTI_LEVEL if more than one
        sub-level is found, otherwise SINGLE.
    """
    start = event.start_idx
    end = event.end_idx

    # Guard: event too short for meaningful analysis
    if (end - start) < 2 * min_segment_samples:
        return event

    event_signal = signal[start:end]
    event_baseline = baseline[start:end]

    # Estimate noise from event signal if not provided
    if noise_std is None:
        noise_std = float(np.median(np.abs(event_signal - np.median(event_signal))) * 1.4826)
    if noise_std < 1e-20:
        noise_std = 1e-20

    # --- Run changepoint detection on raw signal within the event ---
    changepoints = binary_segmentation_bic(
        event_signal,
        min_segment_samples=min_segment_samples,
        penalty_factor=penalty_factor,
    )

    if not changepoints:
        # No changepoints found — single-level event, nothing to update
        return event

    # --- Segment the signal at the changepoints ---
    segments = segment_signal(event_signal, changepoints)

    # --- Prune insignificant transitions ---
    # Merge adjacent segments whose mean difference is less than
    # min_level_diff_sigma * noise_std
    segments = _merge_similar_segments(
        event_signal, segments, min_level_diff_sigma * noise_std
    )

    # --- Enforce max_levels ---
    while len(segments) > max_levels:
        segments = _merge_smallest_transition(event_signal, segments)

    if len(segments) <= 1:
        # After merging, only one level — single-level event
        return event

    # --- Build SubLevel objects ---
    # Rank levels by mean current: level_index 0 = deepest blockade
    #   (lowest mean current for down events, highest for up events)
    # For generality, sort by mean_current ascending and assign indices.
    sorted_means = sorted(seg["mean"] for seg in segments)

    sublevels: list[SubLevel] = []
    for seg in segments:
        seg_start = start + seg["start"]
        seg_end = start + seg["end"]

        seg_baseline = baseline[seg_start:seg_end]
        mean_bl = float(np.mean(seg_baseline))

        # Level index: rank by mean current (ascending = deeper first)
        level_idx = sorted_means.index(seg["mean"])

        sublevel = SubLevel(
            start_idx=seg_start,
            end_idx=seg_end,
            start_time=seg_start / sampling_rate,
            end_time=seg_end / sampling_rate,
            duration=(seg_end - seg_start) / sampling_rate,
            mean_current=seg["mean"],
            std_current=seg["std"],
            level_index=level_idx,
        )
        sublevels.append(sublevel)

    # Sort sub-levels chronologically
    sublevels.sort(key=lambda sl: sl.start_idx)

    # --- Update the Event ---
    # Recompute overall event stats to be consistent
    event.n_levels = len(sublevels)
    event.sublevels = sublevels
    event.event_type = EventType.MULTI_LEVEL if len(sublevels) > 1 else EventType.SINGLE

    return event


def analyze_events_sublevels(
    events: list[Event],
    signal: np.ndarray,
    baseline: np.ndarray,
    sampling_rate: float,
    min_segment_samples: int = 50,
    penalty_factor: float = 1.5,
    max_levels: int = 5,
    min_level_diff_sigma: float = 1.0,
    noise_std: Optional[float] = None,
) -> list[Event]:
    """Analyze all events for sub-level structure.

    Convenience function that applies ``analyze_sublevels`` to each
    event in the list.

    Parameters
    ----------
    events : list[Event]
        List of detected events.
    signal : np.ndarray
        The full (filtered) signal array.
    baseline : np.ndarray
        The estimated baseline array.
    sampling_rate : float
        Sampling rate in Hz.
    min_segment_samples : int
        Minimum samples per sub-level segment.
    penalty_factor : float
        BIC penalty multiplier.
    max_levels : int
        Maximum number of sub-levels per event.
    min_level_diff_sigma : float
        Minimum mean difference between adjacent sub-levels
        (in noise_std units).
    noise_std : float, optional
        Noise standard deviation. If None, estimated per-event.

    Returns
    -------
    list[Event]
        Events with sub-level information updated.
    """
    return [
        analyze_sublevels(
            event=ev,
            signal=signal,
            baseline=baseline,
            sampling_rate=sampling_rate,
            min_segment_samples=min_segment_samples,
            penalty_factor=penalty_factor,
            max_levels=max_levels,
            min_level_diff_sigma=min_level_diff_sigma,
            noise_std=noise_std,
        )
        for ev in events
    ]


# ---- Internal helpers ----


def _merge_similar_segments(
    signal: np.ndarray,
    segments: list[dict],
    min_diff: float,
) -> list[dict]:
    """Merge adjacent segments whose means differ by less than min_diff.

    Parameters
    ----------
    signal : np.ndarray
        Signal used to recompute segment stats after merging.
    segments : list[dict]
        Segment dicts from ``segment_signal``.
    min_diff : float
        Minimum absolute mean difference to keep a transition.

    Returns
    -------
    list[dict]
        Merged segments.
    """
    if len(segments) <= 1:
        return segments

    merged = [segments[0]]
    for seg in segments[1:]:
        prev = merged[-1]
        if abs(seg["mean"] - prev["mean"]) < min_diff:
            # Merge: extend previous segment
            new_start = prev["start"]
            new_end = seg["end"]
            combined = signal[new_start:new_end]
            merged[-1] = {
                "start": new_start,
                "end": new_end,
                "mean": float(np.mean(combined)),
                "std": float(np.std(combined)),
                "n_samples": new_end - new_start,
            }
        else:
            merged.append(seg)

    return merged


def _merge_smallest_transition(
    signal: np.ndarray,
    segments: list[dict],
) -> list[dict]:
    """Merge the pair of adjacent segments with the smallest mean difference.

    Parameters
    ----------
    signal : np.ndarray
        Signal used to recompute stats after merging.
    segments : list[dict]
        Segment dicts.

    Returns
    -------
    list[dict]
        Segments with one fewer element.
    """
    if len(segments) <= 1:
        return segments

    # Find adjacent pair with smallest absolute mean difference
    min_diff = float("inf")
    min_idx = 0
    for i in range(len(segments) - 1):
        diff = abs(segments[i + 1]["mean"] - segments[i]["mean"])
        if diff < min_diff:
            min_diff = diff
            min_idx = i

    # Merge segments[min_idx] and segments[min_idx + 1]
    s1 = segments[min_idx]
    s2 = segments[min_idx + 1]
    new_start = s1["start"]
    new_end = s2["end"]
    combined = signal[new_start:new_end]

    merged_seg = {
        "start": new_start,
        "end": new_end,
        "mean": float(np.mean(combined)),
        "std": float(np.std(combined)),
        "n_samples": new_end - new_start,
    }

    result = segments[:min_idx] + [merged_seg] + segments[min_idx + 2:]
    return result
