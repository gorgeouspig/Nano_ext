"""Shared pieces of the benchmark: event table format, matching and metrics.

Every method adapter returns a list of detected events, each a dict with

    start, end      event boundaries (s, in the time frame of the signal)
    depth           mean blockade relative to the local baseline (pA, > 0)
    n_levels        number of sub-levels (None if the method has none)
    level_bounds    boundaries between sub-levels (s), may be empty

Ground truth (``nano_ext.testing.synthetic.truth_table``) uses the same keys.
"""

from __future__ import annotations

import numpy as np

IOU_THRESHOLD = 0.5  # a detection counts as a true positive at IoU >= this


def interval_iou(a0: float, a1: float, b0: float, b1: float) -> float:
    inter = min(a1, b1) - max(a0, b0)
    if inter <= 0:
        return 0.0
    union = max(a1, b1) - min(a0, b0)
    return inter / union if union > 0 else 0.0


def match_events(truth: list[dict], detected: list[dict], iou_min: float = IOU_THRESHOLD):
    """One-to-one matching of detections to true events by interval IoU.

    Candidate pairs (overlapping intervals) are accepted greedily in order of
    decreasing IoU, keeping those with IoU >= *iou_min*. Returns a list of
    ``(truth_index, detected_index, iou)``.
    """
    if not truth or not detected:
        return []
    t0 = np.array([t["start"] for t in truth])
    t1 = np.array([t["end"] for t in truth])
    order = np.argsort(t0)
    t0s, t1s = t0[order], t1[order]
    max_len = float(np.max(t1 - t0))
    pairs = []
    for j, d in enumerate(detected):
        # true events that can overlap [d.start, d.end]
        lo = np.searchsorted(t0s, d["start"] - max_len, side="left")
        hi = np.searchsorted(t0s, d["end"], side="right")
        for k in range(lo, hi):
            iou = interval_iou(t0s[k], t1s[k], d["start"], d["end"])
            if iou > 0 and iou >= iou_min:
                pairs.append((iou, int(order[k]), j))
    pairs.sort(reverse=True)
    used_t, used_d, out = set(), set(), []
    for iou, i, j in pairs:
        if i in used_t or j in used_d:
            continue
        used_t.add(i)
        used_d.add(j)
        out.append((i, j, iou))
    return out


def _prf(tp: int, n_true: int, n_det: int):
    recall = tp / n_true if n_true else float("nan")
    precision = tp / n_det if n_det else (1.0 if n_true == 0 else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall > 0 else 0.0
    return recall, precision, f1


def evaluate(truth: list[dict], detected: list[dict], iou_min: float = IOU_THRESHOLD) -> dict:
    """Detection, feature and sub-level metrics for one recording.

    * ``recall``, ``precision``, ``f1`` at IoU >= *iou_min*; ``f1_any`` counts
      any overlap as a match (detection regardless of boundary accuracy).
    * ``dwell_err``, ``depth_err``: median absolute relative error over
      matched events.
    * ``level_acc``: fraction of matched events with the true number of
      sub-levels (NaN if the method reports none); ``bound_err_us``: median
      absolute error of the sub-level boundaries (µs) over matched events whose
      level count is right and that have more than one level.
    """
    matches = match_events(truth, detected, iou_min)
    any_matches = match_events(truth, detected, 1e-12)
    n_t, n_d = len(truth), len(detected)
    recall, precision, f1 = _prf(len(matches), n_t, n_d)
    _, _, f1_any = _prf(len(any_matches), n_t, n_d)

    dwell_err, depth_err, lv_ok, bound_err = [], [], [], []
    for i, j, _ in matches:
        t, d = truth[i], detected[j]
        t_dwell = t["end"] - t["start"]
        dwell_err.append(abs((d["end"] - d["start"]) - t_dwell) / t_dwell)
        if t["depth"] != 0:
            depth_err.append(abs(d["depth"] - t["depth"]) / abs(t["depth"]))
        if d.get("n_levels") is not None:
            ok = d["n_levels"] == t["n_levels"]
            lv_ok.append(ok)
            if ok and t["n_levels"] > 1 and len(d.get("level_bounds") or []) == len(t["level_bounds"]):
                bound_err += [abs(a - b) * 1e6 for a, b in zip(d["level_bounds"], t["level_bounds"])]
    med = lambda v: float(np.median(v)) if v else float("nan")  # noqa: E731
    return {
        "n_true": n_t,
        "n_detected": n_d,
        "tp": len(matches),
        "recall": recall,
        "precision": precision,
        "f1": f1,
        "f1_any": f1_any,
        "dwell_err": med(dwell_err),
        "depth_err": med(depth_err),
        "level_acc": float(np.mean(lv_ok)) if lv_ok else float("nan"),
        "bound_err_us": med(bound_err),
    }


def robust_baseline(x: np.ndarray, sr: float, window_sec: float = 0.5, block: int = 256):
    """Slow open-pore baseline: running median of block medians, interpolated.

    Used by the simple reference detectors so that they share one baseline
    estimate (Nano_ext uses its own). Robust while events cover < 50 % of any
    ``window_sec`` window.
    """
    from scipy.ndimage import median_filter

    n = len(x)
    nb = max(1, n // block)
    med = np.median(np.asarray(x[: nb * block], dtype=np.float64).reshape(nb, block), axis=1)
    k = max(1, int(window_sec * sr / block)) | 1
    med = median_filter(med, size=min(k, nb if nb % 2 else nb - 1 or 1), mode="nearest")
    centres = (np.arange(nb) + 0.5) * block
    return np.interp(np.arange(n), centres, med)


def robust_sigma(r: np.ndarray) -> float:
    """Noise standard deviation from the median absolute deviation."""
    r = np.asarray(r, dtype=np.float64)
    return float(1.4826 * np.median(np.abs(r - np.median(r))))
