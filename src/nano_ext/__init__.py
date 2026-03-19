"""Nano_ext — Brand New Nanopore Signal Extraction.

Event detection and analysis from nanopore current traces.
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

from ._nano_ext import pelt, local_baseline_percentile, _nano_ext

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
    "_nano_ext",
]
