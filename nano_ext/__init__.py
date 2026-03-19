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
from .output.csv_writer import write_events_to_csv
from .output.visualize import plot_pipeline_result

import nano_ext_core

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
    "nano_ext_core",
]
