"""Agreement between methods on public recordings (no ground truth).

Datasets (never committed; fetched or unpacked into ``_external/data/``):

* ``autonanopore_demo`` – the 300 s, 250 kHz ABF shipped with AutoNanopore
  (``1.abf.zip`` in its repository, see ``external/setup.sh``).
* ``poriscope_sample`` – Poriscope's sample data (FRDR, DOI 10.20383/103.01695,
  CC BY 4.0), fetched into ``_external/data/poriscope/`` by
  ``external/fetch_poriscope_data.py``. Its recordings are Chimera VC400
  ``.log`` files (raw int16, scaled with the companion ``.json`` as in
  Poriscope's ``ChimeraReader20240501``). The first ``MAX_SEC`` seconds of
  the first channel found are used, decimated (FIR, zero phase) to at most
  500 kHz and cached as ``.npy`` next to the data.

Every method runs with its default setting and with the single setting tuned
for detection in phase 2. The trace is converted to pA and its sign chosen so
that the open pore is positive and blockades point down. Outputs:

* ``results/realdata_summary.csv`` – events, median dwell time and depth per
  method and setting (PELT runs in 10 s chunks here)
* ``results/realdata_agreement.csv`` – pairwise agreement: F1 of one method's
  events against another's (IoU ≥ 0.5 and any overlap; symmetric)
* ``results/realdata_reference.csv`` – Poriscope sample only: recall of each
  method against the 15 visually reviewed events that the dataset's
  ``tutorial_events.sqlite3`` lists for 0–50 s (detected with a 2000 pA
  threshold, so shallower events are not in it and precision is not
  defined), and the number of events each method reports in that window

Event lists stay in ``_external/data/events/`` (derived from third-party data).

    bash benchmarks/external/setup.sh autonanopore
    python benchmarks/run_realdata.py --workers 4
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "RAYON_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
os.environ.setdefault("PYTHONWARNINGS", "ignore")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from adapters import METHODS  # noqa: E402
from common import evaluate, match_events  # noqa: E402
from run_phase2 import GRIDS, _clean  # noqa: E402

DATA = HERE / "_external" / "data"


def prepare(name: str) -> Path | None:
    if name == "autonanopore_demo":
        abf = DATA / "autonanopore_demo.abf"
        if not abf.exists():
            src = HERE / "_external" / "autonanopore" / "1.abf.zip"
            if not src.exists():
                return None
            DATA.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(src) as z:
                member = [m for m in z.namelist() if m.lower().endswith(".abf")][0]
                abf.write_bytes(z.read(member))
        return abf
    if name == "poriscope_sample":
        d = DATA / "poriscope"
        files = sorted(p for p in d.glob("*.log") if p.with_suffix(".json").exists()) if d.exists() else []
        files = [p for p in files if p.stat().st_size > 10 * 2**20] or files  # skip near-empty channels
        if not files:
            return None
        cache = d / f"{files[0].stem}.{MAX_SEC:g}s.npy"  # cropped, decimated, in pA
        if not cache.exists():
            y, sr = _load_chimera(files[0], MAX_SEC)
            np.save(cache, y.astype(np.float32))
            cache.with_suffix(".json").write_text(json.dumps({"source": files[0].name, "sampling_rate": sr}))
        return cache
    raise ValueError(name)


MAX_SEC = 60.0  # Chimera recordings are cropped to this length
MAX_RATE = 500e3  # and decimated to at most this sampling rate


def _load_chimera(path: Path, max_sec: float):
    """Chimera VC400 ``.log`` (2024-05 format: no header, int16, ``.json`` settings)."""
    from scipy.signal import decimate

    cfg = json.loads(path.with_suffix(".json").read_text())
    g, ch = cfg["global"], cfg["channel"]
    sr = float(g["f_sampling"])
    scale = 1e12 * ((2 * 2 * 2.048 / 2**16) / g["filter_gain"]) / ch["tia_gain"]
    raw = np.memmap(path, dtype=np.int16, mode="r")[: int(max_sec * sr)]
    y = raw.astype(np.float64) * scale - ch["i_offset"] * 1e12
    q = int(np.ceil(sr / MAX_RATE))
    if q > 1:
        y = decimate(y, q, ftype="fir", zero_phase=True)
        sr /= q
    return y, sr


def load_pA(path: Path, max_sec: float = MAX_SEC):
    """Signal in pA, sign flipped if needed so the open pore is positive."""
    if path.suffix.lower() == ".npy":  # cache written by prepare()
        y = np.load(path).astype(np.float64)
        sr = float(json.loads(path.with_suffix(".json").read_text())["sampling_rate"])
    elif path.suffix.lower() == ".log":
        y, sr = _load_chimera(path, max_sec)
    else:
        import pyabf

        abf = pyabf.ABF(str(path))
        y = np.asarray(abf.sweepY, dtype=np.float64)
        unit = (abf.adcUnits[0] if abf.adcUnits else "pA").strip()
        y *= {"nA": 1e3, "pA": 1.0, "uA": 1e6, "µA": 1e6}.get(unit, 1.0)
        sr = float(abf.dataRate)
    if np.median(y) < 0:
        y = -y
    return y.astype(np.float32), sr


# (dataset, method): reason. Runs expected to take many hours are not attempted.
SKIP = {
    ("poriscope_sample", "pelt"): (
        "skipped: PELT time grows faster than linearly with the number of samples on this "
        "almost event-free 500 kHz trace (55 s for 0.5 s, 164 s for 1 s of signal); 10 s chunks "
        "would take hours per chunk"),
}


def run_job(args):
    dataset, path, method, setting, params = args
    out = DATA / "events" / dataset
    done = out / f"{method}__{setting}.summary.json"
    if done.exists():
        return json.loads(done.read_text())
    if (dataset, method) in SKIP:
        return {"dataset": dataset, "method": method, "setting": setting, "runtime_s": np.nan,
                "error": SKIP[(dataset, method)], "n_events": np.nan, "median_dwell_ms": np.nan,
                "median_depth_pa": np.nan, "duration_s": np.nan}
    signal, sr = load_pA(path)
    fn, defaults = METHODS[method]
    if method == "pelt":
        params = {**(params or {}), "chunk_sec": 10.0}  # 75 M samples do not fit in one PELT run
    t0 = time.perf_counter()
    try:
        events, err = fn(signal, sr, **{**defaults, **_clean(params or {})}), ""
    except Exception as exc:
        events, err = [], f"{type(exc).__name__}: {exc}"[:300]
    dt = time.perf_counter() - t0
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{method}__{setting}.json").write_text(json.dumps(events))
    row = {"dataset": dataset, "method": method, "setting": setting, "runtime_s": dt, "error": err,
            "n_events": len(events),
            "median_dwell_ms": float(np.median([1e3 * (e["end"] - e["start"]) for e in events])) if events else np.nan,
            "median_depth_pa": float(np.median([e["depth"] for e in events])) if events else np.nan,
            "duration_s": len(signal) / sr}
    done.write_text(json.dumps(row))
    return row


def agreement(dataset: str, keys: list[tuple[str, str]]) -> pd.DataFrame:
    ev = {k: json.loads((DATA / "events" / dataset / f"{k[0]}__{k[1]}.json").read_text()) for k in keys}
    rows = []
    for a, b in itertools.combinations(keys, 2):
        m = evaluate(ev[a], ev[b])  # a as reference; F1 is symmetric
        rows.append({"dataset": dataset, "method_a": a[0], "setting_a": a[1], "method_b": b[0],
                     "setting_b": b[1], "n_a": len(ev[a]), "n_b": len(ev[b]),
                     "f1": m["f1"], "f1_any": m["f1_any"]})
    return pd.DataFrame(rows)


REF_WINDOW = (0.0, 50.0)  # s; the reviewed events cover channel 3 over this window


def poriscope_reference() -> list[dict] | None:
    """Reviewed events of ``tutorial_events.sqlite3`` (channel 3), in s.

    Each row stores ``absolute_start`` (5 MHz sample index of the stored
    window), the padding before/after the event and the raw window; the
    raw-data channels 1, 3 and 4 hold identical data.
    """
    import sqlite3

    db = DATA / "poriscope" / "tutorial_events.sqlite3"
    if not db.exists():
        return None
    con = sqlite3.connect(db)
    (sr,) = con.execute("SELECT samplerate FROM channels WHERE channel_id = 3").fetchone()
    out = []
    for st, pb, pa, nbytes in con.execute(
            "SELECT absolute_start, padding_before, padding_after, length(raw_data) FROM events "
            "WHERE channel_id = 3 ORDER BY absolute_start"):
        n = nbytes // 8 - pb - pa
        out.append({"start": (st + pb) / sr, "end": (st + pb + n) / sr, "depth": np.nan, "n_levels": None})
    con.close()
    return out


def reference_table(keys: list[tuple[str, str]]) -> pd.DataFrame | None:
    ref = poriscope_reference()
    if ref is None:
        return None
    lo, hi = REF_WINDOW
    rows = []
    for method, setting in keys:
        ev = json.loads((DATA / "events" / "poriscope_sample" / f"{method}__{setting}.json").read_text())
        ev = [e for e in ev if lo <= e["start"] < hi]
        m = evaluate(ref, ev)
        any_hit = {i for i, _, _ in match_events(ref, ev, 1e-12)}
        rows.append({"method": method, "setting": setting, "n_reference": len(ref), "n_detected": len(ev),
                     "recall_any": len(any_hit) / len(ref), "recall_iou50": m["recall"],
                     "dwell_err": m["dwell_err"]})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--datasets", default="autonanopore_demo,poriscope_sample")
    ap.add_argument("--methods", default=",".join(METHODS))
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--out", default=str(HERE / "results"))
    args = ap.parse_args()
    out = Path(args.out)
    sel = pd.read_csv(out / "phase2_selected.csv")
    tuned = {r.method: GRIDS[r.method][int(r.config)] for r in sel.itertuples()
             if r.mode == "global" and r.objective == "f1"}
    jobs = []
    for ds in args.datasets.split(","):
        path = prepare(ds)
        if path is None:
            print(f"{ds}: data not available, skipped")
            continue
        for m in args.methods.split(","):
            jobs += [(ds, path, m, "default", None), (ds, path, m, "tuned_global_f1", tuned.get(m))]
    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for fut in as_completed([ex.submit(run_job, j) for j in jobs]):
            r = fut.result()
            rows.append(r)
            print(f"{r['dataset']} {r['method']} {r['setting']}: {r['n_events']} events "
                  f"({r['runtime_s']:.0f}s){' ' + r['error'] if r['error'] else ''}", flush=True)
    summary = pd.DataFrame(rows).sort_values(["dataset", "method", "setting"])
    summary.to_csv(out / "realdata_summary.csv", index=False, float_format="%.4g")
    agree = pd.concat([agreement(ds, [(r.method, r.setting) for r in g.itertuples() if not r.error])
                       for ds, g in summary.groupby("dataset")], ignore_index=True)
    agree.to_csv(out / "realdata_agreement.csv", index=False, float_format="%.3g")
    g = summary[(summary.dataset == "poriscope_sample") & (summary.error == "")]
    ref = reference_table([(r.method, r.setting) for r in g.itertuples()]) if len(g) else None
    if ref is not None:
        ref.to_csv(out / "realdata_reference.csv", index=False, float_format="%.3g")
        print(ref.to_string(index=False))
    print(summary.to_string(index=False))


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
