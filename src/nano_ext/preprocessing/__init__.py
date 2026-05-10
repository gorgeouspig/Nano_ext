"""Signal preprocessing modules (filtering, baseline estimation, range selection)."""

from nano_ext.preprocessing.filters import lowpass_filter
from nano_ext.preprocessing.baseline import estimate_baseline
from nano_ext.preprocessing.segments import (
    crop_signal_to_range,
    restore_event_timestamps,
    build_keep_ranges,
    concatenate_signal_ranges,
    restore_event_timestamps_mapped,
    insert_nan_at_gaps,
)

__all__ = [
    "lowpass_filter",
    "estimate_baseline",
    "crop_signal_to_range",
    "restore_event_timestamps",
    "build_keep_ranges",
    "concatenate_signal_ranges",
    "restore_event_timestamps_mapped",
    "insert_nan_at_gaps",
]
