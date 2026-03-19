"""Change point detection using Pruned Exact Linear Time (PELT) algorithm.

This function detects change points by minimizing a cost function plus a penalty
for each change point. The cost function is based on the negative log-likelihood
of a Gaussian model.

This is used to identify level transitions within multi-level
(stepwise) nanopore events.
"""

from __future__ import annotations

import numpy as np
from nano_ext import pelt

def binary_segmentation_bic(
    signal: np.ndarray,
    min_segment_samples: int = 50,
    penalty_factor: float = 1.5,
) -> list[int]:
    # ... (omitted) ...
    # Call the Rust PELT implementation
    change_points = pelt(signal.tolist(), penalty_factor, min_segment_samples)

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