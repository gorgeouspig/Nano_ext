"""Baseline estimation and drift correction for nanopore signals.

Provides a multi-stage baseline estimation pipeline:
1. Global detrending (polynomial/linear/spline)
2. Iterative local baseline estimation (percentile-based, with event exclusion)
3. Robust noise level estimation (MAD-based)
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from scipy.interpolate import UnivariateSpline
from scipy.ndimage import uniform_filter1d

from nano_ext.models import BaselineResult


def estimate_baseline(
    signal: np.ndarray,
    sampling_rate: float,
    detrend_method: str = "polynomial",
    detrend_order: int = 3,
    window_sec: float = 5.0,
    n_iterations: int = 3,
    percentile: float = 90.0,
    n_sigma: float = 5.0,
    noise_estimation: str = "mad",
) -> BaselineResult:
    """Estimate the local baseline of a nanopore current trace.

    Combines global detrending with iterative local baseline estimation.
    Designed to handle baseline drift (gradual increases/decreases in
    open-channel current over time).

    Parameters
    ----------
    signal : np.ndarray
        Input signal (1-D, typically after low-pass filtering).
    sampling_rate : float
        Sampling rate in Hz.
    detrend_method : str
        Global detrending method: "polynomial", "linear", "spline", or "none".
    detrend_order : int
        Polynomial order for detrending (used when method="polynomial").
    window_sec : float
        Window size in seconds for local baseline estimation.
    n_iterations : int
        Number of iterations for the iterative baseline refinement.
    percentile : float
        Percentile for initial baseline estimation (e.g., 90 means the
        90th percentile of current in each window is used as baseline).
    n_sigma : float
        Number of noise standard deviations below the baseline to set
        as the event exclusion threshold during iteration.
    noise_estimation : str
        Method for noise estimation: "mad" (robust) or "std".

    Returns
    -------
    BaselineResult
        Contains local_baseline, trend, residual, and noise_std.
    """
    n_samples = len(signal)

    # --- Stage 1: Global detrending ---
    trend = _estimate_trend(signal, sampling_rate, detrend_method, detrend_order)
    detrended = signal - trend

    # --- Stage 2: Iterative local baseline estimation ---
    window_samples = max(1, int(window_sec * sampling_rate))
    # Ensure odd window for symmetry
    if window_samples % 2 == 0:
        window_samples += 1

    # Initial mask: all samples are baseline candidates
    mask = np.ones(n_samples, dtype=bool)

    local_baseline = np.zeros(n_samples, dtype=np.float64)

    for iteration in range(n_iterations):
        # Estimate local baseline using only non-event (masked) samples
        local_baseline = _local_baseline_masked(
            detrended, mask, window_samples, percentile
        )

        # Compute residual
        residual = detrended - local_baseline

        # Estimate noise from baseline regions
        if mask.sum() > 10:
            noise_std = _estimate_noise(residual[mask], noise_estimation)
        else:
            noise_std = _estimate_noise(residual, noise_estimation)

        # Update mask: exclude points that deviate significantly from baseline
        # For "down" events: residual << 0
        # For "up" events: residual >> 0
        # We exclude both directions to be safe
        mask = np.abs(residual) < n_sigma * noise_std

    # Final residual
    residual = detrended - local_baseline

    # Final noise estimate from baseline regions
    if mask.sum() > 10:
        noise_std = _estimate_noise(residual[mask], noise_estimation)
    else:
        noise_std = _estimate_noise(residual, noise_estimation)

    # The full baseline (including trend) for absolute current reference
    full_baseline = trend + local_baseline

    return BaselineResult(
        local_baseline=full_baseline,
        trend=trend,
        residual=residual,
        noise_std=noise_std,
    )


def _estimate_trend(
    signal: np.ndarray,
    sampling_rate: float,
    method: str,
    order: int,
) -> np.ndarray:
    """Estimate the global trend component of the signal.

    Parameters
    ----------
    signal : np.ndarray
        Input signal.
    sampling_rate : float
        Sampling rate in Hz.
    method : str
        "polynomial", "linear", "spline", or "none".
    order : int
        Polynomial order (for "polynomial" method).

    Returns
    -------
    np.ndarray
        Estimated trend (same length as signal).
    """
    n_samples = len(signal)

    if method == "none":
        return np.zeros(n_samples, dtype=np.float64)

    t = np.arange(n_samples) / sampling_rate

    if method == "linear":
        # Robust linear fit: use percentile to avoid event influence
        # Subsample for speed on large signals
        step = max(1, n_samples // 10000)
        t_sub = t[::step]
        s_sub = signal[::step]
        coeffs = np.polyfit(t_sub, s_sub, deg=1)
        return np.polyval(coeffs, t)

    elif method == "polynomial":
        # Polynomial fit with iterative outlier rejection
        step = max(1, n_samples // 10000)
        t_sub = t[::step]
        s_sub = signal[::step]

        # First pass: fit all data
        mask = np.ones(len(s_sub), dtype=bool)
        for _ in range(3):  # 3 iterations of outlier rejection
            coeffs = np.polyfit(t_sub[mask], s_sub[mask], deg=order)
            fitted = np.polyval(coeffs, t_sub)
            residuals = s_sub - fitted
            std = np.std(residuals[mask]) if mask.sum() > order + 1 else np.std(residuals)
            # Exclude points > 3 sigma from fit (likely events)
            mask = np.abs(residuals) < 3.0 * std

        return np.polyval(coeffs, t)

    elif method == "spline":
        step = max(1, n_samples // 5000)
        t_sub = t[::step]
        s_sub = signal[::step]

        # High smoothing factor for trend estimation
        smoothing = len(s_sub) * (np.std(signal) * 0.5) ** 2
        spline = UnivariateSpline(t_sub, s_sub, s=smoothing)
        return spline(t)

    else:
        raise ValueError(
            f"Unknown detrend method: {method}. "
            "Use 'polynomial', 'linear', 'spline', or 'none'."
        )


def _local_baseline_masked(
    signal: np.ndarray,
    mask: np.ndarray,
    window_samples: int,
    percentile: float,
) -> np.ndarray:
    """Estimate local baseline using masked (non-event) samples.

    Uses a sliding window approach where only non-event samples
    contribute to the baseline estimate.

    Parameters
    ----------
    signal : np.ndarray
        Detrended signal.
    mask : np.ndarray
        Boolean mask (True = baseline candidate, False = event).
    window_samples : int
        Window size in samples.
    percentile : float
        Percentile to use within each window.

    Returns
    -------
    np.ndarray
        Estimated local baseline.
    """
    n_samples = len(signal)
    half_win = window_samples // 2
    baseline = np.zeros(n_samples, dtype=np.float64)

    # Replace event samples with NaN for percentile calculation
    masked_signal = signal.copy()
    masked_signal[~mask] = np.nan

    # Use stride-based approach for efficiency
    # For very large signals, we subsample and interpolate
    if n_samples > 500_000:
        # Subsample: compute baseline at every Kth point, then interpolate
        step = max(1, window_samples // 4)
        indices = np.arange(0, n_samples, step)
        baseline_sparse = np.zeros(len(indices), dtype=np.float64)

        for i, center in enumerate(indices):
            start = max(0, center - half_win)
            end = min(n_samples, center + half_win)
            window = masked_signal[start:end]
            valid = window[~np.isnan(window)]
            if len(valid) > 0:
                baseline_sparse[i] = np.percentile(valid, percentile)
            elif i > 0:
                baseline_sparse[i] = baseline_sparse[i - 1]

        # Interpolate to full resolution
        baseline = np.interp(np.arange(n_samples), indices, baseline_sparse)
    else:
        for center in range(n_samples):
            start = max(0, center - half_win)
            end = min(n_samples, center + half_win)
            window = masked_signal[start:end]
            valid = window[~np.isnan(window)]
            if len(valid) > 0:
                baseline[center] = np.percentile(valid, percentile)
            elif center > 0:
                baseline[center] = baseline[center - 1]

    # Smooth the baseline to remove discontinuities
    smooth_window = max(3, window_samples // 10)
    if smooth_window % 2 == 0:
        smooth_window += 1
    baseline = uniform_filter1d(baseline, size=smooth_window)

    return baseline


def _estimate_noise(
    residual: np.ndarray,
    method: str = "mad",
) -> float:
    """Estimate noise standard deviation robustly.

    Parameters
    ----------
    residual : np.ndarray
        Residual signal (baseline-corrected).
    method : str
        "mad" for Median Absolute Deviation (robust to outliers),
        "std" for standard deviation.

    Returns
    -------
    float
        Estimated noise standard deviation.
    """
    if len(residual) == 0:
        return 1.0  # Fallback

    if method == "mad":
        # MAD-based robust standard deviation estimator
        # For Gaussian data, std ≈ 1.4826 * MAD
        mad = np.median(np.abs(residual - np.median(residual)))
        return float(mad * 1.4826)
    elif method == "std":
        return float(np.std(residual))
    else:
        raise ValueError(f"Unknown noise estimation method: {method}")
