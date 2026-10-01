"""Agreement between methods on public recordings (no ground truth).

Datasets (never committed; fetched or unpacked into ``_external/data/``):

* ``autonanopore_demo`` – the 300 s, 250 kHz ABF shipped with AutoNanopore
  (``1.abf.zip`` in its repository, see ``external/setup.sh``).
* ``poriscope_sample`` – Poriscope's sample data (DOI 10.20383/103.01599);
  downloaded by hand into ``_external/data/poriscope/`` if available.

Every method runs with its default setting and with the single setting tuned
for detection in phase 2. The trace is converted to pA and its sign chosen so
that the open pore is positive and blockades point down. Outputs:

* ``results/realdata_summary.csv`` – events, median dwell time and depth per
  method and setting (PELT runs in 10 s chunks here)
* ``results/realdata_agreement.csv`` – pairwise agreement: F1 of one method's
  events against another's (IoU ≥ 0.5 and any overlap; symmetric)

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
from common import evaluate  # noqa: E402
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
        files = sorted((DATA / "poriscope").glob("*.abf")) if (DATA / "poriscope").exists() else []
        return files[0] if files else None
    raise ValueError(name)


def load_pA(path: Path):
    """Signal in pA, sign flipped if needed so the open pore is positive."""
    import pyabf

    abf = pyabf.ABF(str(path))
    y = np.asarray(abf.sweepY, dtype=np.float64)
    unit = (abf.adcUnits[0] if abf.adcUnits else "pA").strip()
    y *= {"nA": 1e3, "pA": 1.0, "uA": 1e6, "µA": 1e6}.get(unit, 1.0)
    if np.median(y) < 0:
        y = -y
    return y.astype(np.float32), float(abf.dataRate)


def run_job(args):
    dataset, path, method, setting, params = args
    out = DATA / "events" / dataset
    done = out / f"{method}__{setting}.summary.json"
    if done.exists():
        return json.loads(done.read_text())
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
    print(summary.to_string(index=False))


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
