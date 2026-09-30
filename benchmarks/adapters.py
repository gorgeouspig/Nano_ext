"""Method adapters: ``detect(signal, sampling_rate, **params) -> list[event dict]``.

The event dict format is described in ``common.py``. Adapters are registered
in ``METHODS`` with their default parameters ("default setting": no tuning).
Only downward (current-decreasing) events are considered.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

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


# ---------------------------------------------------------------------------
# External tools (see external/setup.sh); each runs in its own environment
# ---------------------------------------------------------------------------

EXTERNAL = Path(__file__).resolve().parent / "_external"
WORKERS = Path(__file__).resolve().parent / "external"


def _require(path: Path, what: str) -> Path:
    if not path.exists():
        raise RuntimeError(f"{what} is not installed: run bash benchmarks/external/setup.sh")
    return path


def _run_worker(python: Path, script: str, *args) -> list:
    p = subprocess.run([str(python), str(WORKERS / script), *map(str, args)],
                       capture_output=True, text=True, env={**os.environ, "PYTHONWARNINGS": "ignore"})
    lines = [ln for ln in p.stdout.splitlines() if ln.startswith("[")]
    if p.returncode != 0 or not lines:
        raise RuntimeError(f"{script} failed: {p.stderr.strip().splitlines()[-1] if p.stderr.strip() else p.returncode}")
    return json.loads(lines[-1])


def mosaic(signal, sr, **params):
    """MOSAIC (NIST): ``eventSegment`` detection + ``cusumPlus`` (CUSUM+) levels.

    Keyword arguments override settings: ``eventThreshold``, ``minEventLength``,
    ``eventPad``, ``blockSizeSec`` (eventSegment) and ``StepSize``,
    ``MinThreshold``, ``MaxThreshold``, ``MinLength`` (cusumPlus).
    """
    py = _require(EXTERNAL / "venv-mosaic" / "bin" / "python", "MOSAIC")
    seg_keys = {"eventThreshold", "minEventLength", "eventPad", "blockSizeSec", "maxEventLength"}
    settings = {"eventSegment": {k: v for k, v in params.items() if k in seg_keys},
                "cusumPlus": {k: v for k, v in params.items() if k not in seg_keys}}
    with tempfile.TemporaryDirectory() as d:
        np.save(Path(d) / "signal.npy", np.asarray(signal, dtype=np.float64))
        return _run_worker(py, "mosaic_worker.py", Path(d) / "signal.npy", sr, json.dumps(settings))


def threshold_nanotrees(signal, sr, smallest_sublevel_pa=600.0, time_scaling=1.1,
                        exceptional_sensitivity=0.3, **threshold_params):
    """Fixed-threshold events (``threshold``), sub-levels fitted by Nano Trees.

    Nano Trees fits levels inside a given event window; windows come from the
    ``threshold`` detector here, padded by max(100 samples, half the event)
    on each side without reaching into neighbouring events. The plugin's
    default "Smallest Significant Sublevel" is 600 pA.
    """
    py = _require(EXTERNAL / "venv-poriscope" / "bin" / "python", "Poriscope (Nano Trees)")
    events = threshold(signal, sr, **threshold_params)
    if not events:
        return []
    n = len(signal)
    idx = [(int(round(e["start"] * sr)), int(round(e["end"] * sr))) for e in events]
    chunks = []
    for k, (a, b) in enumerate(idx):
        pad = max(100, (b - a) // 2)
        lo = max(0, a - pad, (idx[k - 1][1] + a) // 2 if k else 0)
        hi = min(n, b + pad, (b + idx[k + 1][0]) // 2 if k + 1 < len(idx) else n)
        chunks.append((lo, hi, a - lo, hi - b))
    settings = {"Smallest Significant Sublevel": smallest_sublevel_pa, "Time Scaling": time_scaling,
                "Exceptional Sublevel Sensitivity": exceptional_sensitivity}
    with tempfile.TemporaryDirectory() as d:
        np.savez(Path(d) / "in.npz", signal=np.asarray(signal, dtype=np.float64), sr=sr,
                 chunks=np.array(chunks, dtype=np.int64))
        fits = _run_worker(py, "nanotrees_worker.py", Path(d) / "in.npz", json.dumps(settings))
    out = []
    for ev, (lo, hi, pb, pa), fit in zip(events, chunks, fits):
        edges, cur = fit.get("edges"), fit.get("currents")
        if not edges or len(cur) < 3:  # no level found inside the window: keep the detection
            out.append({**ev, "n_levels": 1})
            continue
        inner = edges[1:-1]  # drop the baseline levels before and after the event
        base = 0.5 * (cur[0] + cur[-1])
        lv = cur[1:-1]
        w = np.diff(inner)
        depth = float(np.sum((base - np.array(lv)) * w) / np.sum(w)) if np.sum(w) > 0 else ev["depth"]
        out.append({"start": (lo + inner[0]) / sr, "end": (lo + inner[-1]) / sr, "depth": depth,
                    "n_levels": len(lv), "level_bounds": [(lo + e) / sr for e in inner[1:-1]]})
    return out


def autonanopore(signal, sr, theta=1.5, window_size_ms=30):
    """AutoNanopore (Sun et al. 2022), unmodified, called on an ABF copy of the signal.

    Its command-line entry point parses arguments but never calls the
    detection, so ``event_detection`` is called directly. It keeps at most one
    event (the largest excursion) per ``window_size_ms`` window, and events are
    amplitude outliers among the windows, so it assumes most windows hold no
    event. No sub-levels.
    """
    import importlib.util
    from argparse import Namespace

    import pandas as pd
    from pyabf import abfWriter

    script = _require(EXTERNAL / "autonanopore" / "AutoNanopore.py", "AutoNanopore")
    spec = importlib.util.spec_from_file_location("AutoNanopore", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with tempfile.TemporaryDirectory() as d:
        abf = Path(d) / "rec.abf"
        abfWriter.writeABF1(np.asarray(signal, dtype=np.float32)[None, :], str(abf), sr, units="pA")
        args = Namespace(output_path=d, window_size=window_size_ms, theta=theta, signal_direction=1)
        with open(os.devnull, "w") as null, contextlib.redirect_stdout(null):
            try:
                mod.event_detection(str(abf), args)
            except IndexError:
                # it crashes when no window passes its amplitude-outlier test
                # (events too frequent to be outliers): that is zero detections
                if len(pd.read_csv(Path(d) / "rec.csv")) == 0:
                    return []
                raise
        table = pd.read_csv(Path(d) / "rec.csv")
    return [{"start": r["Start time (ms)"] / 1e3, "end": r["End time (ms)"] / 1e3,
             "depth": float(abs(r["Amplitude (nA)"])), "n_levels": None, "level_bounds": []}
            for _, r in table.iterrows()]


METHODS = {
    "nano_ext[dpgmm]": (nano_ext, {"threshold_method": "dpgmm", "sublevel_method": "dpgmm"}),
    "nano_ext[gmm]": (nano_ext, {"threshold_method": "gmm", "sublevel_method": "gmm"}),
    "threshold": (threshold, {}),
    "pelt": (pelt, {}),
    "mosaic": (mosaic, {}),
    "threshold+nanotrees": (threshold_nanotrees, {}),
    "autonanopore": (autonanopore, {}),
}
