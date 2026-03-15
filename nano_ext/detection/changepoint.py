"""Change point detection using Binary Segmentation with BIC.

Recursively splits a signal segment at the point that maximizes the
improvement in BIC, stopping when no further split improves the model.
This is used to identify level transitions within multi-level
(stepwise) nanopore events.
"""

from __future__ import annotations

import numpy as np


def binary_segmentation_bic(
    signal: np.ndarray,
    min_segment_samples: int = 50,
    penalty_factor: float = 1.5,
) -> list[int]:
    """Detect change points using recursive binary segmentation with BIC.

    At each recursion, tests whether splitting the segment into two
    sub-segments (each modelled as Gaussian with its own mean and
    variance) improves the BIC compared to a single-segment model.

    Parameters
    ----------
    signal : np.ndarray
        1-D signal segment to analyze (e.g., current within one event).
    min_segment_samples : int
        Minimum number of samples in each sub-segment after a split.
        Prevents over-segmentation on short/noisy data.
    penalty_factor : float
        Multiplier for the BIC penalty term. Values > 1 make the
        criterion more conservative (fewer splits); values < 1 are
        more permissive.

    Returns
    -------
    list[int]
        Sorted list of change point indices (relative to the input
        signal). Empty if no change points are found.
    """
    changepoints: list[int] = []
    _binseg_recurse(signal, 0, len(signal), min_segment_samples,
                    penalty_factor, changepoints)
    changepoints.sort()
    return changepoints


def _binseg_recurse(
    signal: np.ndarray,
    start: int,
    end: int,
    min_seg: int,
    penalty_factor: float,
    result: list[int],
) -> None:
    """Recursive worker for binary segmentation.

    Parameters
    ----------
    signal : np.ndarray
        Full signal array.
    start, end : int
        Current segment boundaries [start, end).
    min_seg : int
        Minimum sub-segment length.
    penalty_factor : float
        BIC penalty multiplier.
    result : list[int]
        Accumulator for detected change points.
    """
    n = end - start
    if n < 2 * min_seg:
        return  # Segment too short to split

    seg = signal[start:end]

    # --- H0: single-segment model ---
    var_all = np.var(seg)
    if var_all < 1e-20:
        return  # Constant segment, no change points

    # BIC for H0: N * log(var) + k * log(N), k=2 (mean + var)
    log_n = np.log(n)
    bic_h0 = n * np.log(var_all) + 2.0 * penalty_factor * log_n

    # --- Search for the best split point ---
    best_t = -1
    best_bic_h1 = bic_h0  # Must beat H0

    # Precompute cumulative sums for O(1) mean/variance per candidate
    cumsum = np.cumsum(seg)
    cumsum2 = np.cumsum(seg ** 2)

    for t in range(min_seg, n - min_seg):
        # Left segment [0, t): mean, var
        n_left = t
        sum_left = cumsum[t - 1]
        sum2_left = cumsum2[t - 1]
        mean_left = sum_left / n_left
        var_left = sum2_left / n_left - mean_left ** 2
        if var_left < 1e-20:
            var_left = 1e-20

        # Right segment [t, n): mean, var
        n_right = n - t
        sum_right = cumsum[n - 1] - cumsum[t - 1]
        sum2_right = cumsum2[n - 1] - cumsum2[t - 1]
        mean_right = sum_right / n_right
        var_right = sum2_right / n_right - mean_right ** 2
        if var_right < 1e-20:
            var_right = 1e-20

        # BIC for H1: sum of per-segment costs + penalty for 4 params
        bic_h1 = (
            n_left * np.log(var_left)
            + n_right * np.log(var_right)
            + 4.0 * penalty_factor * log_n
        )

        if bic_h1 < best_bic_h1:
            best_bic_h1 = bic_h1
            best_t = t

    if best_t < 0:
        return  # No split improves BIC

    # Record change point (in global coordinates)
    cp = start + best_t
    result.append(cp)

    # Recurse on left and right sub-segments
    _binseg_recurse(signal, start, cp, min_seg, penalty_factor, result)
    _binseg_recurse(signal, cp, end, min_seg, penalty_factor, result)


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
