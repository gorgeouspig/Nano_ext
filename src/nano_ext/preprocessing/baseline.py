"""Baseline estimation and drift correction for nanopore signals.

Provides a multi-stage baseline estimation pipeline:
1. Global detrending (polynomial/linear/spline)
2. Iterative local baseline estimation (percentile-based, with event exclusion)
3. Robust noise level estimation (MAD-based)

Arrays keep the dtype of the input signal (float32 for signals from the
readers), so a 10-minute 250 kHz trace needs ~600 MB per array rather than
1.2 GB.  Reductions (medians, fits) are exact or done in float64.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from scipy.interpolate import UnivariateSpline
from scipy.ndimage import uniform_filter1d

from nano_ext.models import BaselineResult
from nano_ext._nano_ext import local_baseline_percentile, local_baseline_percentile_f32

logger = logging.getLogger(__name__)


def estimate_baseline(
    signal: np.ndarray,
    sampling_rate: float,
    detrend_method: str = "none",
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
    dtype = np.float32 if signal.dtype == np.float32 else np.float64

    # --- Stage 1: Global detrending ---
    if detrend_method == "none":
        # A read-only zero view: no full-length allocation.
        trend = np.broadcast_to(dtype(0), (n_samples,))
        detrended = np.asarray(signal, dtype=dtype)  # read-only use below: no copy
    else:
        trend = _estimate_trend(signal, sampling_rate, detrend_method, detrend_order).astype(dtype, copy=False)
        detrended = signal - trend

    # --- Stage 2: Iterative local baseline estimation ---
    window_samples = max(1, int(window_sec * sampling_rate))
    # Ensure odd window for symmetry
    if window_samples % 2 == 0:
        window_samples += 1

    # Memory note: besides the input, at most three full-length float arrays
    # (baseline, residual, a noise-estimation temporary) plus a boolean mask
    # are alive at once.  |r| < t is evaluated as -t < r < t (exactly
    # equivalent for floats) to avoid an abs() temporary.

    # Improved mask initialization: use provisional baseline to avoid deadlock
    # Compute provisional baseline using the entire signal (no masking)
    provisional_mask = np.ones(n_samples, dtype=bool)
    provisional_baseline = _local_baseline_masked(
        detrended, provisional_mask, window_samples, percentile
    )
    del provisional_mask
    residual = np.subtract(detrended, provisional_baseline)
    # Estimate noise from provisional residual (in place), then restore it.
    provisional_noise_std = _estimate_noise(residual, noise_estimation, overwrite=True)
    np.subtract(detrended, provisional_baseline, out=residual)
    del provisional_baseline
    # Initial mask: exclude points that deviate significantly from provisional baseline
    mask = _within(residual, n_sigma * provisional_noise_std)
    # Ensure we have enough baseline candidates; if not, fall back to all True
    if mask.sum() < 10:
        mask = np.ones(n_samples, dtype=bool)

    local_baseline = None
    for iteration in range(n_iterations):
        # Estimate local baseline using only non-event (masked) samples
        local_baseline = None  # release the previous estimate first
        local_baseline = _local_baseline_masked(
            detrended, mask, window_samples, percentile
        )

        # Compute residual
        np.subtract(detrended, local_baseline, out=residual)

        # Estimate noise from baseline regions
        if mask.sum() > 10:
            noise_std = _estimate_noise(residual[mask], noise_estimation, overwrite=True)
        else:
            noise_std = _estimate_noise(residual, noise_estimation)

        # Update mask: exclude points that deviate significantly from baseline
        # For "down" events: residual << 0
        # For "up" events: residual >> 0
        # We exclude both directions to be safe
        mask = _within(residual, n_sigma * noise_std)
        # Guard: if noise_std collapsed (0 / NaN) the new mask will be nearly
        # empty, which would make the next iteration's baseline catastrophically
        # wrong (Rust returns 0.0 when no unmasked samples remain).
        if mask.sum() < max(10, int(0.005 * n_samples)):
            mask = np.ones(n_samples, dtype=bool)

    # Final pass: recompute baseline at the median of masked (open-pore) samples.
    # The iterations above used a high percentile to robustly establish the event
    # mask; now that the mask is stable, the median is an unbiased estimator of
    # the true open-pore mean and removes the ~1.3σ upward bias from the high
    # percentile.
    # Only apply if the mask is sufficiently populated; a sparse mask means the
    # iterative estimation failed and we keep the last iteration's baseline.
    if mask.sum() >= max(10, int(0.005 * n_samples)):
        local_baseline = None
        local_baseline = _local_baseline_masked(
            detrended, mask, window_samples, 50.0
        )
    elif local_baseline is None:  # n_iterations == 0 (previous behaviour: zeros)
        local_baseline = np.zeros(n_samples, dtype=dtype)

    # Final residual
    np.subtract(detrended, local_baseline, out=residual)
    del detrended

    # Final noise estimate from baseline regions
    if mask.sum() > 10:
        noise_std = _estimate_noise(residual[mask], noise_estimation, overwrite=True)
    else:
        noise_std = _estimate_noise(residual, noise_estimation)

    # The full baseline (including trend) for absolute current reference
    full_baseline = np.add(trend, local_baseline, out=local_baseline)

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

    if method == "linear":
        # Robust linear fit: use percentile to avoid event influence
        # Subsample for speed on large signals
        step = max(1, n_samples // 10000)
        t_sub = _time_sub(n_samples, sampling_rate, step)
        s_sub = signal[::step]
        if len(t_sub) > 0: # Check to ensure data exists for polyfit
            coeffs = np.polyfit(t_sub, s_sub, deg=1)
            return _eval_over_time(lambda t: np.polyval(coeffs, t), n_samples, sampling_rate, signal.dtype)
        else:
            return np.zeros(n_samples, dtype=np.float64) # Fallback

    elif method == "polynomial":
        # Polynomial fit with iterative outlier rejection
        step = max(1, n_samples // 10000)
        t_sub = _time_sub(n_samples, sampling_rate, step)
        s_sub = signal[::step]

        coeffs: np.ndarray
        if len(t_sub) > order:
            coeffs = np.polyfit(t_sub, s_sub, deg=order)
        else:
            coeffs = np.array([np.mean(s_sub)]) if len(s_sub) > 0 else np.array([0.0]) # Fallback for insufficient data

        mask = np.ones(len(s_sub), dtype=bool) # Initialize mask here!

        for _ in range(3):  # 3 iterations of outlier rejection
            fitted = np.polyval(coeffs, t_sub)
            residuals = s_sub - fitted

            std_residuals: float
            if mask.sum() > order + 1 and len(residuals[mask]) > 0:
                std_residuals = np.std(residuals[mask])
            elif len(residuals) > 0:
                std_residuals = np.std(residuals)
            else:
                std_residuals = 1.0 # Default if no data

            mask = np.abs(residuals) < 3.0 * std_residuals
            if mask.sum() > order + 1 and len(t_sub[mask]) > order:
                coeffs = np.polyfit(t_sub[mask], s_sub[mask], deg=order)

        return _eval_over_time(lambda t: np.polyval(coeffs, t), n_samples, sampling_rate, signal.dtype)

    elif method == "spline":
        step = max(1, n_samples // 5000)
        t_sub = _time_sub(n_samples, sampling_rate, step)
        s_sub = signal[::step]

        # Handle cases where s_sub might be empty or too short for spline
        if len(s_sub) > 1: # UnivariateSpline requires at least 2 data points
            # Ensure smoothing is a float
            std_signal: float = float(np.std(signal, dtype=np.float64)) if len(signal) > 1 else 0.5
            len_s_sub_float: float = float(len(s_sub))
            smoothing_factor_float: float = (std_signal * 0.5) ** 2
            smoothing: float = len_s_sub_float * smoothing_factor_float
            spline = UnivariateSpline(t_sub, s_sub, s=smoothing)
            return _eval_over_time(spline, n_samples, sampling_rate, signal.dtype)
        else:
            return np.zeros(n_samples, dtype=np.float64) # Fallback

    else:
        raise ValueError(
            f"Unknown detrend method: {method}. "
            "Use 'polynomial', 'linear', 'spline', or 'none'."
        )


def _within(x: np.ndarray, limit: float) -> np.ndarray:
    """Boolean mask of ``|x| < limit`` without an abs() temporary."""
    lim = x.dtype.type(limit)
    mask = x < lim
    mask &= x > -lim
    return mask


def _time_sub(n_samples: int, sampling_rate: float, step: int) -> np.ndarray:
    """Times (s) of every `step`-th sample."""
    return np.arange(0, n_samples, step) / sampling_rate


def _eval_over_time(
    fn, n_samples: int, sampling_rate: float, dtype=np.float64, chunk: int = 1 << 22,
) -> np.ndarray:
    """Evaluate ``fn(t)`` at every sample time, chunked to avoid a full-length
    float64 time array.  Output is float32 for float32 signals."""
    out = np.empty(n_samples, dtype=np.float32 if dtype == np.float32 else np.float64)
    for i in range(0, n_samples, chunk):
        t = np.arange(i, min(n_samples, i + chunk)) / sampling_rate
        out[i:i + len(t)] = fn(t)
    return out


def _local_baseline_masked(
    signal: np.ndarray,
    mask: np.ndarray,
    window_samples: int,
    percentile: float,
) -> np.ndarray:
    """Estimate local baseline using masked (non-event) samples.

    Uses a sliding window approach where only non-event samples
    contribute to the baseline estimate.
    This function uses a Rust implementation for improved performance.

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
    # Use Rust implementation for performance.  The kernel reads the numpy
    # buffers in place and returns a numpy array of the same float type.
    try:
        mask = np.ascontiguousarray(mask, dtype=bool)
        if signal.dtype == np.float32:
            return local_baseline_percentile_f32(
                np.ascontiguousarray(signal), mask, window_samples, percentile
            )
        return local_baseline_percentile(
            np.ascontiguousarray(signal, dtype=np.float64), mask, window_samples, percentile
        )
    except Exception as e:
        logger.warning(f"Rust baseline estimation failed, falling back to Python: {e}")
        return _local_baseline_masked_python(signal, mask, window_samples, percentile)


