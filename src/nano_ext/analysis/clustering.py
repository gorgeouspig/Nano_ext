"""Event population clustering with a Dirichlet-process Gaussian mixture.

Mixtures of analytes (or of binding states of one analyte) produce several
populations of events that differ in blockade depth, dwell time and noise.
:func:`cluster_events` fits a truncated Dirichlet-process GMM to per-event
features, so the number of populations is inferred rather than chosen, and
every event gets a posterior probability of belonging to its population.

Features are z-scored before fitting; dwell times are log-transformed because
their distribution is heavy-tailed (roughly exponential).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence

import numpy as np
from sklearn.mixture import BayesianGaussianMixture

from nano_ext.models import Event

_FEATURES: dict[str, Callable[[Event], float]] = {
    "relative_depth": lambda ev: ev.relative_depth,
    "depth": lambda ev: ev.depth,
    "log_duration": lambda ev: np.log10(max(ev.duration, 1e-12)),
    "duration": lambda ev: ev.duration,
    "std_current": lambda ev: ev.std_current,
    "mean_current": lambda ev: ev.mean_current,
    "log_abs_area": lambda ev: np.log10(max(abs(ev.area), 1e-30)),
    "n_levels": lambda ev: float(ev.n_levels),
}

DEFAULT_FEATURES = ("relative_depth", "log_duration")


@dataclass
class EventClusterResult:
    """Result of :func:`cluster_events`.

    Attributes
    ----------
    n_clusters : int
        Number of populations with at least ``min_count`` expected events.
    labels : np.ndarray
        Population index (0..n_clusters-1) of every event.  Populations are
        ordered by decreasing size.
    probabilities : np.ndarray
        ``(n_events, n_clusters)`` posterior membership probabilities.
    weights : np.ndarray
        Mixture weight of each population (sums to 1).
    means : np.ndarray
        ``(n_clusters, n_features)`` population means in original feature
        units.
    covariances : np.ndarray
        ``(n_clusters, n_features, n_features)`` population covariances in
        original feature units.
    counts : np.ndarray
        Number of events hard-assigned to each population.
    feature_names : tuple[str, ...]
        Features used, in column order.
    features : np.ndarray
        ``(n_events, n_features)`` feature matrix (original units).
    """

    n_clusters: int
    labels: np.ndarray
    probabilities: np.ndarray
    weights: np.ndarray
    means: np.ndarray
    covariances: np.ndarray
    counts: np.ndarray
    feature_names: tuple
    features: np.ndarray

    def summary_table(self):
        """One row per population: size, weight, and feature means / stds."""
        import pandas as pd

        rows = []
        for k in range(self.n_clusters):
            row = {
                "cluster_id": k,
                "n_events": int(self.counts[k]),
                "weight": float(self.weights[k]),
            }
            for j, name in enumerate(self.feature_names):
                row[f"{name}_mean"] = float(self.means[k, j])
                row[f"{name}_std"] = float(np.sqrt(self.covariances[k, j, j]))
            rows.append(row)
        return pd.DataFrame(rows)


def available_features() -> tuple[str, ...]:
    """Names accepted by the *features* argument of :func:`cluster_events`."""
    return tuple(_FEATURES)


def event_features(
    events: Sequence[Event],
    features: Sequence[str] = DEFAULT_FEATURES,
) -> np.ndarray:
    """Build the ``(n_events, n_features)`` feature matrix."""
    unknown = [f for f in features if f not in _FEATURES]
    if unknown:
        raise ValueError(
            f"Unknown feature(s) {unknown}. Available: {available_features()}"
        )
    return np.array(
        [[_FEATURES[f](ev) for f in features] for ev in events],
        dtype=np.float64,
    ).reshape(len(events), len(features))


def cluster_events(
    events: Sequence[Event],
    features: Sequence[str] = DEFAULT_FEATURES,
    max_clusters: int = 10,
    concentration: Optional[float] = None,
    min_count: float = 2.0,
    n_init: int = 3,
    seed: int = 0,
    assign: bool = True,
) -> EventClusterResult:
    """Cluster events into populations with a Dirichlet-process GMM.

    Parameters
    ----------
    events : sequence of Event
        Detected events (e.g. ``PipelineResult.events``).
    features : sequence of str
        Per-event features to cluster on; see :func:`available_features`.
        Default: relative blockade depth and log10 dwell time.
    max_clusters : int
        Truncation level (upper bound on the number of populations).
    concentration : float, optional
        Dirichlet-process concentration.  Smaller values favour fewer
        populations.  ``None`` uses ``1 / max_clusters``.
    min_count : float
        Components whose expected number of events is below this are
        treated as empty and dropped.
    n_init : int
        Number of variational restarts (best lower bound is kept).
    seed : int
        Random seed.
    assign : bool
        If True, set ``cluster_id`` and ``cluster_prob`` on every event.

    Returns
    -------
    EventClusterResult
    """
    features = tuple(features)
    X = event_features(events, features)
    n, d = X.shape

    if n == 0:
        return EventClusterResult(
            n_clusters=0,
            labels=np.zeros(0, dtype=int),
            probabilities=np.zeros((0, 0)),
            weights=np.zeros(0),
            means=np.zeros((0, d)),
            covariances=np.zeros((0, d, d)),
            counts=np.zeros(0, dtype=int),
            feature_names=features,
            features=X,
        )

    if n < 3:
        # Too few events to say anything about populations.
        probs = np.ones((n, 1))
        labels = np.zeros(n, dtype=int)
        cov = np.cov(X, rowvar=False).reshape(d, d) if n > 1 else np.zeros((d, d))
        result = EventClusterResult(
            n_clusters=1,
            labels=labels,
            probabilities=probs,
            weights=np.ones(1),
            means=X.mean(axis=0, keepdims=True),
            covariances=cov[None, :, :],
            counts=np.array([n]),
            feature_names=features,
            features=X,
        )
        if assign:
            _assign(events, result)
        return result

    center = X.mean(axis=0)
    scale = X.std(axis=0)
    scale[scale == 0] = 1.0
    Z = (X - center) / scale

    model = BayesianGaussianMixture(
        n_components=int(min(max_clusters, n)),
        covariance_type="full",
        weight_concentration_prior_type="dirichlet_process",
        weight_concentration_prior=concentration,
        max_iter=1000,
        n_init=n_init,
        init_params="k-means++",
        random_state=seed,
    )
    model.fit(Z)

    resp = model.predict_proba(Z)
    active = np.flatnonzero(model.weights_ * n >= min_count)
    if len(active) == 0:
        active = np.array([int(np.argmax(model.weights_))])
    # Order populations by decreasing weight so cluster 0 is the largest.
    active = active[np.argsort(-model.weights_[active])]

    probs = resp[:, active]
    probs = probs / probs.sum(axis=1, keepdims=True)
    labels = np.argmax(probs, axis=1)

    weights = model.weights_[active] / model.weights_[active].sum()
    means = model.means_[active] * scale + center
    covs = model.covariances_[active] * np.outer(scale, scale)[None, :, :]
    counts = np.bincount(labels, minlength=len(active))

    result = EventClusterResult(
        n_clusters=len(active),
        labels=labels,
        probabilities=probs,
        weights=weights,
        means=means,
        covariances=covs,
        counts=counts,
        feature_names=features,
        features=X,
    )
    if assign:
        _assign(events, result)
    return result


def _assign(events: Sequence[Event], result: EventClusterResult) -> None:
    for i, ev in enumerate(events):
        ev.cluster_id = int(result.labels[i])
        ev.cluster_prob = float(result.probabilities[i, result.labels[i]])
