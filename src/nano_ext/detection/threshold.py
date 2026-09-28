"""BIC-based threshold determination using Gaussian Mixture Models.

This module implements an objective method for determining nanopore event
detection thresholds using Gaussian Mixture Models (GMM) and the Bayesian
Information Criterion (BIC). The approach fits multiple GMMs with varying
numbers of components to the baseline-corrected current distribution and
selects the optimal model by minimizing the information criterion (BIC or AIC).

The detection threshold is computed as the decision boundary between the
identified baseline component and the nearest event-related component in the
GMM. This provides an objective, data-driven approach to threshold selection
that adapts to the specific noise and signal characteristics of each dataset.

Key features:
- Automatic selection of optimal number of GMM components via BIC/AIC
- Robust baseline component identification based on proximity to zero in residual space
- Computation of decision boundaries between Gaussian components
- Support for both BIC (preferred) and AIC criteria
- Optional Dirichlet-process GMM (``method="dpgmm"``): a single variational
  fit whose stick-breaking prior prunes unneeded components, replacing the
  k = 1..K loop
- Efficient handling of large signals through intelligent subsampling
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from sklearn.mixture import GaussianMixture

from nano_ext.models import ThresholdResult

# The variational DPGMM costs ~5x a single EM fit per sample; its posterior
# over the number of components is already sharp at this many samples.
_DPGMM_MAX_SAMPLES = 20_000


def determine_threshold(
    residual: np.ndarray,
    max_components: int = 10,
    criterion: str = "bic",
    max_samples_for_fit: int = 500_000,
    seed: Optional[int] = None,
    method: str = "gmm",
    dp_concentration: Optional[float] = None,
) -> ThresholdResult:
    """Determine event detection threshold using GMM + BIC.

    Fits Gaussian Mixture Models with k=1..max_components to the
    residual (baseline-corrected) current distribution and selects the
    optimal number of components by minimizing BIC (or AIC).

    The threshold is set at the decision boundary between the baseline
    component and the nearest event component.

    Parameters
    ----------
    residual : np.ndarray
        Baseline-corrected signal. Baseline samples should cluster
        around 0, and event samples should be negative (for blockades).
    max_components : int
        Maximum number of GMM components to try.
    criterion : str
        Information criterion: "bic" or "aic".
    max_samples_for_fit : int
        Maximum number of samples for GMM fitting. If the signal is
        longer, a random subsample is used.
    seed : int, optional
        Random seed for reproducibility.
    method : str
        ``"gmm"`` (fit k = 1..max_components, select by *criterion*) or
        ``"dpgmm"`` (single Dirichlet-process GMM fit truncated at
        *max_components*, on at most 20 000 samples; *criterion* is
        ignored).
    dp_concentration : float, optional
        Dirichlet-process concentration for ``method="dpgmm"``.  ``None``
        uses ``1 / max_components``.

    Returns
    -------
    ThresholdResult
        Threshold value, GMM parameters, and BIC scores (empty for
        ``method="dpgmm"``).
    """
    if method not in ("gmm", "dpgmm"):
        raise ValueError(f"Unknown method: {method}. Use 'gmm' or 'dpgmm'.")
    rng = np.random.default_rng(seed)

    # Subsample for efficiency if needed
    data = residual.copy()
    if method == "dpgmm":
        max_samples_for_fit = min(max_samples_for_fit, _DPGMM_MAX_SAMPLES)
    if len(data) > max_samples_for_fit:
        indices = rng.choice(len(data), max_samples_for_fit, replace=False)
        data = data[indices]

    if method == "dpgmm":
        return _determine_threshold_dpgmm(data, max_components, dp_concentration, seed)

    X = data.reshape(-1, 1)

    # Fit GMMs with k=1..max_components
    scores = []
    models = []
    for k in range(1, max_components + 1):
        gmm = GaussianMixture(
            n_components=k,
            covariance_type="full",
            max_iter=200,
            n_init=3,
            random_state=seed,
        )
        gmm.fit(X)
        if criterion == "bic":
            scores.append(gmm.bic(X))
        elif criterion == "aic":
            scores.append(gmm.aic(X))
        else:
            raise ValueError(f"Unknown criterion: {criterion}. Use 'bic' or 'aic'.")
        models.append(gmm)

    # Select optimal model (minimum score)
    k_optimal_idx = int(np.argmin(scores))
    best_gmm = models[k_optimal_idx]
    k_optimal = k_optimal_idx + 1

    # Extract component parameters
    means = best_gmm.means_.flatten()
    stds = np.sqrt(best_gmm.covariances_.flatten())
    weights = best_gmm.weights_.flatten()

    # Identify baseline component:
    # The baseline is the component with the highest mean (for blockade
    # detection, baseline = highest current) AND significant weight.
    # In residual space, baseline should be near 0.
    baseline_idx = _identify_baseline_component(means, weights)

    # Compute threshold
    threshold = _compute_threshold(means, stds, weights, baseline_idx)

    return ThresholdResult(
        threshold=threshold,
        n_components=k_optimal,
        component_means=means,
        component_stds=stds,
        component_weights=weights,
        bic_scores=scores,
        baseline_component_idx=baseline_idx,
    )


def _determine_threshold_dpgmm(
    data: np.ndarray,
    max_components: int,
    concentration: Optional[float],
    seed: Optional[int],
) -> ThresholdResult:
    """Threshold from a single Dirichlet-process GMM fit."""
    from nano_ext.detection.bayes_mixture import fit_dpgmm_1d

    # Event components can be rare (a few % of samples), so prune only
    # components too small to estimate rather than by weight.
    fit = fit_dpgmm_1d(
        data,
        max_components=max_components,
        concentration=concentration,
        min_count=max(10.0, 1e-4 * len(data)),
        seed=0 if seed is None else seed,
    )
    baseline_idx = _identify_baseline_component(fit.means, fit.weights)
    threshold = _compute_threshold(fit.means, fit.stds, fit.weights, baseline_idx)

    return ThresholdResult(
        threshold=threshold,
        n_components=fit.n_components,
        component_means=fit.means,
        component_stds=fit.stds,
        component_weights=fit.weights,
        bic_scores=[],
        baseline_component_idx=baseline_idx,
    )


def _identify_baseline_component(
    means: np.ndarray,
    weights: np.ndarray,
    min_weight: float = 0.05,
) -> int:
    """Identify which GMM component corresponds to the baseline.

    In residual (baseline-corrected) space, the baseline component
    should have a mean closest to 0 and a significant weight.

    Parameters
    ----------
    means : np.ndarray
        Component means.
    weights : np.ndarray
        Component weights.
    min_weight : float
        Minimum weight for a component to be considered as baseline.

    Returns
    -------
    int
        Index of the baseline component.
    """
    # Filter by minimum weight
    candidates = np.where(weights >= min_weight)[0]
    if len(candidates) == 0:
        candidates = np.arange(len(means))

    # Baseline = component with mean closest to 0 (in residual space)
    abs_means = np.abs(means[candidates])
    best = candidates[np.argmin(abs_means)]
    return int(best)


def _compute_threshold(
    means: np.ndarray,
    stds: np.ndarray,
    weights: np.ndarray,
    baseline_idx: int,
) -> float:
    """Compute the decision boundary between baseline and event components.

    For a single-component model, falls back to a sigma-based threshold.
    For multi-component models, computes the crossing point between the
    baseline Gaussian and the nearest non-baseline Gaussian.

    Parameters
    ----------
    means : np.ndarray
        Component means.
    stds : np.ndarray
        Component standard deviations.
    weights : np.ndarray
        Component weights.
    baseline_idx : int
        Index of the baseline component.

    Returns
    -------
    float
        Threshold value (points below this are considered events).
    """
    n_components = len(means)
    baseline_mean = means[baseline_idx]
    baseline_std = stds[baseline_idx]

    if n_components == 1:
        # Single component: use sigma-based threshold
        # 5 sigma below the mean
        return float(baseline_mean - 5.0 * baseline_std)

    # Find the non-baseline component closest to (and below) the baseline
    # In residual space: events are negative, baseline is near 0
    event_indices = [i for i in range(n_components) if i != baseline_idx]

    if not event_indices:
        return float(baseline_mean - 5.0 * baseline_std)

    # Find closest event component below the baseline
    below_baseline = [
        i for i in event_indices if means[i] < baseline_mean
    ]

    if below_baseline:
        # Use the closest component below baseline
        closest_idx = max(below_baseline, key=lambda i: means[i])
    else:
        # All components above baseline (unusual) — use closest overall
        closest_idx = min(event_indices, key=lambda i: abs(means[i] - baseline_mean))

    # Compute Gaussian crossing point between baseline and closest event
    threshold = _gaussian_crossing(
        baseline_mean, baseline_std, weights[baseline_idx],
        means[closest_idx], stds[closest_idx], weights[closest_idx],
    )

    return threshold


def _gaussian_crossing(
    mu1: float, sigma1: float, w1: float,
    mu2: float, sigma2: float, w2: float,
) -> float:
    """Find the crossing point of two weighted Gaussians.

    Solves w1 * N(x; mu1, sigma1) = w2 * N(x; mu2, sigma2) for x.
    Returns the crossing point between mu1 and mu2.

    Parameters
    ----------
    mu1, sigma1, w1 : float
        Mean, std, weight of the first Gaussian (baseline).
    mu2, sigma2, w2 : float
        Mean, std, weight of the second Gaussian (event).

    Returns
    -------
    float
        Crossing point x.
    """
    # The equation w1*N(x;mu1,s1) = w2*N(x;mu2,s2) becomes a quadratic in x
    # when sigma1 != sigma2, or linear when sigma1 == sigma2.

    if abs(sigma1 - sigma2) < 1e-10:
        # Equal variances: linear solution
        if abs(mu1 - mu2) < 1e-10:
            return (mu1 + mu2) / 2.0
        x = (mu1 + mu2) / 2.0 + (sigma1 ** 2) / (mu1 - mu2) * np.log(w2 / w1)
        return float(x)

    # General case: quadratic equation
    # a*x^2 + b*x + c = 0
    s1sq = sigma1 ** 2
    s2sq = sigma2 ** 2

    a = 1.0 / (2.0 * s2sq) - 1.0 / (2.0 * s1sq)
    b = mu1 / s1sq - mu2 / s2sq
    c = (mu2 ** 2) / (2.0 * s2sq) - (mu1 ** 2) / (2.0 * s1sq) + np.log(
        (w1 * sigma2) / (w2 * sigma1)
    )

    discriminant = b ** 2 - 4.0 * a * c
    if discriminant < 0:
        # No real crossing — use midpoint
        return (mu1 + mu2) / 2.0

    sqrt_disc = np.sqrt(discriminant)
    x1 = (-b + sqrt_disc) / (2.0 * a)
    x2 = (-b - sqrt_disc) / (2.0 * a)

    # Return the root that lies between the two means
    lo, hi = min(mu1, mu2), max(mu1, mu2)
    candidates = []
    for x in [x1, x2]:
        if lo <= x <= hi:
            candidates.append(x)

    if candidates:
        # If multiple candidates between the means, pick the one closer to midpoint
        mid = (mu1 + mu2) / 2.0
        return float(min(candidates, key=lambda x: abs(x - mid)))

    # Fallback: return the root closest to the midpoint
    mid = (mu1 + mu2) / 2.0
    return float(min([x1, x2], key=lambda x: abs(x - mid)))
