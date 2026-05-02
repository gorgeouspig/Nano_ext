"""Negative control analysis for noise characterisation.

A *negative control* (analyte-free / solvent-only) nanopore trace contains
the open-pore current with all instrumental noise but no translocation events.
Running this trace through baseline estimation yields a clean, unbiased noise
estimate that can replace the self-estimated noise of an event-laden sample.

Usage in the pipeline
---------------------
Pass the control's :class:`SignalData` to :func:`~nano_ext.pipeline.run_pipeline`
as the *control_signal* parameter::

    from nano_ext import run_pipeline, read_abf
    from nano_ext.preprocessing.control import compute_control_stats

    sample   = read_abf("sample.abf")
    control  = read_abf("control.abf")
    result   = run_pipeline(sample, control_signal=control)

When a control signal is provided, its ``noise_std`` replaces the
sample-derived estimate in the pipeline, stabilising threshold and
event-detection decisions when the sample contains many events.

Caveats
-------
- The control and sample must share the same sampling rate and filter cutoff.
  A warning is emitted when sampling rates differ.
- A control trace taken far in time from the sample may not represent the
  current noise state of the pore.  For best results, use a bracketed
  control (before + after the sample run).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

from nano_ext.models import BaselineResult, DetectionConfig, SignalData

logger = logging.getLogger(__name__)


@dataclass
class ControlStats:
    """Noise and baseline statistics derived from a negative control trace.

    Attributes
    ----------
    noise_std : float
        Estimated noise standard deviation (MAD-based, in signal units).
    baseline_mean : float
        Mean open-pore current over the recording (in signal units).
    baseline_std : float
        Standard deviation of the baseline trace — captures slow drift.
    sampling_rate : float
        Sampling rate of the control trace in Hz.
    n_samples : int
        Number of samples in the control trace.
    """

    noise_std: float
    baseline_mean: float
    baseline_std: float
    sampling_rate: float
    n_samples: int


def compute_control_stats(
    control: SignalData,
    config: Optional[DetectionConfig] = None,
) -> ControlStats:
    """Compute noise and baseline statistics from a negative control trace.

    Applies the same filtering and baseline estimation steps as the main
    pipeline to extract a clean noise estimate from the control trace.

    Parameters
    ----------
    control : SignalData
        Analyte-free (negative control) recording.
    config : DetectionConfig, optional
        Pipeline configuration.  The same config used for the sample should
        be passed here so that filtering and baseline parameters match.
        Defaults to :class:`DetectionConfig` with all defaults.

    Returns
    -------
    ControlStats
        Noise and baseline statistics from the control trace.
    """
    if config is None:
        config = DetectionConfig()

    from nano_ext.preprocessing.filters import lowpass_filter
    from nano_ext.preprocessing.baseline import estimate_baseline

    sr = control.sampling_rate
    signal = control.signal

    # Filter
    if config.apply_filter:
        cutoff = config.filter_cutoff if config.filter_cutoff else sr / 10.0
        filtered = lowpass_filter(
            signal, sr, cutoff,
            filter_type=config.filter_type,
            order=config.filter_order,
        )
    else:
        filtered = signal

    # Baseline estimation (control has no events so iteration is trivial,
    # but we run it to stay consistent with the sample pipeline)
    bl: BaselineResult = estimate_baseline(
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

    return ControlStats(
        noise_std=bl.noise_std,
        baseline_mean=float(np.mean(bl.local_baseline)),
        baseline_std=float(np.std(bl.local_baseline)),
        sampling_rate=sr,
        n_samples=len(signal),
    )
