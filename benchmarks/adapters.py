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

def pelt(signal, sr, penalty_factor=1.0, k_sigma=3.0, min_size=2, chunk_sec=None):
    """PELT segmentation of the baseline-subtracted trace (L2 cost).

    Uses ``ruptures.KernelCPD(kernel="linear")``, the C implementation of
    PELT for the L2 (mean-shift) cost, with the BIC-type penalty
    ``penalty_factor · 2 σ² ln n``. Segments whose mean lies more than
    ``k_sigma·σ`` below the baseline are event levels; runs of consecutive
    event segments form one event with one sub-level per segment.
    ``chunk_sec`` segments long recordings in independent chunks.
    """
    import ruptures as rpt

    if chunk_sec is not None and len(signal) > 2 * chunk_sec * sr:
        # long recordings: segment in chunks (memory and time grow fast with n);
        # events cut by a chunk boundary are dropped
        n, step, out = len(signal), int(chunk_sec * sr), []
        for a in range(0, n, step):
            b = min(n, a + step)
            for e in pelt(signal[a:b], sr, penalty_factor, k_sigma, min_size):
                if e["start"] > 0 and e["end"] < (b - a) / sr:
                    out.append({**e, "start": e["start"] + a / sr, "end": e["end"] + a / sr,
                                "level_bounds": [t + a / sr for t in e["level_bounds"]]})
        return out
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


# ---------------------------------------------------------------------------
# Rolling-median baseline + two-component GMM threshold
# ---------------------------------------------------------------------------

def _histogram_gmm2(centers, counts, mu_b, sd_b, mu_e, sd_e, w_e, n_iter=300):
    """EM for a two-component 1-D Gaussian mixture fitted to a histogram.

    Component 0 is the baseline, component 1 the event cluster. Returns
    ``(weights, means, sds)``.
    """
    w = np.array([1.0 - w_e, w_e])
    mu = np.array([mu_b, mu_e], dtype=np.float64)
    sd = np.array([sd_b, sd_e], dtype=np.float64)
    c = counts / counts.sum()
    floor = 0.5 * (centers[1] - centers[0])
    for _ in range(n_iter):
        pdf = w * np.exp(-0.5 * ((centers[:, None] - mu) / sd) ** 2) / sd
        resp = pdf / np.maximum(pdf.sum(axis=1, keepdims=True), 1e-300)
        r = resp * c[:, None]
        nk = r.sum(axis=0)
        if np.any(nk <= 1e-12):
            break
        new_mu = (r * centers[:, None]).sum(axis=0) / nk
        new_sd = np.sqrt((r * (centers[:, None] - new_mu) ** 2).sum(axis=0) / nk)
        new_sd = np.maximum(new_sd, floor)
        done = np.allclose(new_mu, mu, atol=1e-6 * sd_b) and np.allclose(new_sd, sd, rtol=1e-6)
        w, mu, sd = nk, new_mu, new_sd
        if done:
            break
    return w / w.sum(), mu, sd


