"""Phase 1 benchmark: event detection and sub-levels vs SNR and dwell time.

Runs every method in ``adapters.METHODS`` with its default parameters on the
scenarios of ``scenarios.phase1_scenarios`` (several seeds each), then writes

    results/phase1_runs.csv       one row per (scenario, seed, method)
    results/phase1_summary.csv    mean and standard deviation over seeds
    results/phase1_resources.csv  run time and peak memory, centre scenario
    results/phase1.png            figure

    pip install -r benchmarks/requirements.txt
    python benchmarks/run_phase1.py [--quick] [--seeds 5] [--workers 4]

Every method runs single-threaded. With ``--workers`` > 1 recordings are
processed in parallel, which makes the per-run times in phase1_runs.csv
noisier; the resource table is always measured serially in fresh processes.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
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
from scenarios import Scenario, make_recording, phase1_scenarios  # noqa: E402

METRICS = ["recall", "precision", "f1", "f1_any", "dwell_err", "depth_err", "level_acc",
           "bound_err_us", "runtime_s"]


def run_one(args):
    sc, seed, methods = args
    signal, sr, truth, meta = make_recording(sc, seed)
    rows = []
    for name in methods:
        fn, params = METHODS[name]
        t0 = time.perf_counter()
        try:
            detected = fn(signal, sr, **params)
            err = ""
        except Exception as exc:  # a method failing on a scenario is a result too
            detected, err = [], f"{type(exc).__name__}: {exc}"
        dt = time.perf_counter() - t0
        rows.append({"axis": sc.axis, "value": sc.value, "seed": seed, "method": name,
                     "n_events_true": meta["n_events"], "noise_rms": meta["noise_rms"],
                     "duration_sec": meta["duration_sec"], **evaluate(truth, detected),
                     "runtime_s": dt, "error": err})
    return rows


def summarise(runs: pd.DataFrame) -> pd.DataFrame:
    g = runs.groupby(["axis", "value", "method"])[METRICS]
    mean, std = g.mean(), g.std()
    out = mean.join(std, rsuffix="_sd").reset_index()
    out["n_seeds"] = g.size().values
    return out


def measure_resources(methods, out_csv: Path):
    """Run time and peak RSS per method on the centre scenario, one fresh process each."""
    rows = []
    for name in methods:
        p = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--_resource", name],
                           capture_output=True, text=True, env=os.environ.copy())
        line = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
        rows.append(json.loads(line[-1]) if line else {"method": name, "error": p.stderr[-300:]})
    pd.DataFrame(rows).to_csv(out_csv, index=False)
    return rows


def _resource_child(name: str):
    import resource

    sc = Scenario("snr", 8.0)
    signal, sr, _, meta = make_recording(sc, 0)
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    fn, params = METHODS[name]
    t0 = time.perf_counter()
    fn(signal, sr, **params)
    dt = time.perf_counter() - t0
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    scale = 1024 if sys.platform != "darwin" else 1  # ru_maxrss is KiB on Linux, bytes on macOS
    print(json.dumps({"method": name, "samples": len(signal), "runtime_s": round(dt, 3),
                      "peak_rss_mb": round(peak * scale / 2**20, 1),
                      "peak_rss_increase_mb": round((peak - before) * scale / 2**20, 1)}))


def plot(summary: pd.DataFrame, out_png: Path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    panels = [("f1", "F1 (IoU ≥ 0.5)", False), ("level_acc", "Sub-level count accuracy", False),
              ("dwell_err", "Median |dwell error| (rel.)", True), ("runtime_s", "Run time (s)", True)]
    axes_x = [("snr", "SNR (depth / noise RMS)"), ("dwell", "Mean dwell time (s)")]
    fig, axs = plt.subplots(len(axes_x), len(panels), figsize=(4.2 * len(panels), 3.4 * len(axes_x)))
    for r, (axis, xlabel) in enumerate(axes_x):
        sub = summary[summary.axis == axis]
        for c, (metric, title, logy) in enumerate(panels):
            ax = axs[r, c]
            for m in sorted(sub.method.unique()):
                d = sub[sub.method == m].sort_values("value")
                if d[metric].isna().all():
                    continue
                ax.errorbar(d.value, d[metric], yerr=d[metric + "_sd"], marker="o", ms=4, capsize=2, label=m)
            ax.set_xscale("log")
            if logy:
                ax.set_yscale("log")
            ax.set_xlabel(xlabel)
            ax.set_title(title, fontsize=10)
            ax.grid(alpha=0.3)
    axs[0, 0].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quick", action="store_true", help="fewer scenarios and 2 seeds")
    ap.add_argument("--seeds", type=int, default=None)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--methods", default=",".join(METHODS))
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--no-resources", action="store_true")
    ap.add_argument("--_resource", default=None, help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args._resource:
        return _resource_child(args._resource)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    methods = args.methods.split(",")
    seeds = range(args.seeds or (2 if args.quick else 5))
    jobs = [(sc, s, methods) for sc in phase1_scenarios(args.quick) for s in seeds]
    print(f"{len(jobs)} recordings × {len(methods)} methods")
    rows, t0 = [], time.time()
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for k, r in enumerate(ex.map(run_one, jobs), 1):
            rows += r
            out.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(rows).to_csv(out / "phase1_runs.csv", index=False)  # keep partial results
            print(f"[{k}/{len(jobs)}] {r[0]['axis']}={r[0]['value']:g} seed {r[0]['seed']}: "
                  + ", ".join(f"{x['method']} F1 {x['f1']:.2f}" for x in r)
                  + f"  ({time.time() - t0:.0f}s)", flush=True)
    runs = pd.DataFrame(rows)
    out.mkdir(parents=True, exist_ok=True)
    runs.to_csv(out / "phase1_runs.csv", index=False)
    summary = summarise(runs)
    summary.to_csv(out / "phase1_summary.csv", index=False, float_format="%.4g")
    plot(summary, out / "phase1.png")
    if not args.no_resources:
        measure_resources(methods, out / "phase1_resources.csv")
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(summary[["axis", "value", "method", "f1", "level_acc", "dwell_err", "depth_err",
                       "runtime_s"]].round(3).to_string(index=False))
    print(f"wrote {out}")


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
