"""Bayesian estimates of event-level statistics.

:func:`capture_rate_posterior`
    Poisson capture rate with a conjugate Gamma posterior (credible
    interval instead of a bare ``n / T``).
:func:`dwell_time_mixture`
    Mixture of exponentials for dwell times, sampled by Gibbs sampling with
    a sparse ("overfitted") Dirichlet prior on the weights: extra components
    empty out, so the posterior over the number of occupied components
    tells how many kinetic time constants the data support
    (Rousseau & Mengersen, 2011).  Dwell times are shifted by the shortest
    detectable duration; by memorylessness an exponential truncated at
    ``t_min`` is an exponential in ``t - t_min`` with the same rate.
:func:`summarize_event_statistics`
    Both of the above for a list of events, as a JSON-serialisable dict.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional, Sequence

import numpy as np
from scipy import stats


@dataclass
class RatePosterior:
    """Gamma posterior of a Poisson rate (events per second).

    Attributes
    ----------
    n_events : int
    observation_time : float
        Seconds of signal analysed.
    shape, rate : float
        Gamma posterior parameters (rate parameterisation).
    mean : float
        Posterior mean rate.
    lower, upper : float
        Equal-tailed credible interval.
    credibility : float
    """

    n_events: int
    observation_time: float
    shape: float
    rate: float
    mean: float
    lower: float
    upper: float
    credibility: float


def capture_rate_posterior(
    n_events: int,
    observation_time: float,
    prior_shape: float = 0.5,
    prior_rate: float = 0.0,
    credibility: float = 0.95,
) -> RatePosterior:
    """Posterior of the event (capture) rate for a Poisson process.

    Parameters
    ----------
    n_events : int
        Number of events observed.
    observation_time : float
        Observation time in seconds.
    prior_shape, prior_rate : float
        Gamma prior.  The default ``Gamma(0.5, 0)`` is the Jeffreys prior.
    credibility : float
        Mass of the equal-tailed credible interval.
    """
    if observation_time <= 0:
        raise ValueError("observation_time must be positive.")
    shape = prior_shape + n_events
    rate = prior_rate + observation_time
    tail = 0.5 * (1.0 - credibility)
    dist = stats.gamma(a=shape, scale=1.0 / rate)
    return RatePosterior(
        n_events=int(n_events),
        observation_time=float(observation_time),
        shape=float(shape),
        rate=float(rate),
        mean=float(shape / rate),
        lower=float(dist.ppf(tail)),
        upper=float(dist.ppf(1.0 - tail)),
        credibility=credibility,
    )


@dataclass
class DwellTimePosterior:
    """Posterior summary of a mixture-of-exponentials dwell-time model.

    Attributes
    ----------
    n_components : int
        Most probable number of occupied components.
    n_components_probs : dict[int, float]
        Posterior probability of each number of occupied components.
    tau_mean : np.ndarray
        Posterior mean time constants (s), ascending, for the draws with
        ``n_components`` occupied components.
    tau_lower, tau_upper : np.ndarray
        Equal-tailed credible intervals of the time constants.
    weight_mean : np.ndarray
        Posterior mean weight of each component (same order as tau).
    t_min : float
        Shift applied to the dwell times (s).
    n_events : int
    credibility : float
    """

    n_components: int
    n_components_probs: dict
    tau_mean: np.ndarray
    tau_lower: np.ndarray
    tau_upper: np.ndarray
    weight_mean: np.ndarray
    t_min: float
    n_events: int
    credibility: float


def dwell_time_mixture(
    durations: Sequence[float],
    t_min: Optional[float] = None,
    max_components: int = 4,
    weight_concentration: Optional[float] = None,
    n_iter: int = 3000,
    burn_in: int = 1000,
    min_occupancy: float = 0.02,
    credibility: float = 0.95,
    seed: int = 0,
) -> DwellTimePosterior:
    """Fit a mixture of exponentials to dwell times by Gibbs sampling.

    Parameters
    ----------
    durations : sequence of float
        Event (or state) dwell times in seconds.
    t_min : float, optional
        Shortest detectable dwell time; durations are shifted by it.
        Defaults to the minimum observed duration.
    max_components : int
        Number of components in the overfitted mixture.
    weight_concentration : float, optional
        Symmetric Dirichlet concentration on the weights.  Values well
        below 1/2 let superfluous components empty out.  ``None`` uses
        ``0.01`` (on synthetic data, 0.1 still split a single exponential
        into two components about 40 % of the time).
    n_iter, burn_in : int
        Gibbs iterations and how many initial draws to discard.
    min_occupancy : float
        A component counts as occupied in a draw when it holds at least
        this fraction of the events (and at least one event).
    credibility : float
        Mass of the reported credible intervals.
    seed : int
        Random seed.
    """
    t = np.asarray(durations, dtype=np.float64)
    t = t[np.isfinite(t) & (t > 0)]
    n = len(t)
    if n < 2:
        raise ValueError("dwell_time_mixture needs at least 2 positive durations.")
    if t_min is None:
        t_min = float(t.min())
    x = np.clip(t - t_min, 0.0, None)
    scale = float(x.mean()) if x.mean() > 0 else float(t.mean())

    K = int(max_components)
    alpha = 0.01 if weight_concentration is None else float(weight_concentration)
    # Gamma(a0, rate=b0) prior on each rate, centred on 1 / mean dwell and
    # broad (a0 = 1: exponential prior on the rate).
    a0, b0 = 1.0, scale

    rng = np.random.default_rng(seed)
    # Spread the initial rates over the range of the data.
    lam = 1.0 / (scale * np.logspace(-1, 1, K))
    w = np.full(K, 1.0 / K)

    kept_lam, kept_w, kept_occ = [], [], []
    for it in range(n_iter):
        # Assignments
        logp = np.log(w)[None, :] + np.log(lam)[None, :] - x[:, None] * lam[None, :]
        logp -= logp.max(axis=1, keepdims=True)
        p = np.exp(logp)
        p /= p.sum(axis=1, keepdims=True)
        z = (p.cumsum(axis=1) > rng.random(n)[:, None]).argmax(axis=1)

        counts = np.bincount(z, minlength=K)
        sums = np.bincount(z, weights=x, minlength=K)

        w = rng.dirichlet(alpha + counts)
        w = np.clip(w, 1e-300, None)
        lam = rng.gamma(a0 + counts, 1.0 / (b0 + sums))

        # Resolve label switching by ordering components by time constant.
        order = np.argsort(-lam)  # fastest rate = shortest tau first
        lam, w, counts = lam[order], w[order], counts[order]

        if it >= burn_in:
            occ = (counts >= max(1, min_occupancy * n))
            kept_lam.append(lam.copy())
            kept_w.append(w.copy())
            kept_occ.append(occ)

    kept_lam = np.array(kept_lam)
    kept_w = np.array(kept_w)
    kept_occ = np.array(kept_occ)
    n_occ = kept_occ.sum(axis=1)

    values, freq = np.unique(n_occ, return_counts=True)
    probs = {int(v): float(f / len(n_occ)) for v, f in zip(values, freq)}
    k_map = int(values[np.argmax(freq)])

    # Summarise the occupied components of the draws with k_map occupied.
    sel = n_occ == k_map
    taus = np.array([1.0 / kept_lam[i][kept_occ[i]] for i in np.flatnonzero(sel)])
    ws = np.array([kept_w[i][kept_occ[i]] for i in np.flatnonzero(sel)])
    ws = ws / ws.sum(axis=1, keepdims=True)
    order = np.argsort(taus.mean(axis=0))
    taus, ws = taus[:, order], ws[:, order]
    tail = 0.5 * (1.0 - credibility)

    return DwellTimePosterior(
        n_components=k_map,
        n_components_probs=probs,
        tau_mean=taus.mean(axis=0),
        tau_lower=np.quantile(taus, tail, axis=0),
        tau_upper=np.quantile(taus, 1.0 - tail, axis=0),
        weight_mean=ws.mean(axis=0),
        t_min=float(t_min),
        n_events=n,
        credibility=credibility,
    )


def summarize_event_statistics(
    events: Sequence,
    observation_time: float,
    credibility: float = 0.95,
    seed: int = 0,
) -> dict:
    """Capture rate and dwell-time posteriors, overall and per population.

    Per-population entries are added when the events carry ``cluster_id``
    (see :func:`nano_ext.analysis.clustering.cluster_events`).

    Returns
    -------
    dict
        JSON-serialisable summary.
    """

    def _block(evs) -> dict:
        out = {"capture_rate": asdict(capture_rate_posterior(
            len(evs), observation_time, credibility=credibility,
        ))}
        durations = [ev.duration for ev in evs]
        if len(durations) >= 5:
            dw = dwell_time_mixture(durations, credibility=credibility, seed=seed)
            out["dwell_time"] = _to_jsonable(asdict(dw))
        return out

    summary = {"all": _block(list(events))}
    ids = sorted({ev.cluster_id for ev in events if getattr(ev, "cluster_id", None) is not None})
    if ids:
        summary["clusters"] = {
            str(k): _block([ev for ev in events if ev.cluster_id == k]) for k in ids
        }
    return summary


def _to_jsonable(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, np.ndarray):
            out[k] = v.tolist()
        elif isinstance(v, dict):
            out[k] = {str(kk): vv for kk, vv in v.items()}
        else:
            out[k] = v
    return out
