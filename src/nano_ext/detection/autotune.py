"""Automatic parameter tuning for the event detection pipeline.

Provides :func:`suggest_config`, which analyses a loaded signal and returns
a :class:`~nano_ext.models.DetectionConfig` with parameters tuned to the
signal's noise floor, duration, and sampling rate.

The key noise-aware adjustment is ``min_event_duration_sec``:

- Clean signal  (noise < 2 % of amplitude range) → 2 / filter_cutoff
- Moderate noise (2–5 %)                          → 3 / filter_cutoff
- Noisy signal  (> 5 %)                           → 5 / filter_cutoff

All auto-determined values can be overridden by passing keyword arguments
that match :class:`~nano_ext.models.DetectionConfig` field names.
"""

from __future__ import annotations

import numpy as np

from nano_ext.models import DetectionConfig, SignalData


def estimate_noise_floor(signal: np.ndarray) -> float:
    """Estimate the noise standard deviation from consecutive differences.

    Uses the MAD of first differences, which is insensitive to slow baseline
    drift and event samples.  For white Gaussian noise:

        std(diff(x)) = sqrt(2) * std(noise)

    so the noise std is recovered as::

        noise_std = MAD(|diff(x)|) * 1.4826 / sqrt(2)

    Parameters
    ----------
    signal : np.ndarray
        Raw or filtered current signal (1-D array).

    Returns
    -------
    float
        Estimated noise standard deviation in the same units as *signal*.
    """
    n_max = 100_000
    sig = signal[:n_max] if len(signal) > n_max else signal
    diffs = np.abs(np.diff(sig))
    mad = float(np.median(diffs))
    return mad * 1.4826 / np.sqrt(2)


def suggest_config(signal_data: SignalData, **overrides) -> DetectionConfig:
    """Analyse a signal and return a noise-aware :class:`DetectionConfig`.

    All automatically determined values can be overridden by passing any
    :class:`DetectionConfig` field name as a keyword argument.

    Parameters
    ----------
    signal_data : SignalData
        Loaded signal to analyse.
    **overrides
        Any :class:`DetectionConfig` field passed as a keyword argument
        takes precedence over the auto-determined value.

    Returns
    -------
    DetectionConfig
        Suggested configuration.

    Examples
    --------
    >>> from nano_ext import suggest_config
    >>> cfg = suggest_config(signal_data)
    >>> cfg = suggest_config(signal_data, event_direction=EventDirection.BOTH)
    """
    sr = signal_data.sampling_rate
    signal = signal_data.signal
    n = len(signal)

    # --- Filter cutoff (sr / 10 is a conservative default) ---
    filter_cutoff = sr / 10.0

    # --- Quick noise estimate (no baseline needed) ---
    noise_std = estimate_noise_floor(signal)

    # --- Signal amplitude range (robust: 1st–99th percentile) ---
    sample = signal[:min(n, 500_000)]
    p1, p99 = np.percentile(sample, [1, 99])
    signal_range = float(max(p99 - p1, 1e-10))

    # --- Noise fraction: noise relative to the usable signal range ---
    noise_fraction = noise_std / signal_range

    # --- Noise-aware minimum event duration ---
    # Shorter events are unreliable when signal-to-noise is poor.
    if noise_fraction < 0.02:
        duration_factor = 2.0   # clean: filter rise time is the limit
    elif noise_fraction < 0.05:
        duration_factor = 3.0   # moderate noise
    else:
        duration_factor = 5.0   # noisy: require longer events
    min_event_duration_sec = duration_factor / filter_cutoff

    # --- Merge gap (1.5 / cutoff is standard) ---
    merge_gap_sec = 1.5 / filter_cutoff

    # --- Baseline window: 5 s or 10 % of signal duration, min 1 s ---
    baseline_window_sec = max(1.0, min(5.0, signal_data.duration_sec * 0.1))

    # --- GMM components: 5 avoids over-fitting on typical recordings ---
    gmm_max_components = 5

    params: dict = dict(
        filter_cutoff=filter_cutoff,
        baseline_window_sec=baseline_window_sec,
        min_event_duration_sec=min_event_duration_sec,
        merge_gap_sec=merge_gap_sec,
        gmm_max_components=gmm_max_components,
    )
    params.update(overrides)
    return DetectionConfig(**params)