def _local_baseline_masked_python(
    signal: np.ndarray,
    mask: np.ndarray,
    window_samples: int,
    percentile: float,
) -> np.ndarray:
    """Original Python implementation of local baseline estimation."""
    n_samples = len(signal)
    half_win = window_samples // 2
    baseline = np.zeros(n_samples, dtype=np.float64)

    fallback = np.percentile(signal, 90.0)

    # Replace event samples with NaN for percentile calculation
    masked_signal = signal.copy()
    masked_signal[~mask] = np.nan

    # Switch to the subsampled path when the direct O(n*window) work exceeds
    # a threshold — same logic as the Rust kernel.
    if n_samples * window_samples > 10_000_000:
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
            else:
                baseline_sparse[i] = fallback

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
            else:
                baseline[center] = fallback

    # Smooth the baseline to remove discontinuities
    smooth_window = max(3, window_samples // 10)
    if smooth_window % 2 == 0:
        smooth_window += 1
    baseline = uniform_filter1d(baseline, size=smooth_window)

    return baseline


def _estimate_noise(
    residual: np.ndarray,
    method: str = "mad",
    overwrite: bool = False,
) -> float:
    """Estimate noise standard deviation robustly.

    Parameters
    ----------
    residual : np.ndarray
        Residual signal (baseline-corrected).
    method : str
        "mad" for Median Absolute Deviation (robust to outliers),
        "std" for standard deviation.
    overwrite : bool
        Allow *residual* to be reordered and overwritten in place (pass a
        temporary array to avoid a full-length copy).

    Returns
    -------
    float
        Estimated noise standard deviation.
    """
    finite = np.isfinite(residual)
    if not finite.all():
        x = residual[finite]  # boolean indexing copies
    elif overwrite:
        x = residual
    else:
        x = residual.copy()
    del finite
    if len(x) == 0:
        return 1.0  # Fallback

    if method == "mad":
        # MAD-based robust standard deviation estimator
        # For Gaussian data, std ≈ 1.4826 * MAD.  Medians are taken in place
        # on the (owned) working array to avoid further full-length copies.
        med = _median_inplace(x)
        np.subtract(x, x.dtype.type(med), out=x)
        np.abs(x, out=x)
        mad = _median_inplace(x)
        return float(mad * 1.4826)
    elif method == "std":
        return float(np.std(x, dtype=np.float64))
    else:
        raise ValueError(f"Unknown noise estimation method: {method}")


def _median_inplace(x: np.ndarray) -> float:
    """Median of *x* (same definition as ``np.median``), reordering *x*."""
    n = len(x)
    k = n // 2
    if n % 2:
        x.partition(k)
        return float(x[k])
    x.partition([k - 1, k])
    return (float(x[k - 1]) + float(x[k])) / 2.0
