"""End-to-end nanopore event detection pipeline.

Orchestrates the full processing chain:
    load → filter → baseline → threshold → detect → sublevel → export/plot.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

import numpy as np

from nano_ext.models import (
    BaselineResult,
    DetectionConfig,
    Event,
    EventDirection,
    SignalData,
    ThresholdResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

class PipelineResult:
    """Container for all outputs produced by a pipeline run.

    Attributes
    ----------
    signal_data : SignalData
        The loaded signal (raw).
    filtered_signal : np.ndarray
        Signal after low-pass filtering.
    baseline_result : BaselineResult
        Baseline estimation outputs.
    threshold_result : ThresholdResult
        GMM + BIC threshold outputs.
    events : list[Event]
        Detected events (with sub-levels if analysed).
    config : DetectionConfig
        Configuration used.
    """

    def __init__(
        self,
        signal_data: SignalData,
        filtered_signal: np.ndarray,
        baseline_result: BaselineResult,
        threshold_result: ThresholdResult,
        events: list[Event],
        config: DetectionConfig,
    ):
        self.signal_data = signal_data
        self.filtered_signal = filtered_signal
        self.baseline_result = baseline_result
        self.threshold_result = threshold_result
        self.events = events
        self.config = config

    @property
    def n_events(self) -> int:
        """Total number of detected events."""
        return len(self.events)

    @property
    def n_multilevel(self) -> int:
        """Number of events classified as multi-level (stepwise)."""
        return sum(1 for ev in self.events if ev.is_multilevel)

    def summary(self) -> str:
        """Human-readable summary of pipeline results."""
        import math

        noise_std = self.baseline_result.noise_std
        noise_str = (
            f"{noise_std:.4f}" if math.isfinite(noise_std) else "N/A"
        )

        # Effective detection threshold: always shown as a positive absolute
        # deviation from baseline, matching the value used in detect_events.
        tr = self.threshold_result
        raw = tr.threshold
        if tr.baseline_component_idx is not None and tr.component_stds is not None:
            op_mean = tr.component_means[tr.baseline_component_idx]
            op_std  = tr.component_stds[tr.baseline_component_idx]
            eff_down = abs(min(raw, op_mean - 5.0 * op_std))
            eff_up   = max(abs(raw), op_mean + 5.0 * op_std)
        else:
            eff_down = abs(raw)
            eff_up   = abs(raw)

        direction = self.config.event_direction
        from nano_ext.models import EventDirection
        if direction == EventDirection.UP:
            thr_display = eff_up
            thr_label = "Threshold (↑): "
        elif direction == EventDirection.DOWN:
            thr_display = eff_down
            thr_label = "Threshold (↓): "
        else:
            thr_display = eff_up
            thr_label = "Threshold (↕): "

        units = self.signal_data.units or ""
        lines = [
            f"Sampling rate:   {self.signal_data.sampling_rate:.0f} Hz",
            f"Duration:        {self.signal_data.duration_sec:.3f} s",
            f"Samples:         {self.signal_data.n_samples}",
            f"Noise std:       {noise_str} {units}",
            f"GMM components:  {tr.n_components}",
            f"{thr_label}{thr_display:.4f} {units}",
            f"Events detected: {self.n_events}",
            f"Multi-level:     {self.n_multilevel}",
        ]
        if self.events:
            durations = [ev.duration * 1000 for ev in self.events]
            depths = [ev.depth for ev in self.events]
            lines.append(
                f"Duration range:  {min(durations):.3f} – {max(durations):.3f} ms"
            )
            lines.append(
                f"Depth range:     {min(depths):.2f} – {max(depths):.2f} {self.signal_data.units}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pipeline function
# ---------------------------------------------------------------------------

def run_pipeline(
    signal_data: SignalData,
    config: Optional[DetectionConfig] = None,
    analyze_sublevel: bool = True,
    verbose: bool = False,
    control_signal: Optional[SignalData] = None,
    on_step: Optional[callable] = None,
) -> PipelineResult:
    """Run the full event detection pipeline.

    Parameters
    ----------
    signal_data : SignalData
        Loaded signal data (from ``read_abf`` or ``read_binary``).
    config : DetectionConfig, optional
        Detection configuration. If None, defaults are used.
    analyze_sublevel : bool
        Whether to perform sub-level analysis on detected events.
    verbose : bool
        If True, log progress messages.
    control_signal : SignalData, optional
        Analyte-free (negative control) recording.  When provided, its
        noise standard deviation replaces the sample-derived estimate,
        giving a cleaner noise floor when the sample is event-dense.
        The control must have the same sampling rate as *signal_data*.

    Returns
    -------
    PipelineResult
        All intermediate and final results.
    """
    if config is None:
        config = DetectionConfig()

    sr = signal_data.sampling_rate
    signal = signal_data.signal

    # ---- Step 1: Low-pass filtering ----
    if on_step:
        on_step("Filter signal" if config.apply_filter else "Skip filter (pre-filtered)")
    if verbose:
        logger.info("Filtering signal...")
    from nano_ext.preprocessing.filters import lowpass_filter

    cutoff = None
    if config.apply_filter:
        cutoff = config.filter_cutoff if config.filter_cutoff else sr / 10.0
        filtered = lowpass_filter(
            signal,
            sampling_rate=sr,
            cutoff=cutoff,
            filter_type=config.filter_type,
            order=config.filter_order,
        )
        if verbose:
            logger.info(f"  Filter: {config.filter_type} order={config.filter_order} cutoff={cutoff:.0f} Hz")
    else:
        filtered = signal
        cutoff = config.pre_applied_filter_cutoff if config.pre_applied_filter_cutoff else sr / 10.0
        if verbose:
            logger.info(f"  Low-pass filtering skipped. Using pre-applied cutoff: {cutoff:.0f} Hz")

    # ---- Step 2: Baseline estimation ----
    if on_step:
        on_step("Estimate baseline")
    if verbose:
        logger.info("Estimating baseline...")
    from nano_ext.preprocessing.baseline import estimate_baseline

    bl_result = estimate_baseline(
        filtered,
        sampling_rate=sr,
        detrend_method=config.detrend_method,
        detrend_order=config.detrend_order,
        window_sec=config.baseline_window_sec,
        n_iterations=config.baseline_iterations,
        percentile=config.baseline_percentile,
        n_sigma=config.baseline_n_sigma,
        noise_estimation=config.noise_estimation,
    )
    if verbose:
        logger.info(f"  Noise std (sample): {bl_result.noise_std:.4f}")

    # ---- Step 2b: Override noise estimate from control (if provided) ----
    if control_signal is not None:
        if control_signal.sampling_rate != sr:
            logger.warning(
                f"Control sampling rate ({control_signal.sampling_rate} Hz) differs "
                f"from sample ({sr} Hz). Control noise estimate may be unreliable."
            )
        from nano_ext.preprocessing.control import compute_control_stats
        ctrl_stats = compute_control_stats(control_signal, config)
        if verbose:
            logger.info(
                f"  Noise std (control): {ctrl_stats.noise_std:.4f} "
                f"→ overrides sample estimate"
            )
        bl_result = BaselineResult(
            local_baseline=bl_result.local_baseline,
            trend=bl_result.trend,
            residual=bl_result.residual,
            noise_std=ctrl_stats.noise_std,
        )

    # ---- Step 3: Threshold determination ----
    if on_step:
        on_step("Determine threshold (GMM)")
    if verbose:
        logger.info("Determining threshold (GMM + BIC)...")
    from nano_ext.detection.threshold import determine_threshold

    th_result = determine_threshold(
        bl_result.residual,
        max_components=config.gmm_max_components,
        criterion=config.bic_criterion,
        max_samples_for_fit=config.gmm_max_samples,
    )
    if verbose:
        logger.info(
            f"  Components: {th_result.n_components}, "
            f"Threshold: {th_result.threshold:.4f}"
        )

    # ---- Step 4: Event detection ----
    if on_step:
        on_step("Detect events")
    if verbose:
        logger.info("Detecting events...")
    from nano_ext.detection.events import detect_events

    events = detect_events(
        residual=bl_result.residual,
        sampling_rate=sr,
        threshold=th_result.threshold,
        baseline=bl_result.local_baseline,
        direction=config.event_direction,
        min_event_duration_sec=config.min_event_duration_sec,
        merge_gap_sec=config.merge_gap_sec,
        filter_cutoff=cutoff,
        # Pass GMM parameters for improved event detection
        gmm_means=th_result.component_means,
        gmm_stds=th_result.component_stds,
        gmm_weights=th_result.component_weights,
        baseline_component_idx=th_result.baseline_component_idx,
    )
    if verbose:
        logger.info(f"  Events detected: {len(events)}")

    # ---- Step 5: Sub-level analysis ----
    if analyze_sublevel and events:
        if on_step:
            on_step("Analyze sub-levels")
        if verbose:
            logger.info("Analyzing sub-levels...")
        from nano_ext.detection.sublevel import analyze_events_sublevels

        events = analyze_events_sublevels(
            events=events,
            signal=filtered,
            baseline=bl_result.local_baseline,
            sampling_rate=sr,
            filter_cutoff=cutoff,
            max_levels=config.max_sublevel_depth,
            min_segment_samples=config.min_segment_samples,
        )
        n_multi = sum(1 for ev in events if ev.is_multilevel)
        if verbose:
            logger.info(f"  Multi-level events: {n_multi}")

    return PipelineResult(
        signal_data=signal_data,
        filtered_signal=filtered,
        baseline_result=bl_result,
        threshold_result=th_result,
        events=events,
        config=config,
    )


# ---------------------------------------------------------------------------
# Convenience: file → results in one call
# ---------------------------------------------------------------------------

def process_file(
    filepath: Union[str, Path],
    config: Optional[DetectionConfig] = None,
    file_format: Optional[str] = None,
    channel: int = 0,
    sampling_rate: Optional[float] = None,
    dtype: str = "float32",
    analyze_sublevel: bool = True,
    verbose: bool = False,
    control_path: Optional[Union[str, Path]] = None,
) -> PipelineResult:
    """Load a file and run the full pipeline.

    Parameters
    ----------
    filepath : str or Path
        Path to the data file.
    config : DetectionConfig, optional
        Pipeline configuration.
    file_format : str, optional
        File format: ``"abf"``, ``"binary"``, or None (auto-detect
        from extension).
    channel : int
        Channel number (for multi-channel ABF files).
    sampling_rate : float, optional
        Sampling rate in Hz (required for binary files).
    dtype : str
        Data type for binary files (default: ``"float32"``).
    analyze_sublevel : bool
        Whether to perform sub-level analysis.
    verbose : bool
        If True, log progress.
    control_path : str or Path, optional
        Path to a negative control file (same format as *filepath*).
        When provided, the control's noise estimate is used to stabilise
        threshold determination.

    Returns
    -------
    PipelineResult
        Pipeline results.
    """
    filepath = Path(filepath)

    # Auto-detect format
    if file_format is None:
        ext = filepath.suffix.lower()
        if ext == ".abf":
            file_format = "abf"
        else:
            file_format = "binary"

    # Load data
    if file_format == "abf":
        from nano_ext.io.abf_reader import read_abf
        signal_data = read_abf(str(filepath), channel=channel)
    elif file_format == "binary":
        if sampling_rate is None:
            raise ValueError(
                "sampling_rate is required for binary files. "
                "Specify --sampling-rate on the command line."
            )
        from nano_ext.io.binary_reader import read_binary
        signal_data = read_binary(
            str(filepath), sampling_rate=sampling_rate, dtype=dtype
        )
    else:
        raise ValueError(f"Unknown file format: {file_format}. Use 'abf' or 'binary'.")

    if verbose:
        logger.info(f"Loaded {filepath.name}: {signal_data.n_samples} samples @ {signal_data.sampling_rate} Hz")

    # Load control signal if path is provided
    control_signal: Optional[SignalData] = None
    if control_path is not None:
        control_path = Path(control_path)
        ctrl_format = "abf" if control_path.suffix.lower() == ".abf" else "binary"
        if ctrl_format == "abf":
            from nano_ext.io.abf_reader import read_abf
            control_signal = read_abf(str(control_path), channel=channel)
        else:
            if sampling_rate is None:
                raise ValueError("sampling_rate is required for binary control files.")
            from nano_ext.io.binary_reader import read_binary
            control_signal = read_binary(
                str(control_path), sampling_rate=sampling_rate, dtype=dtype
            )
        if verbose:
            logger.info(
                f"Loaded control {control_path.name}: "
                f"{control_signal.n_samples} samples @ {control_signal.sampling_rate} Hz"
            )

    return run_pipeline(
        signal_data=signal_data,
        config=config,
        analyze_sublevel=analyze_sublevel,
        verbose=verbose,
        control_signal=control_signal,
    )
