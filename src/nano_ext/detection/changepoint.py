"""Change point detection using Pruned Exact Linear Time (PELT) algorithm.

This function detects change points by minimizing a cost function plus a penalty
for each change point. The cost function is based on the negative log-likelihood
of a Gaussian model.

This is used to identify level transitions within multi-level
(stepwise) nanopore events.
"""

from __future__ import annotations

import numpy as np
from nano_ext._nano_ext import nano_ext_core


def binary_segmentation_bic(
    signal: np.ndarray,
    min_segment_samples: int = 50,
    penalty_factor: float = 1.5,
) -> list[int]:
    """Detect change points using the PELT algorithm with a Gaussian cost function.

    The algorithm minimizes the sum of segment costs (negative log-likelihood)
    plus a penalty for each change point. The penalty is controlled by the
    penalty_factor parameter.

    Parameters
    ----------
    signal : np.ndarray
        1-D signal segment to analyze (e.g., current within one event).
    min_segment_samples : int
        Minimum number of samples in each segment. Prevents over-segmentation
        on short/noisy data.
    penalty_factor : float
        Multiplier for the penalty term. Values > 1 increase the penalty
        (fewer change points); values < 1 decrease the penalty (more change points).

    Returns
    -------
    list[int]
        Sorted list of change point indices (relative to the input signal).
        Empty if no change points are found.
    """
    # Call the Rust PELT implementation
    change_points = nano_ext_core.pelt(signal.tolist(), penalty_factor, min_segment_samples)
    # The Rust function returns indices in increasing order, so we can return directly
    return change_points


def segment_signal(
    signal: np.ndarray,
    changepoints: list[int],
) -> list[dict]:
    """Split signal at change points and compute per-segment statistics.

    Parameters
    ----------
    signal : np.ndarray
        Input signal.
    changepoints : list[int]
        Sorted change point indices.

    Returns
    -------
    list[dict]
        List of segment dictionaries with keys:
        - "start": start index
        - "end": end index (exclusive)
        - "mean": segment mean
        - "std": segment standard deviation
        - "n_samples": number of samples
    """
    boundaries = [0] + sorted(changepoints) + [len(signal)]
    segments = []

    for i in range(len(boundaries) - 1):
        s = boundaries[i]
        e = boundaries[i + 1]
        seg = signal[s:e]
        segments.append({
            "start": s,
            "end": e,
            "mean": float(np.mean(seg)),
            "std": float(np.std(seg)),
            "n_samples": e - s,
        })

    return segments