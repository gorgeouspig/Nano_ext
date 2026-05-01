"""Sub-level analysis for multi-level nanopore events.

Uses Gaussian Mixture Models (GMM) with Bayesian Information Criterion (BIC)
to objectively determine the number of distinct current levels within each
detected event — the same information-theoretic approach used for global
threshold determination.

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


def analyze_sublevels(
    event: Event,
    signal: np.ndarray,
    baseline: np.ndarray,
    sampling_rate: float,
    filter_cutoff: Optional[float] = None,
    max_levels: int = 5,
    min_segment_samples: int = 50,
) -> Event:
    """Analyze an event for sub-level structure using GMM + BIC.

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
        transients. If None, no trimming is applied.
    max_levels : int
        Maximum number of GMM components (sub-levels) to consider.
    min_segment_samples : int
        Minimum number of samples for a sub-level segment to be
        physically resolvable. Segments shorter than this are absorbed
        into their nearest neighbor by GMM mean distance.

    Returns
    -------
    Event
        Updated event. event_type is set to MULTI_LEVEL if BIC selects
        more than one component; otherwise the event is returned unchanged
        as a single-level event.
    """
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

    # --- GMM + BIC: objective determination of number of levels ---
    max_k = min(max_levels, (inner_end - inner_start) // min_segment_samples)
    if max_k < 1:
        return event

    k_optimal, best_gmm = _fit_gmm_bic(event_signal, max_components=max_k)

    if k_optimal == 1 or best_gmm is None:
        return event

    # --- Temporal segmentation via hard assignment ---
    X = event_signal.reshape(-1, 1)
    labels = best_gmm.predict(X)
    gmm_means = best_gmm.means_.flatten()

    segments = _rle_segments(labels)
    segments = _merge_short_segments(segments, min_segment_samples, gmm_means)
    segments = _merge_same_label(segments)

    if len(segments) <= 1:
        return event

    # --- Build SubLevel objects ---
    sorted_means = np.sort(gmm_means)

    sublevels: list[SubLevel] = []
    for seg in segments:
        abs_start = inner_start + seg["start"]
        abs_end = inner_start + seg["end"]
        seg_signal = signal[abs_start:abs_end]

        mean_current = float(np.mean(seg_signal))
        seg_gmm_mean = gmm_means[seg["label"]]
        level_idx = int(np.argmin(np.abs(sorted_means - seg_gmm_mean)))

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


def analyze_events_sublevels(
    events: list[Event],
    signal: np.ndarray,
    baseline: np.ndarray,
    sampling_rate: float,
    filter_cutoff: Optional[float] = None,
    max_levels: int = 5,
    min_segment_samples: int = 50,
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

    Returns
    -------
    list[Event]
        Same list of events with sub-level information added where BIC
        supports more than one level.
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
