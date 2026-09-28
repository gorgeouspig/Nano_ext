"""CSV export for nanopore event detection results.

Provides two writers:
- :func:`write_events_to_csv` — one row per event (main statistics).
- :func:`write_sublevels_to_csv` — one row per sub-level (for multi-level events).

Both files share an ``event_id`` column so they can be joined in downstream
analysis (e.g. ``pd.merge(events_df, sublevels_df, on="event_id")``).
"""

import numpy as np
import pandas as pd
from typing import List, Optional
from nano_ext.models import Event


def write_events_to_csv(events: List[Event], path: str, sampling_rate: float) -> None:
    """Save detected events to a CSV file (one row per event).

    Parameters
    ----------
    events : list[Event]
        Events to export (typically ``PipelineResult.events``).
    path : str
        Destination file path.  The file is created or overwritten.
    sampling_rate : float
        Sampling rate in Hz of the source signal.  Stored in file metadata
        for reference but not used for any calculation in this function.

    Notes
    -----
    Columns written: ``event_id``, ``start_idx``, ``end_idx``,
    ``start_time``, ``end_time``, ``duration``, ``mean_current``,
    ``std_current``, ``baseline_current``, ``depth``,
    ``relative_depth``, ``area``, ``n_levels``, ``event_type``,
    ``direction``; plus ``cluster_id`` and ``cluster_prob`` when the events
    were clustered (see :func:`nano_ext.analysis.clustering.cluster_events`).
    """
    clustered = any(ev.cluster_id is not None for ev in events)
    data = []
    for ev_idx, ev in enumerate(events):
        row = {
            "event_id": ev_idx,
            "start_idx": ev.start_idx,
            "end_idx": ev.end_idx,
            "start_time": ev.start_time,
            "end_time": ev.end_time,
            "duration": ev.duration,
            "mean_current": ev.mean_current,
            "std_current": ev.std_current,
            "baseline_current": ev.baseline_current,
            "depth": ev.depth,
            "relative_depth": ev.relative_depth,
            "area": ev.area,
            "n_levels": ev.n_levels,
            "event_type": ev.event_type.value,
            "direction": ev.direction.value,
        }
        if clustered:
            row["cluster_id"] = ev.cluster_id
            row["cluster_prob"] = ev.cluster_prob
        data.append(row)
    pd.DataFrame(data).to_csv(path, index=False)


def write_sublevels_to_csv(events: List[Event], path: str, sampling_rate: float) -> None:
    """Save sub-level details to a CSV file (one row per sub-level).

    Only multi-level events contribute rows; single-level events are skipped.
    Each row is keyed by ``event_id`` so the output can be joined with the
    file produced by :func:`write_events_to_csv`.

    Parameters
    ----------
    events : list[Event]
        Events to export (typically ``PipelineResult.events``).
    path : str
        Destination file path.  The file is created or overwritten.
    sampling_rate : float
        Sampling rate in Hz of the source signal.  Stored for reference
        but not used for any calculation in this function.

    Notes
    -----
    Columns written: ``event_id``, ``sublevel_idx``, ``level_index``,
    ``start_idx``, ``end_idx``, ``start_time``, ``end_time``,
    ``duration``, ``mean_current``, ``std_current``,
    ``depth_from_baseline``, ``relative_depth_from_baseline``.

    ``depth_from_baseline`` and ``relative_depth_from_baseline`` express how
    far each sub-level's mean current deviates from the event's local baseline.
    """
    data = []
    for ev_idx, ev in enumerate(events):
        for sl_idx, sl in enumerate(ev.sublevels):
            depth = ev.baseline_current - sl.mean_current
            rel_depth = (
                depth / abs(ev.baseline_current)
                if abs(ev.baseline_current) > 1e-10
                else np.nan
            )
            data.append({
                "event_id": ev_idx,
                "sublevel_idx": sl_idx,
                "level_index": sl.level_index,
                "start_idx": sl.start_idx,
                "end_idx": sl.end_idx,
                "start_time": sl.start_time,
                "end_time": sl.end_time,
                "duration": sl.duration,
                "mean_current": sl.mean_current,
                "std_current": sl.std_current,
                "depth_from_baseline": depth,
                "relative_depth_from_baseline": rel_depth,
            })
    pd.DataFrame(data).to_csv(path, index=False)