def rolling_median_2gmm(signal, sr, window_sec=1.0, posterior=0.5, n_bins=512,
                        merge_gap_sec=20e-6, min_duration_sec=0.0):
    """Rolling-median baseline and a two-component GMM threshold.

    Re-implementation based on the description of cluster-based event
    detection in Wei et al. 2026 (bioRxiv, doi 10.64898/2026.05.07.723187);
    not the original authors' code.

    1. Baseline B(t): median of the current over a centred window of
       ``window_sec`` (fewer samples at the ends, at least one).
    2. Residual C(t) = I(t) − B(t).
    3. A two-component Gaussian mixture is fitted by EM to the histogram of
       C (``n_bins`` bins): one baseline cluster and one event cluster.
    4. Samples whose posterior probability of the event cluster is at least
       ``posterior`` belong to events; the threshold is the residual where
       that probability is reached, between the two cluster means
       (``posterior = 0.5``: the Bayes decision boundary).
    5. Post-processing: events closer than ``merge_gap_sec`` are merged,
       events shorter than ``min_duration_sec`` are dropped.

    Assumptions where the description leaves details open: the window length,
    the number of bins, the initialisation (baseline cluster at the median
    of C with its MAD width; event cluster at the 1st percentile with twice
    that width and weight 0.05) and the post-processing values. Without a
    separate event cluster (event mean not below the baseline mean by more
    than one baseline SD) no events are reported. No sub-level analysis.
    """
    import pandas as pd

    x = np.asarray(signal, dtype=np.float64)
    win = max(1, int(round(window_sec * sr)))
    base = pd.Series(x).rolling(win, center=True, min_periods=1).median().to_numpy()
    r = x - base
    counts, edges = np.histogram(r, bins=n_bins)
    centers = 0.5 * (edges[:-1] + edges[1:])
    med = float(np.median(r))
    mad = max(float(1.4826 * np.median(np.abs(r - med))), 1e-9)
    w, mu, sd = _histogram_gmm2(centers, counts.astype(np.float64), med, mad,
                                float(np.percentile(r, 1.0)), 2.0 * mad, 0.05)
    b, e = (0, 1) if mu[0] >= mu[1] else (1, 0)
    if not mu[e] < mu[b] - sd[b]:
        return []
    grid = np.linspace(mu[e], mu[b], 2000)
    pb = w[b] * np.exp(-0.5 * ((grid - mu[b]) / sd[b]) ** 2) / sd[b]
    pe = w[e] * np.exp(-0.5 * ((grid - mu[e]) / sd[e]) ** 2) / sd[e]
    post = pe / np.maximum(pb + pe, 1e-300)
    above = np.flatnonzero(post >= posterior)
    if len(above) == 0:
        return []
    thr = grid[above[-1]]  # highest residual still assigned to events
    inside = r <= thr
    edges_i = np.flatnonzero(np.diff(np.concatenate(([0], inside.astype(np.int8), [0]))))
    starts, ends = list(edges_i[::2]), list(edges_i[1::2])
    gap = int(merge_gap_sec * sr)
    ms, me = [], []
    for s0, e0 in zip(starts, ends):
        if ms and s0 - me[-1] <= gap:
            me[-1] = e0
        else:
            ms.append(s0)
            me.append(e0)
    min_len = max(1, int(min_duration_sec * sr))
    return [{"start": s0 / sr, "end": e0 / sr, "depth": float(-r[s0:e0].mean()),
             "n_levels": None, "level_bounds": []}
            for s0, e0 in zip(ms, me) if e0 - s0 >= min_len]


# ---------------------------------------------------------------------------
# DPGMM ablations (benchmark-side switches; the package defaults are unchanged)
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _dpgmm_ablation(ablate: str):
    """Temporarily remove one ingredient of Nano_ext's DPGMM fits.

    * ``no_dp_prior`` – finite symmetric Dirichlet weight prior instead of
      the Dirichlet-process (stick-breaking) prior
    * ``no_merge`` – adjacent components with a unimodal mixture are not merged
    * ``no_thinning`` – sub-level fits use every sample instead of one per
      filter correlation time (``thinning_step`` returns 1)

    The first two affect both the event threshold and the sub-level fits; the
    third only the sub-level fits (the threshold fit is not thinned).
    """
    from nano_ext.detection import bayes_mixture as bm

    saved = (bm.fit_dpgmm_1d, bm.thinning_step, bm.BayesianGaussianMixture)
    if ablate == "no_dp_prior":
        class _FiniteDirichlet(saved[2]):
            def __init__(self, **kw):
                kw["weight_concentration_prior_type"] = "dirichlet_distribution"
                super().__init__(**kw)
        bm.BayesianGaussianMixture = _FiniteDirichlet
    elif ablate == "no_merge":
        def _fit(*a, **kw):
            kw["merge_unimodal"] = False
            return saved[0](*a, **kw)
        bm.fit_dpgmm_1d = _fit
    elif ablate == "no_thinning":
        bm.thinning_step = lambda sampling_rate, filter_cutoff: 1
    elif ablate != "none":
        raise ValueError(f"unknown ablation {ablate!r}")
    try:
        yield
    finally:
        bm.fit_dpgmm_1d, bm.thinning_step, bm.BayesianGaussianMixture = saved


def nano_ext_ablation(signal, sr, ablate="none", **config):
    """``nano_ext[dpgmm]`` with one DPGMM ingredient removed (see ``_dpgmm_ablation``)."""
    with _dpgmm_ablation(ablate):
        return nano_ext(signal, sr, threshold_method="dpgmm", sublevel_method="dpgmm", **config)


ABLATIONS = {f"nano_ext[dpgmm]{'' if a == 'none' else '-' + a}": (nano_ext_ablation, {"ablate": a})
             for a in ("none", "no_dp_prior", "no_merge", "no_thinning")}


METHODS = {
    "nano_ext[dpgmm]": (nano_ext, {"threshold_method": "dpgmm", "sublevel_method": "dpgmm"}),
    "nano_ext[gmm]": (nano_ext, {"threshold_method": "gmm", "sublevel_method": "gmm"}),
    "threshold": (threshold, {}),
    "pelt": (pelt, {}),
    "mosaic": (mosaic, {}),
    "threshold+nanotrees": (threshold_nanotrees, {}),
    "autonanopore": (autonanopore, {}),
    "rolling_median_2gmm": (rolling_median_2gmm, {}),
}
