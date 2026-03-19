"""Data models for nanopore event detection.

This module defines the core data structures used throughout the nanopore event
detection pipeline. These include classes for representing detected events,
their sub-levels, signal data, thresholding results, baseline estimates, and
detection configuration.

The models use Python dataclasses for clean, efficient data storage with
automatic generation of __init__, __repr__, and other special methods.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np


class EventDirection(str, Enum):
    """Direction of current change to detect as events.
    
    Attributes
    ----------
    DOWN : str
        Current decrease (blockade event)
    UP : str
        Current increase (anti-blockade or spike event)
    BOTH : str
        Both directions (detect blockades and spikes)
    """

    DOWN = "down"  # Current decrease (blockade)
    UP = "up"  # Current increase
    BOTH = "both"  # Both directions


class EventType(str, Enum):
    """Classification of detected events based on their temporal structure.
    
    Attributes
    ----------
    SINGLE : str
        Single-level blockade with constant current level
    MULTI_LEVEL : str
        Multi-level (stepwise) event with distinct current levels
    SPIKE : str
        Very short transient spike (typically < 1ms duration)
    """

    SINGLE = "single"  # Single-level blockade
    MULTI_LEVEL = "multi_level"  # Multi-level (stepwise) event
    SPIKE = "spike"  # Very short transient spike


@dataclass
class SubLevel:
    """A single sub-level within a multi-level nanopore event.
    
    Represents a segment of constant current level within a stepwise event.
    Multi-level events are composed of two or more sub-levels with different
    current amplitudes.

    Attributes
    ----------
    start_idx : int
        Start index in the signal array (inclusive).
    end_idx : int
        End index in the signal array (exclusive).
    start_time : float
        Start time in seconds.
    end_time : float
        End time in seconds.
    duration : float
        Duration in seconds (end_time - start_time).
    mean_current : float
        Mean current value during this sub-level (in raw units, e.g., pA).
    std_current : float
        Standard deviation of current during this sub-level.
    level_index : int
        Level number indicating the hierarchical depth (0 = deepest blockade,
        with increasing values representing shallower levels or returns toward
        baseline).
    """

    start_idx: int
    end_idx: int
    start_time: float
    end_time: float
    duration: float
    mean_current: float
    std_current: float
    level_index: int = 0


@dataclass
class Event:
    """A detected nanopore event representing a significant deviation from baseline current.
    
    In nanopore sensing, events typically correspond to transient blockades where
    molecules passing through the nanopore obstruct ionic flow, causing a drop in
    current. Events can be simple blockades (single-level) or complex stepwise
    transitions (multi-level) as molecules interact with the nanopore in different
    conformations or orientations.

    Attributes
    ----------
    start_idx : int
        Start index in the signal array (inclusive).
    end_idx : int
        End index in the signal array (exclusive).
    start_time : float
        Start time in seconds.
    end_time : float
        End time in seconds.
    duration : float
        Duration in seconds (end_time - start_time).
    mean_current : float
        Mean current during the event (baseline-corrected residual, in raw units).
    std_current : float
        Standard deviation of current during the event.
    baseline_current : float
        Local baseline current at the event location (in raw units, e.g., pA).
    depth : float
        Blockade depth (absolute value of current change from baseline, positive value).
    relative_depth : float
        Relative blockade depth (depth / baseline_current, dimensionless).
    area : float
        Event area (integral of current deviation from baseline, in units*seconds).
    n_levels : int
        Number of sub-levels detected within this event (1 for single-level events).
    sublevels : list[SubLevel]
        List of sub-levels (empty for single-level events).
    event_type : EventType
        Classification of the event (SINGLE, MULTI_LEVEL, or SPIKE).
    """

    start_idx: int
    end_idx: int
    start_time: float
    end_time: float
    duration: float
    mean_current: float
    std_current: float
    baseline_current: float
    depth: float
    relative_depth: float
    area: float
    n_levels: int = 1
    sublevels: list[SubLevel] = field(default_factory=list)
    event_type: EventType = EventType.SINGLE

    @property
    def is_multilevel(self) -> bool:
        """Return True if the event has multiple sub-levels."""
        return self.n_levels > 1


@dataclass
class SignalData:
    """Container for loaded nanopore signal data.

    Attributes
    ----------
    signal : np.ndarray
        Raw current signal (1-D array).
    sampling_rate : float
        Sampling rate in Hz.
    time : np.ndarray
        Time array in seconds (computed from sampling rate).
    channel : int
        Channel number (for multi-channel recordings).
    units : str
        Current units (e.g., 'pA', 'nA').
    metadata : dict
        Additional metadata from the file.
    """

    signal: np.ndarray
    sampling_rate: float
    time: Optional[np.ndarray] = None
    channel: int = 0
    units: str = "pA"
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.time is None:
            n_samples = len(self.signal)
            self.time = np.arange(n_samples) / self.sampling_rate

    @property
    def duration_sec(self) -> float:
        """Total duration of the recording in seconds."""
        return len(self.signal) / self.sampling_rate

    @property
    def n_samples(self) -> int:
        """Total number of samples."""
        return len(self.signal)


@dataclass
class ThresholdResult:
    """Result of BIC-based threshold determination.

    Attributes
    ----------
    threshold : float
        The determined threshold value.
    n_components : int
        Optimal number of GMM components (by BIC).
    component_means : np.ndarray
        Mean of each GMM component.
    component_stds : np.ndarray
        Standard deviation of each GMM component.
    component_weights : np.ndarray
        Weight of each GMM component.
    bic_scores : list[float]
        BIC score for each tested number of components.
    baseline_component_idx : int
        Index of the component identified as the baseline.
    """

    threshold: float
    n_components: int
    component_means: np.ndarray
    component_stds: np.ndarray
    component_weights: np.ndarray
    bic_scores: list[float]
    baseline_component_idx: int


@dataclass
class BaselineResult:
    """Result of baseline estimation.

    Attributes
    ----------
    local_baseline : np.ndarray
        Estimated local baseline (same length as signal).
    trend : np.ndarray
        Estimated global trend component.
    residual : np.ndarray
        Baseline-corrected signal (signal - local_baseline).
    noise_std : float
        Estimated noise standard deviation (MAD-based).
    """

    local_baseline: np.ndarray
    trend: np.ndarray
    residual: np.ndarray
    noise_std: float


@dataclass
class DetectionConfig:
    """Configuration for the event detection pipeline.

    All parameters with sensible defaults; most can be auto-determined
    from the data.
    """

    # --- Filtering ---
    apply_filter: bool = True
    pre_applied_filter_cutoff: Optional[float] = None  # Hz; Used if apply_filter is False
    filter_type: str = "bessel"  # "bessel" or "butterworth"
    filter_order: int = 4
    filter_cutoff: Optional[float] = None  # Hz; None = auto (fs / 10)

    # --- Baseline estimation ---
    detrend_method: str = "polynomial"  # "polynomial", "linear", "spline"
    detrend_order: int = 3
    baseline_window_sec: float = 5.0  # Window for local baseline [s]
    baseline_iterations: int = 3
    baseline_percentile: float = 90.0
    baseline_n_sigma: float = 5.0  # Sigma threshold for iterative exclusion

    # --- Threshold (GMM + BIC) ---
    gmm_max_components: int = 10
    bic_criterion: str = "bic"  # "bic", "aic"

    # --- Event detection ---
    event_direction: EventDirection = EventDirection.DOWN
    min_event_duration_sec: Optional[float] = None  # None = auto
    merge_gap_sec: Optional[float] = None  # None = auto

    # --- Sublevel analysis ---
    changepoint_method: str = "binary_seg_bic"
    min_segment_samples: int = 50
    max_sublevel_depth: int = 5

    # --- Noise estimation ---
    noise_estimation: str = "mad"  # "mad" or "std"
