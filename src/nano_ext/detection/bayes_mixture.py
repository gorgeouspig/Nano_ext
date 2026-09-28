"""Dirichlet-process Gaussian mixture helpers.

A truncated Dirichlet-process Gaussian mixture (DPGMM), fitted by
variational inference, replaces the "fit k = 1..K and pick the minimum BIC"
loop with a single fit: the stick-breaking prior drives the weights of
unneeded components towards zero, so the number of *effective* components
is inferred from the data.

Components whose expected sample count falls below ``min_count`` are treated
as unused and pruned from the reported parameters.

With large samples of slightly non-Gaussian noise, variational inference
tends to keep several near-identical copies of one Gaussian (the posterior is
symmetric in them, so none is driven to zero).  Adjacent components whose
two-component mixture is unimodal are therefore merged into one cluster
(Hennig, 2010, "Methods for merging Gaussian mixture components"), so that one
reported component corresponds to one mode of the density.

Filtered nanopore traces are strongly autocorrelated, so neither BIC nor a
DPGMM should see every sample as an independent draw.  :func:`thinning_step`
returns a stride of roughly one sample per correlation time of the low-pass
filter; fitting on the thinned samples keeps the effective sample size honest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from sklearn.mixture import BayesianGaussianMixture


@dataclass
class DPGMMFit:
    """Result of a 1-D Dirichlet-process GMM fit.

    Attributes
    ----------
    model : BayesianGaussianMixture
        The fitted scikit-learn model (all truncation components).
    groups : list[np.ndarray]
        For each effective component, the indices of the ``model``
        components merged into it.  Components outside every group were
        pruned.  Groups are ordered by increasing mean.
    means, stds, weights : np.ndarray
        Moment-matched parameters of the effective components, in
        ``groups`` order.  ``weights`` are renormalised to sum to 1.
    lower_bound : float
        Final variational lower bound (ELBO).
    """

    model: BayesianGaussianMixture
    groups: list
    means: np.ndarray
    stds: np.ndarray
    weights: np.ndarray
    lower_bound: float

    @property
    def n_components(self) -> int:
        """Number of effective (non-pruned) components."""
        return len(self.groups)

    def predict(self, x: np.ndarray) -> np.ndarray:
        """Assign each sample to an effective component (0..n_components-1)."""
        resp = self.model.predict_proba(np.asarray(x, dtype=np.float64).reshape(-1, 1))
        grouped = np.column_stack([resp[:, g].sum(axis=1) for g in self.groups])
        return np.argmax(grouped, axis=1)


def thinning_step(
    sampling_rate: float,
    filter_cutoff: Optional[float],
) -> int:
    """Stride that leaves roughly independent samples after low-pass filtering.

    The autocorrelation of white noise low-passed at ``fc`` decays over
    ~``1 / (2 fc)`` seconds, i.e. ``sampling_rate / (2 fc)`` samples.
    """
    if filter_cutoff is None or filter_cutoff <= 0:
        return 1
    return max(1, int(sampling_rate / (2.0 * filter_cutoff)))


def fit_dpgmm_1d(
    x: np.ndarray,
    max_components: int = 10,
    concentration: Optional[float] = None,
    min_count: float = 5.0,
    min_weight: float = 0.0,
    n_init: int = 1,
    max_iter: int = 300,
    merge_unimodal: bool = True,
    seed: Optional[int] = 0,
) -> DPGMMFit:
    """Fit a truncated Dirichlet-process GMM to 1-D data.

    Parameters
    ----------
    x : np.ndarray
        1-D samples.
    max_components : int
        Truncation level (upper bound on the number of components).
    concentration : float, optional
        Dirichlet-process concentration ``alpha``.  Smaller values favour
        fewer components.  ``None`` uses ``1 / max_components`` (the
        scikit-learn default), which is conservative.
    min_count : float
        Components whose expected number of samples (``weight * n``) is
        below this are pruned.
    min_weight : float
        Components whose weight is below this are pruned as well.
    n_init : int
        Number of variational restarts (best ELBO is kept).
    max_iter : int
        Maximum variational iterations per restart.
    merge_unimodal : bool
        Merge adjacent components whose two-component mixture is unimodal.
    seed : int, optional
        Random seed.

    Returns
    -------
    DPGMMFit
    """
    x = np.asarray(x, dtype=np.float64).ravel()
    n = len(x)
    if n < 2:
        raise ValueError("fit_dpgmm_1d needs at least 2 samples.")

    k_max = int(max(1, min(max_components, n)))
    model = BayesianGaussianMixture(
        n_components=k_max,
        covariance_type="full",
        weight_concentration_prior_type="dirichlet_process",
        weight_concentration_prior=concentration,
        max_iter=max_iter,
        n_init=n_init,
        init_params="k-means++",
        random_state=seed,
    )
    model.fit(x.reshape(-1, 1))

    weights = model.weights_.ravel()
    keep = (weights * n >= min_count) & (weights >= min_weight)
    if not np.any(keep):
        keep = weights == weights.max()
    active = np.flatnonzero(keep)

    means = model.means_.ravel()
    variances = model.covariances_.ravel()
    order = active[np.argsort(means[active])]
    # Each cluster: [member indices, weight, mean, variance]
    clusters = [[np.array([i]), weights[i], means[i], variances[i]] for i in order]

    if merge_unimodal:
        merged = True
        while merged and len(clusters) > 1:
            merged = False
            for j in range(len(clusters) - 1):
                a, b = clusters[j], clusters[j + 1]
                if _is_unimodal(a[1], a[2], a[3], b[1], b[2], b[3]):
                    clusters[j] = _moment_match(a, b)
                    del clusters[j + 1]
                    merged = True
                    break

    w = np.array([c[1] for c in clusters])
    return DPGMMFit(
        model=model,
        groups=[c[0] for c in clusters],
        means=np.array([c[2] for c in clusters]),
        stds=np.sqrt(np.array([c[3] for c in clusters])),
        weights=w / w.sum(),
        lower_bound=float(model.lower_bound_),
    )


def _is_unimodal(
    w1: float, m1: float, v1: float,
    w2: float, m2: float, v2: float,
    n_grid: int = 200,
) -> bool:
    """True if ``w1 N(m1, v1) + w2 N(m2, v2)`` has no dip between the means."""
    if m1 == m2:
        return True
    grid = np.linspace(m1, m2, n_grid)
    dens = (
        w1 * np.exp(-0.5 * (grid - m1) ** 2 / v1) / np.sqrt(v1)
        + w2 * np.exp(-0.5 * (grid - m2) ** 2 / v2) / np.sqrt(v2)
    )
    # A dip is an interior point lower than both ends by more than rounding.
    return bool(dens.min() >= min(dens[0], dens[-1]) * (1.0 - 1e-6))


def _moment_match(a: list, b: list) -> list:
    """Merge two weighted Gaussians into one with the same first two moments."""
    w = a[1] + b[1]
    m = (a[1] * a[2] + b[1] * b[2]) / w
    v = (a[1] * (a[3] + (a[2] - m) ** 2) + b[1] * (b[3] + (b[2] - m) ** 2)) / w
    return [np.concatenate([a[0], b[0]]), w, m, v]
