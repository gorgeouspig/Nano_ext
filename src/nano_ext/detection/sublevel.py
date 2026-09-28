"""Sub-level analysis for multi-level nanopore events.

Three interchangeable methods determine the current levels within each
detected event:

``"gmm"`` (default)
    Gaussian Mixture Models with the Bayesian Information Criterion (BIC)
    — the same information-theoretic approach used for global threshold
    determination.
``"dpgmm"``
    A Dirichlet-process GMM (see :mod:`nano_ext.detection.bayes_mixture`)
    fitted on samples thinned to about one per filter correlation time, so
    the number of levels is inferred without treating autocorrelated samples
    as independent.
``"bocpd"``
    Bayesian online change-point detection
    (:mod:`nano_ext.detection.bocpd`) on the thinned samples; segments with
    statistically indistinguishable means are then grouped into levels.

Edge artefacts introduced by the low-pass filter are removed before fitting
by trimming a margin proportional to the filter rise time at both ends of
the event, preventing the filter's finite transition slope from being
misidentified as a step transition.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from sklearn.mixture import GaussianMixture

from nano_ext.models import Event, EventType, SubLevel

SUBLEVEL_METHODS = ("gmm", "dpgmm", "bocpd")


def analyze_sublevels(
    event: Event,
    signal: np.ndarray,
    baseline: np.ndarray,
    sampling_rate: float,
    filter_cutoff: Optional[float] = None,
    max_levels: int = 5,
    min_segment_samples: int = 50,
    method: str = "gmm",
    dp_concentration: Optional[float] = None,
) -> Event:
    """Analyze an event for sub-level structure.

    Parameters
    ----------
    event : Event
        A previously detected event.
    signal : np.ndarray
        Full filtered signal array (absolute current, not residual).
    baseline : np.ndarray
        Estimated baseline array (same length as signal).
    sampling_rate : float
        Sampling rate in Hz.
    filter_cutoff : float, optional
        Low-pass filter cutoff frequency in Hz. Used to compute how many
        samples to trim at each end of the event to remove filter
        transients, and (for ``"dpgmm"`` / ``"bocpd"``) the thinning stride.
        If None, no trimming or thinning is applied.
    max_levels : int
        Maximum number of GMM components (sub-levels) to consider
        (truncation level for ``"dpgmm"``; unused by ``"bocpd"``).
    min_segment_samples : int
        Minimum number of samples for a sub-level segment to be
        physically resolvable. Segments shorter than this are absorbed
        into their nearest neighbor by level mean distance.
    method : str
        ``"gmm"``, ``"dpgmm"`` or ``"bocpd"`` (see module docstring).
    dp_concentration : float, optional
        Dirichlet-process concentration for ``method="dpgmm"``.

    Returns
    -------
    Event
        Updated event. event_type is set to MULTI_LEVEL if more than one
        level is found; otherwise the event is returned unchanged as a
        single-level event.
    """
    if method not in SUBLEVEL_METHODS:
        raise ValueError(
            f"Unknown sublevel method: {method}. Use one of {SUBLEVEL_METHODS}."
        )

    start = event.start_idx
    end = event.end_idx

    # --- Edge trimming: remove low-pass filter transients ---
    # Approximate 10-90% rise time of a 4th-order Bessel filter: ~2 / cutoff
    if filter_cutoff is not None and filter_cutoff > 0:
        edge_samples = max(0, int(2.0 * sampling_rate / filter_cutoff))
    else:
        edge_samples = 0

    inner_start = start + edge_samples
    inner_end = end - edge_samples

    if (inner_end - inner_start) < 2 * min_segment_samples:
        return event

    event_signal = signal[inner_start:inner_end]

    max_k = min(max_levels, (inner_end - inner_start) // min_segment_samples)
    if max_k < 1:
        return event

    if method == "gmm":
        found = _levels_gmm_bic(event_signal, max_k)
    elif method == "dpgmm":
        found = _levels_dpgmm(
            event_signal, max_k, sampling_rate, filter_cutoff,
            min_segment_samples, dp_concentration,
        )
    else:
        found = _levels_bocpd(
            event_signal, sampling_rate, filter_cutoff, min_segment_samples,
        )
    if found is None:
        return event
    segments, level_means = found

    segments = _merge_short_segments(segments, min_segment_samples, level_means)
    segments = _merge_same_label(segments)

    if len(segments) <= 1:
        return event

    # --- Build SubLevel objects ---
    sorted_means = np.sort(level_means)

    sublevels: list[SubLevel] = []
    for seg in segments:
        abs_start = inner_start + seg["start"]
        abs_end = inner_start + seg["end"]
        seg_signal = signal[abs_start:abs_end]

        mean_current = float(np.mean(seg_signal))
        seg_level_mean = level_means[seg["label"]]
        level_idx = int(np.argmin(np.abs(sorted_means - seg_level_mean)))

        sublevels.append(SubLevel(
            start_idx=abs_start,
            end_idx=abs_end,
            start_time=abs_start / sampling_rate,
            end_time=abs_end / sampling_rate,
            duration=(abs_end - abs_start) / sampling_rate,
            mean_current=mean_current,
            std_current=float(np.std(seg_signal)),
            level_index=level_idx,
        ))

    if len(sublevels) <= 1:
        return event

    event.n_levels = len(sublevels)
    event.sublevels = sublevels
    event.event_type = EventType.MULTI_LEVEL

    return event


# ---------------------------------------------------------------------------
# Level-finding back-ends.  Each returns (segments, level_means) where every
# segment dict carries a "label" indexing level_means, or None when the event
# has a single level.
# ---------------------------------------------------------------------------

def _levels_gmm_bic(
    event_signal: np.ndarray,
    max_k: int,
) -> Optional[tuple[list[dict], np.ndarray]]:
    k_optimal, best_gmm = _fit_gmm_bic(event_signal, max_components=max_k)
    if k_optimal == 1 or best_gmm is None:
        return None
    labels = best_gmm.predict(event_signal.reshape(-1, 1))
    return _rle_segments(labels), best_gmm.means_.flatten()


def _levels_dpgmm(
    event_signal: np.ndarray,
    max_k: int,
    sampling_rate: float,
    filter_cutoff: Optional[float],
    min_segment_samples: int,
    concentration: Optional[float],
) -> Optional[tuple[list[dict], np.ndarray]]:
    from nano_ext.detection.bayes_mixture import fit_dpgmm_1d, thinning_step

    step = thinning_step(sampling_rate, filter_cutoff)
    thinned = event_signal[::step]
    if len(thinned) < 4:
        return None
    fit = fit_dpgmm_1d(
        thinned,
        max_components=max_k,
        concentration=concentration,
        min_count=max(2.0, min_segment_samples / step),
    )
    if fit.n_components == 1:
        return None
    # Fit on decorrelated samples, but assign every sample.
    labels = fit.predict(event_signal)
    return _rle_segments(labels), fit.means


def _levels_bocpd(
    event_signal: np.ndarray,
    sampling_rate: float,
    filter_cutoff: Optional[float],
    min_segment_samples: int,
) -> Optional[tuple[list[dict], np.ndarray]]:
    from nano_ext.detection.bayes_mixture import thinning_step
    from nano_ext.detection.bocpd import bocpd, robust_noise_std

    step = thinning_step(sampling_rate, filter_cutoff)
    thinned = event_signal[::step]
    n = len(thinned)
    min_seg = max(2, int(np.ceil(min_segment_samples / step)))
    if n < 2 * min_seg:
        return None

    # Prior: about one change point per event.
    hazard = min(0.5, 1.0 / max(n / 2.0, 2.0 * min_seg))
    noise = robust_noise_std(thinned)
    res = bocpd(thinned, hazard=hazard, beta0=noise ** 2, min_segment_length=min_seg)
    if not res.changepoints:
        return None

    bounds = [0] + [cp * step for cp in res.changepoints] + [len(event_signal)]
    segments = [
        {"start": bounds[i], "end": bounds[i + 1]}
        for i in range(len(bounds) - 1)
        if bounds[i + 1] > bounds[i]
    ]
    seg_means = np.array([np.mean(event_signal[s["start"]:s["end"]]) for s in segments])
    seg_n = np.array([(s["end"] - s["start"]) / step for s in segments])

    # Group segments into levels: walk the segments sorted by mean and start
    # a new level when the gap exceeds 4 standard errors of the difference.
    order = np.argsort(seg_means)
    labels = np.empty(len(segments), dtype=int)
    level = 0
    labels[order[0]] = 0
    for a, b in zip(order[:-1], order[1:]):
        se = noise * np.sqrt(1.0 / seg_n[a] + 1.0 / seg_n[b])
        if seg_means[b] - seg_means[a] > 4.0 * se:
            level += 1
        labels[b] = level
    if level == 0:
        return None

    level_means = np.array([
        np.average(seg_means[labels == k], weights=seg_n[labels == k])
        for k in range(level + 1)
    ])
    for seg, lab in zip(segments, labels):
        seg["label"] = int(lab)
    return segments, level_means


def analyze_events_sublevels(
    events: list[Event],
    signal: np.ndarray,
    baseline: np.ndarray,
    sampling_rate: float,
    filter_cutoff: Optional[float] = None,
    max_levels: int = 5,
    min_segment_samples: int = 50,
    method: str = "gmm",
    dp_concentration: Optional[float] = None,
) -> list[Event]:
    """Analyze all events for sub-level structure.

    Applies :func:`analyze_sublevels` to every event in the list and
    returns the updated list.  Events that do not contain resolvable
    sub-levels are returned unchanged.

    Parameters
    ----------
    events : list[Event]
        Previously detected events (output of ``detect_events``).
    signal : np.ndarray
        Full filtered signal array (absolute current values, not residual).
    baseline : np.ndarray
        Estimated baseline array, same length as *signal*.
    sampling_rate : float
        Sampling rate in Hz.
    filter_cutoff : float, optional
        Low-pass filter cutoff in Hz; used to trim filter transients at
        event edges.  Pass the same value used in the filtering step.
    max_levels : int
        Maximum number of GMM components (sub-levels) to test per event.
    min_segment_samples : int
        Minimum number of samples for a sub-level to be considered
        physically resolvable.
    method : str
        ``"gmm"``, ``"dpgmm"`` or ``"bocpd"`` (see :func:`analyze_sublevels`).
    dp_concentration : float, optional
        Dirichlet-process concentration for ``method="dpgmm"``.

    Returns
    -------
    list[Event]
        Same list of events with sub-level information added where the
        chosen method finds more than one level.
    """
    return [
        analyze_sublevels(
            event=ev,
            signal=signal,
            baseline=baseline,
            sampling_rate=sampling_rate,
            filter_cutoff=filter_cutoff,
            max_levels=max_levels,
            min_segment_samples=min_segment_samples,
            method=method,
            dp_concentration=dp_concentration,
        )
        for ev in events
    ]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fit_gmm_bic(
    signal: np.ndarray,
    max_components: int,
) -> tuple[int, Optional[GaussianMixture]]:
    """Fit GMMs with k=1..max_components; return (k_optimal, best_gmm)."""
    X = signal.reshape(-1, 1)
    best_score = np.inf
    best_k = 1
    best_gmm: Optional[GaussianMixture] = None

    for k in range(1, max_components + 1):
        gmm = GaussianMixture(
            n_components=k,
            covariance_type="full",
            max_iter=200,
            n_init=3,
            random_state=0,
        )
        try:
            gmm.fit(X)
            score = gmm.bic(X)
        except Exception:
            break
        if score < best_score:
            best_score = score
            best_k = k
            best_gmm = gmm

    return best_k, best_gmm


def _rle_segments(labels: np.ndarray) -> list[dict]:
    """Run-length encode a label array into segment dicts."""
    if len(labels) == 0:
        return []

    segments: list[dict] = []
    current = int(labels[0])
    seg_start = 0

    for i in range(1, len(labels)):
        if int(labels[i]) != current:
            segments.append({"label": current, "start": seg_start, "end": i})
            current = int(labels[i])
            seg_start = i

    segments.append({"label": current, "start": seg_start, "end": len(labels)})
    return segments


def _merge_short_segments(
    segments: list[dict],
    min_n: int,
    gmm_means: np.ndarray,
) -> list[dict]:
    """Absorb segments shorter than min_n into their nearest neighbor."""
    segments = [dict(s) for s in segments]

    while len(segments) > 1:
        lengths = [s["end"] - s["start"] for s in segments]
        min_len = min(lengths)
        if min_len >= min_n:
            break

        idx = lengths.index(min_len)
        seg_mean = gmm_means[segments[idx]["label"]]

        if idx == 0:
            neighbor = 1
        elif idx == len(segments) - 1:
            neighbor = idx - 1
        else:
            left_diff = abs(seg_mean - gmm_means[segments[idx - 1]["label"]])
            right_diff = abs(seg_mean - gmm_means[segments[idx + 1]["label"]])
            neighbor = idx - 1 if left_diff <= right_diff else idx + 1

        if neighbor < idx:
            segments[neighbor]["end"] = segments[idx]["end"]
        else:
            segments[neighbor]["start"] = segments[idx]["start"]

        segments.pop(idx)

    return segments


def _merge_same_label(segments: list[dict]) -> list[dict]:
    """Merge consecutive segments that share the same label."""
    if not segments:
        return segments

    merged = [dict(segments[0])]
    for seg in segments[1:]:
        if seg["label"] == merged[-1]["label"]:
            merged[-1]["end"] = seg["end"]
        else:
            merged.append(dict(seg))

    return merged
