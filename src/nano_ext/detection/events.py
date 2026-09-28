"""Event detection from nanopore current traces.

Threshold-based event detection with run-length encoding,
minimum duration filtering, and nearby event merging.
Also computes event statistics (depth, duration, area, etc.).

For EventDirection.BOTH the function runs DOWN and UP detection
independently with symmetric thresholds and returns a single list
sorted by start_idx, with each event tagged by its direction.
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
    gmm_means: Optional[np.ndarray] = None,
    gmm_stds: Optional[np.ndarray] = None,
    gmm_weights: Optional[np.ndarray] = None,
    baseline_component_idx: Optional[int] = None,
) -> list[Event]:
    """Detect events in a baseline-corrected signal.

    Parameters
    ----------
    residual : np.ndarray
        Baseline-corrected signal (baseline ≈ 0, events deviate from 0).
    sampling_rate : float
        Sampling rate in Hz.
    threshold : float
        Threshold from BIC-based determination.  For downward events this
        value is negative; for upward events the absolute value is used.
    baseline : np.ndarray
        Estimated absolute baseline (same length as residual), used
        for computing absolute current values in the output.
    direction : EventDirection
        Which current deviations to detect:
        - DOWN: negative deviations (blockades)
        - UP: positive deviations (anti-blockades / current spikes)
        - BOTH: both directions; DOWN and UP events are detected
          independently with symmetric thresholds and merged into a
          single time-sorted list, each tagged with its direction.
    min_event_duration_sec : float, optional
        Minimum event duration in seconds.  If None, auto-determined from
        filter_cutoff or sampling_rate.
    merge_gap_sec : float, optional
        Maximum gap between adjacent events to merge.  If None,
        auto-determined from filter_cutoff or sampling_rate.
    filter_cutoff : float, optional
        Low-pass filter cutoff frequency (Hz).  Used for auto-determining
        min_event_duration and merge_gap.
    gmm_means : np.ndarray, optional
        Component means from GMM threshold determination.
    gmm_stds : np.ndarray, optional
        Component standard deviations from GMM threshold determination.
    gmm_weights : np.ndarray, optional
        Component weights from GMM threshold determination.
    baseline_component_idx : int, optional
        Index of the baseline component in the GMM.

    Returns
    -------
    list[Event]
        Detected events sorted by start_idx.  Each event has its
        ``direction`` field set to DOWN or UP.
    """
    if filter_cutoff is None:
        filter_cutoff = sampling_rate / 10.0

    if min_event_duration_sec is None:
        min_event_duration_sec = 5.0 / filter_cutoff

    if merge_gap_sec is None:
        merge_gap_sec = 1.5 / filter_cutoff

    min_samples = max(1, int(min_event_duration_sec * sampling_rate))
    merge_gap_samples = max(0, int(merge_gap_sec * sampling_rate))

    # GMM-derived open-pore statistics (used for conservative thresholding)
    gmm_params_ok = (
        gmm_means is not None
        and gmm_stds is not None
        and gmm_weights is not None
        and baseline_component_idx is not None
    )
    if gmm_params_ok:
        open_pore_mean = gmm_means[baseline_component_idx]
        open_pore_std = gmm_stds[baseline_component_idx]
    else:
        open_pore_mean = 0.0
        open_pore_std = 0.0

    def _down_threshold() -> float:
        if gmm_params_ok:
            return min(threshold, open_pore_mean - 5.0 * open_pore_std)
        return threshold

    def _up_threshold() -> float:
        if gmm_params_ok:
            return max(abs(threshold), open_pore_mean + 5.0 * open_pore_std)
        return abs(threshold)

    if direction == EventDirection.BOTH:
        down_events = _detect_one_direction(
            residual, baseline, sampling_rate,
            _down_threshold(), EventDirection.DOWN,
            open_pore_mean, open_pore_std, gmm_params_ok,
            min_samples, merge_gap_samples,
        )
        up_events = _detect_one_direction(
            residual, baseline, sampling_rate,
            _up_threshold(), EventDirection.UP,
            open_pore_mean, open_pore_std, gmm_params_ok,
            min_samples, merge_gap_samples,
        )
        return sorted(down_events + up_events, key=lambda e: e.start_idx)

    eff_threshold = _down_threshold() if direction == EventDirection.DOWN else _up_threshold()
    return _detect_one_direction(
        residual, baseline, sampling_rate,
        eff_threshold, direction,
        open_pore_mean, open_pore_std, gmm_params_ok,
        min_samples, merge_gap_samples,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _detect_one_direction(
    residual: np.ndarray,
    baseline: np.ndarray,
    sampling_rate: float,
    threshold: float,
    direction: EventDirection,
    open_pore_mean: float,
    open_pore_std: float,
    use_gmm: bool,
    min_samples: int,
    merge_gap_samples: int,
) -> list[Event]:
    """Run detection for a single direction and return tagged events."""
    if direction == EventDirection.DOWN:
        event_mask = residual < threshold
    else:
        event_mask = residual > threshold

    if use_gmm and open_pore_std > 0:
        # Exclude samples within 1σ of the open-pore mean from being flagged.
        # Only samples already past the threshold can be affected, so test
        # just those instead of building full-length float temporaries.
        idx = np.flatnonzero(event_mask)
        near_open_pore = np.abs(residual[idx] - open_pore_mean) < open_pore_std
        event_mask[idx[near_open_pore]] = False

    raw_intervals = _find_runs(event_mask)
    if not raw_intervals:
        return []

    merged = _merge_intervals(raw_intervals, merge_gap_samples)
    filtered = [(s, e) for s, e in merged if (e - s) >= min_samples]

    return [
        _compute_event_stats(residual, baseline, sampling_rate, s, e, direction)
        for s, e in filtered
    ]


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

    diff = np.diff(mask.astype(np.int8))
    starts = np.where(diff == 1)[0] + 1
    ends = np.where(diff == -1)[0] + 1

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
    direction: EventDirection = EventDirection.DOWN,
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
    direction : EventDirection
        Direction of the event (DOWN or UP).

    Returns
    -------
    Event
        Event with computed statistics and direction tag.
    """
    event_residual = residual[start_idx:end_idx]
    event_baseline = baseline[start_idx:end_idx]

    start_time = start_idx / sampling_rate
    end_time = end_idx / sampling_rate
    duration = end_time - start_time

    mean_residual = float(np.mean(event_residual))
    std_current = float(np.std(event_residual))
    mean_current = float(np.mean(event_baseline + event_residual))
    baseline_current = float(np.mean(event_baseline))

    depth = abs(mean_residual)

    if abs(baseline_current) > 1e-10:
        relative_depth = depth / abs(baseline_current)
    else:
        relative_depth = 0.0

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
        direction=direction,
    )
