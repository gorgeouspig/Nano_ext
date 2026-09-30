"""Method adapters: ``detect(signal, sampling_rate, **params) -> list[event dict]``.

The event dict format is described in ``common.py``. Adapters are registered
in ``METHODS`` with their default parameters ("default setting": no tuning).
Only downward (current-decreasing) events are considered.
"""

from __future__ import annotations

import numpy as np

from common import robust_baseline, robust_sigma


# ---------------------------------------------------------------------------
# Nano_ext
# ---------------------------------------------------------------------------

def nano_ext(signal, sr, threshold_method="dpgmm", sublevel_method="dpgmm", **config):
    """Nano_ext pipeline with ``DetectionConfig()`` defaults (single process)."""
    import warnings

    from nano_ext.models import DetectionConfig, SignalData
    from nano_ext.pipeline import run_pipeline

    warnings.simplefilter("ignore")
    cfg = DetectionConfig(threshold_method=threshold_method, sublevel_method=sublevel_method,
                          n_jobs=1, **config)
    res = run_pipeline(SignalData(signal=np.asarray(signal, dtype=np.float32), sampling_rate=sr), cfg)
    out = []
    for ev in res.events:
        subs = ev.sublevels or []
        out.append({
            "start": ev.start_time,
            "end": ev.end_time,
            "depth": float(ev.depth),
            "n_levels": max(1, int(ev.n_levels)),
            "level_bounds": [s.start_time for s in subs[1:]],
        })
    return out


# ---------------------------------------------------------------------------
# Fixed threshold (baseline − k·σ) with hysteresis
# ---------------------------------------------------------------------------

def threshold(signal, sr, k_sigma=5.0, k_exit=1.0, min_duration_sec=0.0, merge_gap_sec=0.0):
    """Classic fixed-threshold detector.

    Samples below ``baseline − k_sigma·σ`` start an event; its boundaries are
    extended outward until the signal returns above ``baseline − k_exit·σ``
    (hysteresis). σ is the MAD of the baseline-subtracted signal. No sub-level
    analysis (``n_levels`` is None).
    """
    x = np.asarray(signal, dtype=np.float64)
    base = robust_baseline(x, sr)
    r = x - base
    sigma = robust_sigma(r)
    inside = r < -k_exit * sigma
    trigger = r < -k_sigma * sigma
    # runs where the hysteresis condition holds
    edges = np.flatnonzero(np.diff(np.concatenate(([0], inside.astype(np.int8), [0]))))
    starts, ends = edges[::2], edges[1::2]
    # keep runs that contain a trigger sample
    csum = np.concatenate(([0], np.cumsum(trigger)))
    keep = csum[ends] - csum[starts] > 0
    starts, ends = starts[keep], ends[keep]
    if merge_gap_sec > 0 and len(starts) > 1:
        gap = int(merge_gap_sec * sr)
        ms, me = [starts[0]], [ends[0]]
        for s, e in zip(starts[1:], ends[1:]):
            if s - me[-1] <= gap:
                me[-1] = e
            else:
                ms.append(s)
                me.append(e)
        starts, ends = np.array(ms), np.array(me)
    out = []
    min_len = int(min_duration_sec * sr)
    for s, e in zip(starts, ends):
        if e - s < max(1, min_len):
            continue
        out.append({"start": s / sr, "end": e / sr, "depth": float(-r[s:e].mean()),
                    "n_levels": None, "level_bounds": []})
    return out


# ---------------------------------------------------------------------------
# PELT change points (ruptures)
# ---------------------------------------------------------------------------

def pelt(signal, sr, penalty_factor=1.0, k_sigma=3.0, min_size=2):
    """PELT segmentation of the baseline-subtracted trace (L2 cost).

    Uses ``ruptures.KernelCPD(kernel="linear")``, the C implementation of
    PELT for the L2 (mean-shift) cost, with the BIC-type penalty
    ``penalty_factor · 2 σ² ln n``. Segments whose mean lies more than
    ``k_sigma·σ`` below the baseline are event levels; runs of consecutive
    event segments form one event with one sub-level per segment.
    """
    import ruptures as rpt

    x = np.asarray(signal, dtype=np.float64)
    base = robust_baseline(x, sr)
    r = x - base
    sigma = robust_sigma(r)
    n = len(r)
    pen = penalty_factor * 2.0 * sigma ** 2 * np.log(n)
    bkps = rpt.KernelCPD(kernel="linear", min_size=min_size, jump=1).fit(r).predict(pen=pen)
    bounds = [0] + list(bkps)
    csum = np.concatenate(([0.0], np.cumsum(r)))
    out, cur = [], None
    for a, b in zip(bounds[:-1], bounds[1:]):
        mean = (csum[b] - csum[a]) / (b - a)
        if mean < -k_sigma * sigma:
            if cur is None:
                cur = {"a": a, "b": b, "segs": [(a, b)]}
            else:
                cur["b"] = b
                cur["segs"].append((a, b))
            continue
        if cur is not None:
            out.append(cur)
            cur = None
    if cur is not None:
        out.append(cur)
    events = []
    for c in out:
        a, b = c["a"], c["b"]
        events.append({
            "start": a / sr, "end": b / sr, "depth": float(-(csum[b] - csum[a]) / (b - a)),
            "n_levels": len(c["segs"]),
            "level_bounds": [s / sr for s, _ in c["segs"][1:]],
        })
    return events


METHODS = {
    "nano_ext[dpgmm]": (nano_ext, {"threshold_method": "dpgmm", "sublevel_method": "dpgmm"}),
    "nano_ext[gmm]": (nano_ext, {"threshold_method": "gmm", "sublevel_method": "gmm"}),
    "threshold": (threshold, {}),
    "pelt": (pelt, {}),
}
