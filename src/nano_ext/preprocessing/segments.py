"""Signal range cropping for artifact exclusion.

Allows restricting analysis to a specific time window (Stage 1) or
concatenating multiple keep-ranges while excluding artifact regions such
as those created by zapping operations (Stage 2).  All pipeline outputs
report event timestamps in original-file coordinates regardless of which
path is used.
"""

from __future__ import annotations

import numpy as np

from nano_ext.models import Event, SignalData


# ---------------------------------------------------------------------------
# Stage 1: single keep-range
# ---------------------------------------------------------------------------

def crop_signal_to_range(
    signal_data: SignalData,
    t_start: float,
    t_end: float,
) -> tuple[SignalData, int]:
    """Crop a SignalData to the specified time range.

    The returned ``SignalData.time`` reflects **original-file** time
    (starts at approximately *t_start*; implied by ``time_offset``), so that event timestamps reported
    by the pipeline are in original-file coordinates without further adjustment.

    Parameters
    ----------
    signal_data : SignalData
        Full recording to crop.
    t_start : float
        Start of the keep range in seconds (inclusive).
    t_end : float
        End of the keep range in seconds (exclusive).

    Returns
    -------
    cropped : SignalData
        Cropped signal with ``time`` in original-file coordinates.
    sample_offset : int
        Number of samples before *t_start* in the original recording.
        Pass this to :func:`restore_event_timestamps` to convert indices
        back to original-file coordinates after detection.

    Raises
    ------
    ValueError
        If *t_start* >= *t_end* or the range falls entirely outside the
        recording duration.
    """
    sr = signal_data.sampling_rate
    duration = signal_data.duration_sec

    if t_start >= t_end:
        raise ValueError(
            f"t_start ({t_start:.6f} s) must be strictly less than t_end ({t_end:.6f} s)"
        )
    if t_end <= 0 or t_start >= duration:
        raise ValueError(
            f"Range [{t_start:.3f}, {t_end:.3f}) s is entirely outside the recording "
            f"duration ({duration:.3f} s)"
        )

    i_start = max(0, int(np.floor(t_start * sr)))
    i_end = min(len(signal_data.signal), int(np.ceil(t_end * sr)))

    if i_start >= i_end:
        raise ValueError(
            f"Clamped sample range [{i_start}, {i_end}) is empty — "
            f"check t_start/t_end against recording duration ({duration:.3f} s)"
        )

    cropped_signal = signal_data.signal[i_start:i_end]

    cropped = SignalData(
        signal=cropped_signal,
        sampling_rate=sr,
        channel=signal_data.channel,
        units=signal_data.units,
        metadata=dict(signal_data.metadata),
        time_offset=signal_data.time_offset + i_start / sr,
    )

    return cropped, i_start


def restore_event_timestamps(
    events: list[Event],
    sample_offset: int,
    sampling_rate: float,
) -> list[Event]:
    """Shift event indices and times by *sample_offset* samples.

    Call this after running the pipeline on a cropped signal to convert
    all sample indices and timestamps back to original-file coordinates.

    Parameters
    ----------
    events : list[Event]
        Events detected on the cropped signal (indices relative to crop).
    sample_offset : int
        ``i_start`` returned by :func:`crop_signal_to_range`.
    sampling_rate : float
        Recording sampling rate in Hz.

    Returns
    -------
    list[Event]
        The same event objects with updated ``start_idx``, ``end_idx``,
        ``start_time``, and ``end_time`` (and the same for sub-levels).
    """
    if sample_offset == 0:
        return events

    time_offset = sample_offset / sampling_rate

    for ev in events:
        ev.start_idx += sample_offset
        ev.end_idx += sample_offset
        ev.start_time += time_offset
        ev.end_time += time_offset

        for sl in ev.sublevels:
            sl.start_idx += sample_offset
            sl.end_idx += sample_offset
            sl.start_time += time_offset
            sl.end_time += time_offset

    return events


# ---------------------------------------------------------------------------
# Stage 2: multiple ranges / exclude-ranges
# ---------------------------------------------------------------------------

