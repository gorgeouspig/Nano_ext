"""Event detection from nanopore current traces.

Threshold-based event detection with run-length encoding,
minimum duration filtering, and nearby event merging.
Also computes event statistics (depth, duration, area, etc.).
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from nano_ext.models import Event, EventDirection, EventType


def detect_events(
    residual: np.ndarray,
    sampling_rate: float,
    threshold: float,
    baseline: np.ndarray,
    direction: EventDirection = EventDirection.DOWN,
    min_event_duration_sec: Optional[float] = None,
    merge_gap_sec: Optional[float] = None,
    filter_cutoff: Optional[float] = None,
) -> list[Event]:
    """Detect events in a baseline-corrected signal.

    Parameters
    ----------
    residual : np.ndarray
        Baseline-corrected signal (baseline ≈ 0, events deviate from 0).
    sampling_rate : float
        Sampling rate in Hz.
    threshold : float
        Threshold for event detection (from BIC-based determination).
        For downward events, the threshold should be negative.
    baseline : np.ndarray
        Estimated absolute baseline (same length as residual), used
        for computing absolute current values in the output.
    direction : EventDirection
        Direction of events to detect:
        - DOWN: detect negative deviations (blockades)
        - UP: detect positive deviations
        - BOTH: detect both directions
    min_event_duration_sec : float, optional
        Minimum event duration in seconds. Events shorter than this
        are discarded as noise. If None, auto-determined from
        filter_cutoff or sampling_rate.
    merge_gap_sec : float, optional
        Maximum gap between events to merge them. If None,
        auto-determined from filter_cutoff or sampling_rate.
    filter_cutoff : float, optional
        Low-pass filter cutoff frequency (Hz). Used for auto-determining
        min_event_duration and merge_gap if not specified.

    Returns
    -------
    list[Event]
        List of detected events with computed statistics.
    """
    n_samples = len(residual)

    # Auto-determine parameters
    if filter_cutoff is None:
        filter_cutoff = sampling_rate / 10.0

    if min_event_duration_sec is None:
        # Minimum resolvable event ≈ 5 / filter_cutoff
        min_event_duration_sec = 5.0 / filter_cutoff

    if merge_gap_sec is None:
        # Merge gap ≈ 3 / filter_cutoff
        merge_gap_sec = 3.0 / filter_cutoff

    min_samples = max(1, int(min_event_duration_sec * sampling_rate))
    merge_gap_samples = max(0, int(merge_gap_sec * sampling_rate))

    # --- Create event mask based on direction ---
    if direction == EventDirection.DOWN:
        event_mask = residual < threshold
    elif direction == EventDirection.UP:
        event_mask = residual > abs(threshold)
    elif direction == EventDirection.BOTH:
        event_mask = np.abs(residual) > abs(threshold)
    else:
        raise ValueError(f"Unknown direction: {direction}")

    # --- Extract contiguous runs (run-length encoding) ---
    raw_intervals = _find_runs(event_mask)

    if len(raw_intervals) == 0:
        return []

    # --- Merge nearby events ---
    merged = _merge_intervals(raw_intervals, merge_gap_samples)

    # --- Filter by minimum duration ---
    filtered_intervals = [
        (start, end) for start, end in merged
        if (end - start) >= min_samples
    ]

    # --- Compute statistics for each event ---
    events = []
    for start_idx, end_idx in filtered_intervals:
        event = _compute_event_stats(
            residual, baseline, sampling_rate, start_idx, end_idx
        )
        events.append(event)

    return events


def _find_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Find contiguous True runs in a boolean array.

    Parameters
    ----------
    mask : np.ndarray
        Boolean array.

    Returns
    -------
    list[tuple[int, int]]
        List of (start_idx, end_idx) pairs. end_idx is exclusive.
    """
    if len(mask) == 0:
        return []

    # Find transitions
    diff = np.diff(mask.astype(np.int8))
    starts = np.where(diff == 1)[0] + 1
    ends = np.where(diff == -1)[0] + 1

    # Handle edge cases
    if mask[0]:
        starts = np.concatenate([[0], starts])
    if mask[-1]:
        ends = np.concatenate([ends, [len(mask)]])

    return list(zip(starts.tolist(), ends.tolist()))


def _merge_intervals(
    intervals: list[tuple[int, int]],
    max_gap: int,
) -> list[tuple[int, int]]:
    """Merge intervals that are closer than max_gap samples.

    Parameters
    ----------
    intervals : list[tuple[int, int]]
        Sorted list of (start, end) intervals.
    max_gap : int
        Maximum gap in samples between intervals to merge.

    Returns
    -------
    list[tuple[int, int]]
        Merged intervals.
    """
    if len(intervals) == 0:
        return []

    merged = [intervals[0]]
    for start, end in intervals[1:]:
        prev_start, prev_end = merged[-1]
        if start - prev_end <= max_gap:
            # Merge with previous
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))

    return merged


def _compute_event_stats(
    residual: np.ndarray,
    baseline: np.ndarray,
    sampling_rate: float,
    start_idx: int,
    end_idx: int,
) -> Event:
    """Compute statistics for a single event.

    Parameters
    ----------
    residual : np.ndarray
        Baseline-corrected signal.
    baseline : np.ndarray
        Absolute baseline signal.
    sampling_rate : float
        Sampling rate in Hz.
    start_idx : int
        Start index of the event.
    end_idx : int
        End index of the event (exclusive).

    Returns
    -------
    Event
        Event with computed statistics.
    """
    event_residual = residual[start_idx:end_idx]
    event_baseline = baseline[start_idx:end_idx]

    start_time = start_idx / sampling_rate
    end_time = end_idx / sampling_rate
    duration = end_time - start_time

    mean_residual = float(np.mean(event_residual))
    std_current = float(np.std(event_residual))

    # Absolute current during event
    mean_current = float(np.mean(event_baseline + event_residual))

    # Baseline current (average of local baseline during event)
    baseline_current = float(np.mean(event_baseline))

    # Depth = how far current dropped from baseline (positive value)
    depth = abs(mean_residual)

    # Relative depth
    if abs(baseline_current) > 1e-10:
        relative_depth = depth / abs(baseline_current)
    else:
        relative_depth = 0.0

    # Area = integral of deviation from baseline (in pA * seconds)
    area = float(np.sum(np.abs(event_residual))) / sampling_rate

    return Event(
        start_idx=start_idx,
        end_idx=end_idx,
        start_time=start_time,
        end_time=end_time,
        duration=duration,
        mean_current=mean_current,
        std_current=std_current,
        baseline_current=baseline_current,
        depth=depth,
        relative_depth=relative_depth,
        area=area,
        n_levels=1,
        sublevels=[],
        event_type=EventType.SINGLE,
    )
