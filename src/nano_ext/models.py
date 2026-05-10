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
class HMMResult:
    """Result of Gaussian HMM fitting to a single event.

    Produced by :func:`~nano_ext.detection.hmm.fit_hmm_event` when
    ``DetectionConfig.hmm_analysis`` is True.

    Attributes
    ----------
    n_states : int
        Number of hidden states selected by BIC.
    state_means : np.ndarray
        Mean current of each state.
    state_stds : np.ndarray
        Standard deviation of each state.
    transition_matrix : np.ndarray
        k × k row-stochastic transition probability matrix.
    state_sequence : np.ndarray
        Viterbi-decoded state index for every sample in the event.
    dwell_times_s : list of np.ndarray
        Per-state arrays of consecutive dwell durations (seconds).
    log_likelihood : float
        Total log-likelihood of the fitted model.
    bic : float
        BIC score (lower is better).
    """

    n_states: int
    state_means: np.ndarray
    state_stds: np.ndarray
    transition_matrix: np.ndarray
    state_sequence: np.ndarray
    dwell_times_s: list
    log_likelihood: float
    bic: float


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
    direction : EventDirection
        Direction of the current change: DOWN (blockade) or UP (anti-blockade /
        current enhancement). Always DOWN unless detected with EventDirection.BOTH.
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
    direction: EventDirection = EventDirection.DOWN
    hmm_result: Optional[HMMResult] = None

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

    All parameters have sensible defaults; most can be left at their
    default values for standard nanopore blockade experiments.

    Attributes
    ----------
    apply_filter : bool
        Whether to apply a low-pass filter before processing.  Set to
        False if the signal has already been hardware-filtered.
    pre_applied_filter_cutoff : float, optional
        Effective cutoff frequency (Hz) when *apply_filter* is False.
        Used only to set auto-detection thresholds; ignored otherwise.
    filter_type : str
        Filter design: ``"bessel"`` (recommended — maximally flat group
        delay, minimal event-shape distortion) or ``"butterworth"``
        (sharper roll-off but more phase distortion).
    filter_order : int
        Filter order.  Higher orders give sharper roll-off at the cost of
        more transient ringing.  Default 4 is standard for nanopore data.
    filter_cutoff : float, optional
        Low-pass cutoff in Hz.  ``None`` uses ``sampling_rate / 10``
        automatically.
    detrend_method : str
        Global baseline detrending applied before local estimation.
        One of ``"none"`` (skip), ``"linear"``, ``"polynomial"``,
        ``"spline"``.  Use ``"none"`` unless the baseline drifts strongly.
    detrend_order : int
        Polynomial order for ``detrend_method="polynomial"``.
    baseline_window_sec : float
        Sliding-window size in seconds for local baseline estimation.
        Should be several times the typical inter-event interval.
    baseline_iterations : int
        Number of iterative refinement passes for baseline estimation.
        More iterations improve exclusion of events from the estimate.
    baseline_percentile : float
        Percentile (0–100) used within each window to anchor the baseline.
        90 works well for downward blockades (excludes the lower tail).
    baseline_n_sigma : float
        Points farther than this many noise standard deviations from the
        current baseline estimate are masked as events during iteration.
    gmm_max_components : int
        Maximum number of Gaussian mixture components to test when
        determining the detection threshold.
    bic_criterion : str
        Information criterion for GMM model selection: ``"bic"``
        (preferred, penalises complexity more) or ``"aic"``.
    event_direction : EventDirection
        Which current deviations to treat as events: ``DOWN`` (blockades),
        ``UP`` (spikes / anti-blockades), or ``BOTH``.
    min_event_duration_sec : float, optional
        Minimum event duration in seconds.  Events shorter than this are
        discarded as noise.  ``None`` sets the threshold automatically
        based on the filter rise time (~5 / filter_cutoff).
    merge_gap_sec : float, optional
        Maximum gap between adjacent events to merge them into one.
        ``None`` sets the gap automatically (~1.5 / filter_cutoff).
    min_segment_samples : int
        Minimum number of samples required for a resolvable sub-level.
        Related to the filter rise time; the default of 50 is suitable
        for 100 kHz / 10 kHz cut-off recordings.
    max_sublevel_depth : int
        Maximum number of sub-level components (GMM) to test per event.
    noise_estimation : str
        Method for estimating noise standard deviation: ``"mad"`` (Median
        Absolute Deviation — robust to outliers, recommended) or
        ``"std"`` (standard deviation).
    """

    # --- Filtering ---
    apply_filter: bool = True
    pre_applied_filter_cutoff: Optional[float] = None
    filter_type: str = "bessel"
    filter_order: int = 4
    filter_cutoff: Optional[float] = None

    # --- Baseline estimation ---
    detrend_method: str = "none"
    detrend_order: int = 3
    baseline_window_sec: float = 5.0
    baseline_iterations: int = 3
    baseline_percentile: float = 90.0
    baseline_n_sigma: float = 5.0

    # --- Threshold (GMM + BIC) ---
    gmm_max_components: int = 10
    bic_criterion: str = "bic"
    gmm_max_samples: int = 100_000

    # --- Event detection ---
    event_direction: EventDirection = EventDirection.DOWN
    min_event_duration_sec: Optional[float] = None
    merge_gap_sec: Optional[float] = None

    # --- Sublevel analysis ---
    min_segment_samples: int = 50
    max_sublevel_depth: int = 5

    # --- HMM analysis ---
    hmm_analysis: bool = False
    hmm_max_states: int = 5

    # --- Noise estimation ---
    noise_estimation: str = "mad"