def build_keep_ranges(
    duration_sec: float,
    exclude_ranges: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Convert a list of exclusion windows into a list of keep windows.

    Computes the complement of *exclude_ranges* within [0, *duration_sec*].
    Adjacent or overlapping exclusion ranges are merged before inversion.

    Parameters
    ----------
    duration_sec : float
        Total recording duration in seconds.
    exclude_ranges : list[tuple[float, float]]
        Regions to exclude, each as ``(t_start, t_end)`` in seconds.
        Ranges may overlap; they will be merged automatically.

    Returns
    -------
    list[tuple[float, float]]
        Keep windows, sorted by start time, non-overlapping.
        May be empty if the entire recording is excluded.
    """
    if not exclude_ranges:
        return [(0.0, duration_sec)]

    # Clamp and sort
    clamped = []
    for t0, t1 in exclude_ranges:
        t0 = max(0.0, min(t0, duration_sec))
        t1 = max(0.0, min(t1, duration_sec))
        if t0 < t1:
            clamped.append((t0, t1))
    if not clamped:
        return [(0.0, duration_sec)]

    # Merge overlapping / adjacent exclusions
    clamped.sort()
    merged: list[tuple[float, float]] = [clamped[0]]
    for t0, t1 in clamped[1:]:
        if t0 <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], t1))
        else:
            merged.append((t0, t1))

    # Invert
    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for ex_start, ex_end in merged:
        if cursor < ex_start:
            keep.append((cursor, ex_start))
        cursor = ex_end
    if cursor < duration_sec:
        keep.append((cursor, duration_sec))

    return keep


def concatenate_signal_ranges(
    signal_data: SignalData,
    keep_ranges: list[tuple[float, float]],
) -> tuple[SignalData, np.ndarray]:
    """Concatenate multiple time windows from a recording into one signal.

    The returned ``SignalData.time`` gives **original-file** timestamps for
    each concatenated sample (implied by ``SignalData.index_map``) (non-monotone jumps occur at range boundaries —
    use :func:`insert_nan_at_gaps` before plotting).

    Parameters
    ----------
    signal_data : SignalData
        Full recording.
    keep_ranges : list[tuple[float, float]]
        Windows to include, each as ``(t_start, t_end)`` in seconds.
        Should be non-overlapping and sorted; call :func:`build_keep_ranges`
        to compute them from exclusion intervals.

    Returns
    -------
    concatenated : SignalData
        Signal formed by concatenating the specified windows in order.
    idx_map : np.ndarray of int, shape (n_concatenated,)
        ``idx_map[i]`` is the original-file sample index corresponding to
        concatenated sample *i*.  Pass this to
        :func:`restore_event_timestamps_mapped` to convert events back to
        original-file coordinates.

    Raises
    ------
    ValueError
        If *keep_ranges* is empty or all ranges are outside the recording.
    """
    if not keep_ranges:
        raise ValueError("keep_ranges must not be empty")

    sr = signal_data.sampling_rate
    n_orig = len(signal_data.signal)
    duration = signal_data.duration_sec

    segments: list[np.ndarray] = []
    idx_maps: list[np.ndarray] = []

    for t0, t1 in keep_ranges:
        i_start = max(0, int(np.floor(t0 * sr)))
        i_end = min(n_orig, int(np.ceil(t1 * sr)))
        if i_start >= i_end:
            continue
        segments.append(signal_data.signal[i_start:i_end])
        idx_maps.append(np.arange(i_start, i_end, dtype=np.int64))

    if not segments:
        raise ValueError(
            f"All specified keep_ranges fall outside the recording "
            f"duration ({duration:.3f} s)"
        )

    concat_signal = np.concatenate(segments)
    idx_map = np.concatenate(idx_maps)

    concatenated = SignalData(
        signal=concat_signal,
        sampling_rate=sr,
        # Original-file times are implied by the index map (no time array).
        index_map=idx_map,
        channel=signal_data.channel,
        units=signal_data.units,
        metadata={**dict(signal_data.metadata), "_range_boundaries": _find_boundaries(idx_maps)},
    )

    return concatenated, idx_map


def _find_boundaries(idx_maps: list[np.ndarray]) -> list[int]:
    """Return concatenated-signal indices where range boundaries occur.

    These are the positions *after* the last sample of each range (except
    the final one), i.e. where a NaN separator should be inserted for
    display purposes.
    """
    boundaries: list[int] = []
    cursor = 0
    for arr in idx_maps[:-1]:
        cursor += len(arr)
        boundaries.append(cursor)
    return boundaries


def restore_event_timestamps_mapped(
    events: list[Event],
    idx_map: np.ndarray,
    sampling_rate: float,
) -> list[Event]:
    """Restore event timestamps using an index map from :func:`concatenate_signal_ranges`.

    Works for both single-range (where *idx_map* is an arithmetic sequence)
    and multi-range (where *idx_map* may have jumps) scenarios.

    Parameters
    ----------
    events : list[Event]
        Events with indices relative to the concatenated signal.
    idx_map : np.ndarray
        Array returned by :func:`concatenate_signal_ranges`.
    sampling_rate : float
        Recording sampling rate in Hz.

    Returns
    -------
    list[Event]
        Events with ``start_idx``, ``end_idx``, ``start_time``, and
        ``end_time`` (and sub-level equivalents) in original-file coordinates.
    """
    n = len(idx_map)
    sr = sampling_rate

    for ev in events:
        s = int(np.clip(ev.start_idx, 0, n - 1))
        e = int(np.clip(ev.end_idx - 1, 0, n - 1))
        ev.start_idx = int(idx_map[s])
        ev.end_idx = int(idx_map[e]) + 1
        ev.start_time = ev.start_idx / sr
        ev.end_time = ev.end_idx / sr

        for sl in ev.sublevels:
            ss = int(np.clip(sl.start_idx, 0, n - 1))
            se = int(np.clip(sl.end_idx - 1, 0, n - 1))
            sl.start_idx = int(idx_map[ss])
            sl.end_idx = int(idx_map[se]) + 1
            sl.start_time = sl.start_idx / sr
            sl.end_time = sl.end_idx / sr

    return events


def insert_nan_at_gaps(
    arr: np.ndarray,
    boundaries: list[int],
) -> np.ndarray:
    """Insert NaN values at range boundaries for display purposes.

    Plotly and matplotlib will not connect line segments across NaN,
    correctly showing the gaps between excluded artifact regions.

    Parameters
    ----------
    arr : np.ndarray
        Signal, baseline, or threshold array aligned to the concatenated
        signal.
    boundaries : list[int]
        Boundary positions from ``signal_data.metadata['_range_boundaries']``.

    Returns
    -------
    np.ndarray
        Float64 array with NaN inserted at each boundary index.
    """
    if not boundaries:
        return arr.astype(np.float64)
    return np.insert(arr.astype(np.float64), boundaries, np.nan)
