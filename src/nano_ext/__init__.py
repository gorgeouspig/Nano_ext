"""Nano_ext — Nanopore Signal Event Extraction.

Provides an objective, information-criterion-based toolkit for detecting and
analysing ionic current blockade events in high-speed (>100 kHz) nanopore
traces.

Public API
----------
Data loading:
    read_abf, read_binary  (nano_ext.io)

Pipeline entry points:
    run_pipeline(signal_data, config) -> PipelineResult
    process_file(filepath, config)    -> PipelineResult

Key data classes:
    SignalData, DetectionConfig, PipelineResult, Event, SubLevel

Output helpers:
    write_events_to_csv, write_sublevels_to_csv
    plot_pipeline_result

Rust-accelerated primitives (advanced use):
    pelt, local_baseline_percentile
"""

from .models import (
    EventDirection,
    EventType,
    SubLevel,
    Event,
    SignalData,
    ThresholdResult,
    BaselineResult,
    DetectionConfig,
)

from .pipeline import run_pipeline, process_file, PipelineResult
from .outputs.csv_writer import write_events_to_csv
from .outputs.visualize import plot_pipeline_result
from .detection.autotune import suggest_config, estimate_noise_floor
from .preprocessing.control import ControlStats, compute_control_stats
from .preprocessing.segments import (
    crop_signal_to_range,
    restore_event_timestamps,
    build_keep_ranges,
    concatenate_signal_ranges,
    restore_event_timestamps_mapped,
    insert_nan_at_gaps,
)

from ._nano_ext import pelt, local_baseline_percentile

__version__ = "0.1.0"

__all__ = [
    # Data models
    "EventDirection",
    "EventType",
    "SubLevel",
    "Event",
    "SignalData",
    "ThresholdResult",
    "BaselineResult",
    "DetectionConfig",
    # Pipeline
    "PipelineResult",
    "run_pipeline",
    "process_file",
    # Auto-tuning
    "suggest_config",
    "estimate_noise_floor",
    # Negative control
    "ControlStats",
    "compute_control_stats",
    # Analysis range / artifact exclusion
    "crop_signal_to_range",
    "restore_event_timestamps",
    "build_keep_ranges",
    "concatenate_signal_ranges",
    "restore_event_timestamps_mapped",
    "insert_nan_at_gaps",
    # Output helpers
    "write_events_to_csv",
    "plot_pipeline_result",
    # Rust primitives (advanced use)
    "pelt",
    "local_baseline_percentile",
]
