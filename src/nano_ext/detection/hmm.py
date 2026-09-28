"""HMM-based multi-state event analysis.

Requires hmmlearn:  pip install "nano_ext[hmm]"
"""

from __future__ import annotations

from typing import Optional

import numpy as np


def _require_hmmlearn():
    try:
        from hmmlearn.hmm import GaussianHMM
        return GaussianHMM
    except ImportError:
        raise ImportError(
            "hmmlearn is required for HMM analysis.\n"
            'Install it with:  pip install "nano_ext[hmm]"'
        )


def fit_hmm_event(
    event_signal: np.ndarray,
    sampling_rate: float,
    max_states: int = 5,
    n_iter: int = 100,
    random_state: int = 42,
) -> Optional["HMMResult"]:
    """Fit a Gaussian HMM to an event waveform, selecting state count by BIC.

    Parameters
    ----------
    event_signal : np.ndarray
        Filtered signal samples covering the event (baseline-corrected or raw).
    sampling_rate : float
        Sampling rate in Hz (used to compute dwell times).
    max_states : int
        Maximum number of hidden states to consider.
    n_iter : int
        Maximum EM iterations per fit.
    random_state : int
        Random seed for reproducibility.

    Returns
    -------
    HMMResult or None
        Best-BIC result, or None if the signal is too short to fit.
    """
    from nano_ext.models import HMMResult

    GaussianHMM = _require_hmmlearn()

    X = event_signal.reshape(-1, 1).astype(np.float64)
    n_samples = len(X)
    if n_samples < 10:
        return None

    best_result: Optional[HMMResult] = None
    best_bic = np.inf

    for k in range(1, max_states + 1):
        if n_samples < k * 2:
            break
        try:
            model = GaussianHMM(
                n_components=k,
                covariance_type="diag",
                n_iter=n_iter,
                random_state=random_state,
            )
            model.fit(X)
            ll = model.score(X)  # total log-likelihood of the whole sequence
            # n_params: k means + k variances + k*(k-1) transitions + (k-1) start probs
            n_params = k * 2 + k * (k - 1) + (k - 1)
            bic = -2.0 * ll + n_params * np.log(n_samples)

            if bic < best_bic:
                best_bic = bic
                _, state_seq = model.decode(X, algorithm="viterbi")
                means = model.means_[:, 0]
                stds = np.sqrt(model.covars_[:, 0])
                trans = model.transmat_
                dwell_times = _compute_dwell_times(state_seq, k, sampling_rate)
                best_result = HMMResult(
                    n_states=k,
                    state_means=means,
                    state_stds=stds,
                    transition_matrix=trans,
                    state_sequence=state_seq,
                    dwell_times_s=dwell_times,
                    log_likelihood=float(ll),
                    bic=float(bic),
                )
        except Exception:
            continue

    return best_result


def _compute_dwell_times(
    state_seq: np.ndarray,
    n_states: int,
    sampling_rate: float,
) -> list[np.ndarray]:
    """Convert a Viterbi state sequence to per-state dwell-time arrays (seconds)."""
    runs: list[list[float]] = [[] for _ in range(n_states)]
    if len(state_seq) == 0:
        return [np.empty(0) for _ in range(n_states)]

    cur = state_seq[0]
    length = 1
    for s in state_seq[1:]:
        if s == cur:
            length += 1
        else:
            runs[cur].append(length / sampling_rate)
            cur = s
            length = 1
    runs[cur].append(length / sampling_rate)
    return [np.array(r) for r in runs]


def analyze_events_hmm(
    events,
    filtered_signal: np.ndarray,
    sampling_rate: float,
    max_states: int = 5,
    method: str = "bic",
    filter_cutoff: Optional[float] = None,
) -> list:
    """Fit a Gaussian HMM to each event's waveform in-place.

    Sets ``event.hmm_result`` for every event.  Events whose signal segment
    is too short are left with ``hmm_result = None``.

    Parameters
    ----------
    events : list[Event]
    filtered_signal : np.ndarray
        Filtered signal array (same coordinate system as event indices).
    sampling_rate : float
    max_states : int
        Maximum number of hidden states to try per event (the weak-limit
        truncation for ``"sticky_hdp"``).
    method : str
        ``"bic"`` (hmmlearn EM + BIC) or ``"sticky_hdp"`` (sticky HDP-HMM,
        see :func:`nano_ext.detection.hdphmm.fit_sticky_hdp_hmm`).
    filter_cutoff : float, optional
        Low-pass cutoff in Hz; used by ``"sticky_hdp"`` to thin
        autocorrelated samples.

    Returns
    -------
    list[Event]
        Same list with ``hmm_result`` populated.
    """
    if method not in ("bic", "sticky_hdp"):
        raise ValueError(f"Unknown HMM method: {method}. Use 'bic' or 'sticky_hdp'.")
    for ev in events:
        seg = filtered_signal[ev.start_idx:ev.end_idx]
        if method == "bic":
            ev.hmm_result = fit_hmm_event(seg, sampling_rate, max_states=max_states)
        else:
            from nano_ext.detection.hdphmm import fit_sticky_hdp_hmm
            ev.hmm_result = fit_sticky_hdp_hmm(
                seg, sampling_rate, filter_cutoff=filter_cutoff, max_states=max_states,
            )
    return events
