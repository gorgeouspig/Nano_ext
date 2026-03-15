"""Low-pass filtering for nanopore current signals.

Provides Bessel and Butterworth low-pass filters suitable for
nanopore data. Bessel filters are preferred for their minimal
phase distortion, preserving event transition shapes.
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
) -> np.ndarray:
    """Apply a low-pass filter to the signal.

    Uses zero-phase (forward-backward) filtering via sosfiltfilt
    to avoid phase distortion.

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

    Returns
    -------
    np.ndarray
        Filtered signal (same length as input).

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

    filtered = sosfiltfilt(sos, signal)
    return filtered


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
