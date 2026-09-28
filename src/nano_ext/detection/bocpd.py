"""Bayesian online change-point detection (BOCPD).

Implements Adams & MacKay (2007), "Bayesian Online Changepoint Detection",
for piecewise-constant Gaussian signals with unknown mean and variance per
segment (Normal-Inverse-Gamma conjugate prior, Student-t predictive).

Instead of a penalty per change point (as in PELT / binary segmentation),
the prior is a constant *hazard rate* — the prior probability that a new
segment starts at any given sample — and the algorithm returns the full
posterior over the current run length at every sample.

Run lengths whose posterior mass falls below ``prune_tol`` are dropped, so
the cost per sample stays roughly constant instead of growing with time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.special import gammaln, logsumexp


@dataclass
class BOCPDResult:
    """Output of :func:`bocpd`.

    Attributes
    ----------
    changepoints : list[int]
        Sorted indices at which a new segment starts (the maximum a
        posteriori segmentation, recovered by back-tracking the MAP run
        length from the end of the signal).
    map_run_length : np.ndarray
        MAP run length after each sample: the number of samples (including
        the current one) since the most recent change point.
    """

    changepoints: list
    map_run_length: np.ndarray


def robust_noise_std(x: np.ndarray) -> float:
    """Noise standard deviation from the MAD of first differences.

    Level changes affect only a few differences, so this is insensitive to
    the piecewise-constant structure (assuming roughly uncorrelated noise).
    """
    x = np.asarray(x, dtype=np.float64)
    if len(x) < 3:
        return float(np.std(x)) or 1.0
    d = np.diff(x)
    sigma = float(np.median(np.abs(d - np.median(d))) / 0.6745 / np.sqrt(2.0))
    return sigma if sigma > 0 else (float(np.std(x)) or 1.0)


def bocpd(
    x: np.ndarray,
    hazard: float,
    mu0: Optional[float] = None,
    kappa0: float = 0.01,
    alpha0: float = 1.0,
    beta0: Optional[float] = None,
    prune_tol: float = 1e-10,
    max_run_lengths: int = 500,
    min_segment_length: int = 10,
) -> BOCPDResult:
    """Run BOCPD on a 1-D signal.

    Parameters
    ----------
    x : np.ndarray
        Signal samples (ideally decorrelated, e.g. thinned to about one
        sample per filter correlation time).
    hazard : float
        Prior probability that a change point occurs at each sample
        (``1 / expected_segment_length``).
    mu0 : float, optional
        Prior mean of a segment level.  Defaults to the median of *x*.
    kappa0 : float
        Prior pseudo-count for the mean.  Small values make the level
        prior vague.
    alpha0 : float
        Prior shape for the segment variance (``alpha0 = 1`` gives a
        heavy-tailed Student-t predictive with 2 degrees of freedom).
    beta0 : float, optional
        Prior scale for the segment variance.  Defaults to
        ``alpha0 * robust_noise_std(x) ** 2`` so the prior mean variance
        matches the noise.
    prune_tol : float
        Run lengths with posterior probability below this are dropped.
    max_run_lengths : int
        Hard cap on the number of run-length hypotheses kept per step.
    min_segment_length : int
        Segments shorter than this are merged into the neighbour with the
        closer mean.  The MAP run length can hesitate for a few samples
        right after a step, which otherwise leaves a sliver segment next to
        the true change point.

    Returns
    -------
    BOCPDResult
    """
    x = np.asarray(x, dtype=np.float64).ravel()
    n = len(x)
    if not 0.0 < hazard < 1.0:
        raise ValueError("hazard must be in (0, 1).")
    if n == 0:
        return BOCPDResult(changepoints=[], map_run_length=np.zeros(0, dtype=int))

    if mu0 is None:
        mu0 = float(np.median(x))
    if beta0 is None:
        beta0 = alpha0 * robust_noise_std(x) ** 2

    log_h = np.log(hazard)
    log_1mh = np.log1p(-hazard)
    log_prune = np.log(prune_tol)

    # Hypotheses: run length r, log joint prob, and posterior NIG parameters
    # after observing the r most recent samples.  Start with r = 0 (prior).
    rl = np.array([0])
    log_r = np.array([0.0])
    mu = np.array([mu0])
    kappa = np.array([kappa0])
    alpha = np.array([alpha0])
    beta = np.array([beta0])

    map_rl = np.empty(n, dtype=int)

    for t in range(n):
        xt = x[t]
        # Student-t predictive log-density under each hypothesis
        df = 2.0 * alpha
        scale2 = beta * (kappa + 1.0) / (alpha * kappa)
        z2 = (xt - mu) ** 2 / scale2
        log_pred = (
            gammaln(0.5 * (df + 1.0)) - gammaln(0.5 * df)
            - 0.5 * np.log(np.pi * df * scale2)
            - 0.5 * (df + 1.0) * np.log1p(z2 / df)
        )

        joint = log_r + log_pred
        growth = joint + log_1mh
        change = logsumexp(joint) + log_h

        # New hypotheses: a change point (x_t starts a new run of length 1)
        # plus every existing run grown by one sample.
        new_log_r = np.concatenate(([change], growth))
        new_rl = np.concatenate(([1], rl + 1))

        # Update sufficient statistics with x_t.  The change-point entry is
        # the prior updated with x_t alone.
        mu_prev = np.concatenate(([mu0], mu))
        kappa_prev = np.concatenate(([kappa0], kappa))
        alpha_prev = np.concatenate(([alpha0], alpha))
        beta_prev = np.concatenate(([beta0], beta))

        kappa = kappa_prev + 1.0
        mu = (kappa_prev * mu_prev + xt) / kappa
        alpha = alpha_prev + 0.5
        beta = beta_prev + kappa_prev * (xt - mu_prev) ** 2 / (2.0 * kappa)

        # Normalise to a posterior and prune negligible hypotheses.
        new_log_r = new_log_r - logsumexp(new_log_r)
        keep = new_log_r > log_prune
        if keep.sum() > max_run_lengths:
            keep = np.zeros_like(keep)
            keep[np.argsort(new_log_r)[-max_run_lengths:]] = True
        rl = new_rl[keep]
        log_r = new_log_r[keep]
        mu, kappa, alpha, beta = mu[keep], kappa[keep], alpha[keep], beta[keep]

        map_rl[t] = rl[np.argmax(log_r)]

    # Back-track the MAP segmentation: at the end of each segment the run
    # length posterior has seen the whole segment.
    changepoints: list[int] = []
    t = n - 1
    while t > 0:
        start = t - int(map_rl[t]) + 1
        if start <= 0:
            break
        changepoints.append(start)
        t = start - 1

    changepoints = _merge_short(x, sorted(changepoints), min_segment_length)
    return BOCPDResult(changepoints=changepoints, map_run_length=map_rl)


def _merge_short(x: np.ndarray, changepoints: list, min_len: int) -> list:
    """Remove change points bounding segments shorter than *min_len*."""
    cps = list(changepoints)
    while cps:
        bounds = [0] + cps + [len(x)]
        lengths = np.diff(bounds)
        i = int(np.argmin(lengths))
        if lengths[i] >= min_len:
            break
        if i == 0:
            cps.pop(0)
        elif i == len(lengths) - 1:
            cps.pop(-1)
        else:
            m = x[bounds[i]:bounds[i + 1]].mean()
            left = abs(m - x[bounds[i - 1]:bounds[i]].mean())
            right = abs(m - x[bounds[i + 1]:bounds[i + 2]].mean())
            # Merging into a neighbour removes the boundary shared with it.
            cps.pop(i - 1 if left <= right else i)
    return cps
