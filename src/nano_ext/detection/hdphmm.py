"""Sticky HDP-HMM for multi-state event analysis.

Implements the weak-limit Gibbs sampler of the sticky hierarchical
Dirichlet-process HMM (Fox, Sudderth, Jordan & Willsky, 2011, "A sticky
HDP-HMM with application to speaker diarization") for 1-D Gaussian
emissions with unknown mean and variance per state.

Compared with fitting k-state HMMs by EM and picking k by BIC:

* the number of states is inferred (states the data do not need stay
  empty), and its posterior distribution is reported;
* the *sticky* self-transition bias ``kappa`` suppresses the rapid spurious
  state switching that plain (HDP-)HMMs produce on noisy step signals.

Filtered traces are autocorrelated, which makes any HMM see "extra" states
in correlated noise.  The sampler therefore runs on samples thinned to about
one per filter correlation time; the decoded path is expanded back to full
resolution, so dwell times are resolved to the thinning stride (comparable to
the filter rise time, the physical resolution limit anyway).

Only NumPy / SciPy are required.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from nano_ext.models import HMMResult


def fit_sticky_hdp_hmm(
    event_signal: np.ndarray,
    sampling_rate: float,
    filter_cutoff: Optional[float] = None,
    max_states: int = 10,
    alpha: float = 1.0,
    gamma: float = 1.0,
    kappa: Optional[float] = None,
    n_iter: int = 300,
    burn_in: int = 150,
    min_state_samples: int = 10,
    max_fit_samples: int = 4000,
    seed: int = 0,
) -> Optional[HMMResult]:
    """Fit a sticky HDP-HMM to an event waveform by Gibbs sampling.

    Parameters
    ----------
    event_signal : np.ndarray
        Signal samples covering the event.
    sampling_rate : float
        Sampling rate in Hz.
    filter_cutoff : float, optional
        Low-pass cutoff in Hz; sets the thinning stride.  None = no thinning.
    max_states : int
        Weak-limit truncation ``L`` (upper bound on the number of states).
    alpha, gamma : float
        Transition and top-level Dirichlet-process concentrations.
    kappa : float, optional
        Self-transition bias.  ``None`` sets it so the prior expected
        self-transition probability ``kappa / (alpha + kappa)`` implies a
        dwell of ``min_state_samples`` thinned samples.
    n_iter, burn_in : int
        Gibbs iterations and number of initial draws discarded.
    min_state_samples : int
        A state counts as occupied when it holds at least this many samples
        (at full resolution).
    max_fit_samples : int
        The stride is increased if needed so the sampler sees at most this
        many samples.
    seed : int
        Random seed.

    Returns
    -------
    HMMResult or None
        ``None`` if the signal is too short.  ``state_means`` are ordered
        ascending and ``state_sequence`` indexes them.  The transition
        matrix is estimated from the decoded full-resolution path.
    """
    x_full = np.asarray(event_signal, dtype=np.float64).ravel()
    n_full = len(x_full)
    if n_full < 10:
        return None

    from nano_ext.detection.bayes_mixture import thinning_step
    from nano_ext.detection.bocpd import robust_noise_std

    step = thinning_step(sampling_rate, filter_cutoff)
    step = max(step, int(np.ceil(n_full / max_fit_samples)))
    x = x_full[::step]
    n = len(x)
    if n < 5:
        return None

    L = int(max_states)
    min_occ = max(1, int(np.ceil(min_state_samples / step)))
    if kappa is None:
        # E[self-transition] = kappa / (alpha + kappa) = 1 - 1 / dwell
        dwell = max(2.0, float(min_occ))
        kappa = alpha * (dwell - 1.0)

    rng = np.random.default_rng(seed)

    # --- Emission prior: Normal-Inverse-Gamma, weakly informative ---
    noise = robust_noise_std(x)
    m0 = float(np.median(x))
    k0 = 0.01
    a0 = 1.0
    b0 = a0 * noise ** 2

    # --- Initialisation ---
    beta = np.full(L, 1.0 / L)
    pi = np.full((L, L), (1.0 - 0.9) / (L - 1)) if L > 1 else np.ones((1, 1))
    if L > 1:
        np.fill_diagonal(pi, 0.9)
    mu = np.quantile(x, (np.arange(L) + 0.5) / L)
    var = np.full(L, noise ** 2)

    kept_k: list[int] = []
    # Highest log-joint draw for each number of occupied states:
    # k -> (log joint, z, mu, var, pi, occupied)
    best_by_k: dict = {}

    for it in range(n_iter):
        loglik = _gauss_loglik(x, mu, var)
        z = _ffbs(loglik, pi, beta, rng)

        # Transition counts
        trans = np.zeros((L, L))
        np.add.at(trans, (z[:-1], z[1:]), 1.0)

        # Auxiliary table counts m_jk (Chinese restaurant table sampling)
        m = np.zeros((L, L))
        for j in range(L):
            for k in range(L):
                njk = int(trans[j, k])
                if njk == 0:
                    continue
                conc = alpha * beta[k] + (kappa if j == k else 0.0)
                m[j, k] = np.sum(rng.random(njk) < conc / (conc + np.arange(njk)))
        # Override variables: remove self-transition tables due to kappa
        rho = kappa / (alpha + kappa)
        m_bar = m.copy()
        for j in range(L):
            if m[j, j] > 0:
                p_override = rho / (rho + beta[j] * (1.0 - rho))
                m_bar[j, j] -= rng.binomial(int(m[j, j]), p_override)

        beta = rng.dirichlet(gamma / L + m_bar.sum(axis=0))
        beta = np.clip(beta, 1e-12, None)
        beta /= beta.sum()
        for j in range(L):
            prior = alpha * beta.copy()
            prior[j] += kappa
            pi[j] = rng.dirichlet(prior + trans[j])
        pi = np.clip(pi, 1e-300, None)

        # Emission parameters from the NIG posterior
        counts = np.bincount(z, minlength=L).astype(float)
        sums = np.bincount(z, weights=x, minlength=L)
        sq = np.bincount(z, weights=x * x, minlength=L)
        xbar = np.divide(sums, counts, out=np.zeros(L), where=counts > 0)
        ss = np.clip(sq - counts * xbar ** 2, 0.0, None)
        kn = k0 + counts
        mn = (k0 * m0 + sums) / kn
        an = a0 + 0.5 * counts
        bn = b0 + 0.5 * ss + 0.5 * k0 * counts * (xbar - m0) ** 2 / kn
        var = 1.0 / rng.gamma(an, 1.0 / bn)
        mu = rng.normal(mn, np.sqrt(var / kn))

        if it >= burn_in:
            occupied = np.flatnonzero(counts >= min_occ)
            if len(occupied) == 0:
                occupied = np.array([int(np.argmax(counts))])
            k_draw = len(occupied)
            kept_k.append(k_draw)
            lj = _log_joint(x, z, mu, var, pi)
            if k_draw not in best_by_k or lj > best_by_k[k_draw][0]:
                best_by_k[k_draw] = (lj, mu.copy(), var.copy(), pi.copy(), occupied)

    values, freq = np.unique(kept_k, return_counts=True)
    posterior = {int(v): float(f / len(kept_k)) for v, f in zip(values, freq)}
    k_map = int(values[np.argmax(freq)])

    # Representative draw: the most probable one with the MAP number of
    # states.  Re-decode with Viterbi on its occupied states for a clean path.
    best_lj, mu_b, var_b, pi_b, occ = best_by_k[k_map]
    pi_occ = pi_b[np.ix_(occ, occ)]
    pi_occ = pi_occ / pi_occ.sum(axis=1, keepdims=True)
    path = _viterbi(_gauss_loglik(x, mu_b[occ], var_b[occ]), pi_occ)

    # Expand to full resolution and relabel states by ascending mean.
    full_path = np.repeat(path, step)[:n_full]
    if len(full_path) < n_full:
        full_path = np.concatenate([full_path, np.full(n_full - len(full_path), path[-1])])
    used = np.unique(full_path)
    means = np.array([x_full[full_path == s].mean() for s in used])
    order = np.argsort(means)
    relabel = np.empty(int(used.max()) + 1, dtype=int)
    relabel[used[order]] = np.arange(len(used))
    state_seq = relabel[full_path]
    k = len(used)

    state_means = means[order]
    state_stds = np.array([x_full[state_seq == s].std() for s in range(k)])
    state_stds = np.where(state_stds > 0, state_stds, noise)

    tm = np.zeros((k, k))
    np.add.at(tm, (state_seq[:-1], state_seq[1:]), 1.0)
    rows = tm.sum(axis=1)
    for s in range(k):
        if rows[s] == 0:
            tm[s, s] = 1.0
    tm = tm / tm.sum(axis=1, keepdims=True)

    from nano_ext.detection.hmm import _compute_dwell_times

    return HMMResult(
        n_states=k,
        state_means=state_means,
        state_stds=state_stds,
        transition_matrix=tm,
        state_sequence=state_seq,
        dwell_times_s=_compute_dwell_times(state_seq, k, sampling_rate),
        log_likelihood=float(best_lj),
        bic=float("nan"),
        method="sticky_hdp",
        n_states_posterior=posterior,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gauss_loglik(x: np.ndarray, mu: np.ndarray, var: np.ndarray) -> np.ndarray:
    """(n, L) Gaussian log-likelihoods."""
    return -0.5 * (np.log(2.0 * np.pi * var)[None, :] + (x[:, None] - mu[None, :]) ** 2 / var[None, :])


def _ffbs(
    loglik: np.ndarray,
    pi: np.ndarray,
    init: np.ndarray,
    rng: np.random.Generator,
) -> np.ndarray:
    """Forward-filtering backward-sampling of a state path."""
    n, L = loglik.shape
    lik = np.exp(loglik - loglik.max(axis=1, keepdims=True))
    alpha = np.empty((n, L))
    a = init * lik[0]
    alpha[0] = a / a.sum()
    for t in range(1, n):
        a = (alpha[t - 1] @ pi) * lik[t]
        s = a.sum()
        alpha[t] = a / s if s > 0 else np.full(L, 1.0 / L)

    z = np.empty(n, dtype=int)
    u = rng.random(n)
    p = alpha[-1]
    z[-1] = min(int(np.searchsorted(np.cumsum(p), u[-1] * p.sum())), L - 1)
    for t in range(n - 2, -1, -1):
        p = alpha[t] * pi[:, z[t + 1]]
        z[t] = min(int(np.searchsorted(np.cumsum(p), u[t] * p.sum())), L - 1)
    return z


def _viterbi(loglik: np.ndarray, pi: np.ndarray) -> np.ndarray:
    """Most probable path (uniform initial distribution)."""
    n, L = loglik.shape
    log_pi = np.log(np.clip(pi, 1e-300, None))
    delta = loglik[0] - np.log(L)
    back = np.empty((n, L), dtype=int)
    for t in range(1, n):
        cand = delta[:, None] + log_pi
        back[t] = np.argmax(cand, axis=0)
        delta = cand[back[t], np.arange(L)] + loglik[t]
    path = np.empty(n, dtype=int)
    path[-1] = int(np.argmax(delta))
    for t in range(n - 1, 0, -1):
        path[t - 1] = back[t, path[t]]
    return path


def _log_joint(
    x: np.ndarray,
    z: np.ndarray,
    mu: np.ndarray,
    var: np.ndarray,
    pi: np.ndarray,
) -> float:
    """log p(x, z | theta) for choosing a representative draw."""
    ll = -0.5 * np.sum(np.log(2.0 * np.pi * var[z]) + (x - mu[z]) ** 2 / var[z])
    lt = np.sum(np.log(np.clip(pi[z[:-1], z[1:]], 1e-300, None)))
    return float(ll + lt)
