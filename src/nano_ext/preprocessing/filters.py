"""Low-pass filtering for nanopore current signals.

This module provides implementations of Bessel and Butterworth low-pass filters
specifically designed for nanopore current signal processing. The filters use
zero-phase (forward-backward) filtering to avoid phase distortion, which is
critical for preserving the true shape of nanopore events.

Bessel filters are recommended for nanopore data due to their maximally flat
group delay, which minimizes distortion of event transition shapes. Butterworth
filters provide a sharper rolloff but introduce more phase distortion.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from scipy.signal import bessel, butter, sosfiltfilt


def lowpass_filter(
    signal: np.ndarray,
    sampling_rate: float,
    cutoff: Optional[float] = None,
    filter_type: str = "bessel",
    order: int = 4,
    chunk_samples: int = 1 << 22,
) -> np.ndarray:
    """Apply a low-pass filter to the signal.

    Uses zero-phase (forward-backward) filtering via sosfiltfilt
    to avoid phase distortion. Applies symmetric padding to reduce edge effects.

    Parameters
    ----------
    signal : np.ndarray
        Input signal (1-D array).
    sampling_rate : float
        Sampling rate in Hz.
    cutoff : float, optional
        Filter cutoff frequency in Hz. If None, defaults to
        sampling_rate / 10 (a conservative default).
    filter_type : str
        Filter type: "bessel" (recommended for nanopore data,
        minimal phase distortion) or "butterworth" (sharper rolloff).
    order : int
        Filter order. Default 4.
    chunk_samples : int
        Signals longer than this are filtered in overlapping chunks to bound
        the float64 working memory (the result matches a single pass to
        rounding error).

    Returns
    -------
    np.ndarray
        Filtered signal (same length as input); float32 for float32 input,
        float64 otherwise.

    Raises
    ------
    ValueError
        If filter_type is unknown or cutoff is invalid.
    """
    if cutoff is None:
        cutoff = sampling_rate / 10.0

    nyquist = sampling_rate / 2.0

    if cutoff >= nyquist:
        raise ValueError(
            f"Cutoff frequency ({cutoff} Hz) must be less than "
            f"Nyquist frequency ({nyquist} Hz)."
        )
    if cutoff <= 0:
        raise ValueError(f"Cutoff frequency must be positive, got {cutoff}")

    # Normalized frequency for scipy (0 to 1, where 1 = Nyquist)
    wn = cutoff / nyquist

    if filter_type == "bessel":
        sos = bessel(order, wn, btype="low", output="sos")
    elif filter_type == "butterworth":
        sos = butter(order, wn, btype="low", output="sos")
    else:
        raise ValueError(
            f"Unknown filter_type: {filter_type}. "
            "Use 'bessel' or 'butterworth'."
        )

    # --- Padding to reduce edge effects ---
    # Pad with 0.1 seconds of signal (reflected) on each side, but not more than half the signal
    pad_duration_sec = 0.1  # seconds
    pad_samples = int(pad_duration_sec * sampling_rate)
    # Limit padding to avoid excessive padding on very short signals
    max_pad = len(signal) // 2
    if pad_samples > max_pad:
        pad_samples = max_pad

    out_dtype = np.float32 if signal.dtype == np.float32 else np.float64
    n = len(signal)

    if n <= chunk_samples:
        return _filtfilt_padded(sos, signal, pad_samples).astype(out_dtype, copy=False)

    # Long signals: filter in chunks, each extended by `pad_samples` of the
    # neighbouring signal (reflection only at the true ends), and keep the
    # chunk's own span.  The padding is far longer than the filter's impulse
    # response, so the result matches a single full-length pass to rounding
    # error while keeping the float64 working set to a few chunks.
    filtered = np.empty(n, dtype=out_dtype)
    for start in range(0, n, chunk_samples):
        end = min(n, start + chunk_samples)
        a = max(0, start - pad_samples)
        b = min(n, end + pad_samples)
        seg = np.asarray(signal[a:b], dtype=np.float64)
        left = pad_samples - (start - a)   # reflection needed at the start
        right = pad_samples - (b - end)    # reflection needed at the end
        if left > 0 or right > 0:
            seg = np.pad(seg, (left, right), mode="reflect")
        out = sosfiltfilt(sos, seg)
        keep_from = left + (start - a)
        filtered[start:end] = out[keep_from:keep_from + (end - start)]
    return filtered


def _filtfilt_padded(sos: np.ndarray, signal: np.ndarray, pad_samples: int) -> np.ndarray:
    """Zero-phase filtering with reflection padding of `pad_samples` per side."""
    if pad_samples > 0:
        # Use reflection padding at the edges
        padded_signal = np.pad(signal, (pad_samples, pad_samples), mode='reflect')
    else:
        padded_signal = signal

    # Apply zero-phase filtering
    filtered_padded = sosfiltfilt(sos, padded_signal)

    # Remove padding
    if pad_samples > 0:
        return filtered_padded[pad_samples:-pad_samples]
    return filtered_padded


def auto_cutoff(sampling_rate: float, factor: float = 10.0) -> float:
    """Compute a default cutoff frequency from the sampling rate.

    Parameters
    ----------
    sampling_rate : float
        Sampling rate in Hz.
    factor : float
        Division factor. Default 10 means cutoff = fs/10.

    Returns
    -------
    float
        Recommended cutoff frequency in Hz.
    """
    return sampling_rate / factor
