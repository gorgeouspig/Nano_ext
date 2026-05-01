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

from ._nano_ext import pelt, local_baseline_percentile

__version__ = "0.1.0"

__all__ = [
    "EventDirection",
    "EventType",
    "SubLevel",
    "Event",
    "SignalData",
    "ThresholdResult",
    "BaselineResult",
    "DetectionConfig",
    "PipelineResult",
    "run_pipeline",
    "process_file",
    "write_events_to_csv",
    "plot_pipeline_result",
    "pelt",
    "local_baseline_percentile",
]
