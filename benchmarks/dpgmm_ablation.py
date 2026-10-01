"""Ablation of Nano_ext's DPGMM: remove one ingredient at a time.

Variants (``adapters.ABLATIONS``; switched in the benchmark adapter, the
package defaults are unchanged):

* ``nano_ext[dpgmm]`` – unchanged
* ``-no_dp_prior`` – finite symmetric Dirichlet weight prior instead of the
  Dirichlet-process prior
* ``-no_merge`` – no merging of components whose mixture is unimodal
* ``-no_thinning`` – sub-level fits on every sample instead of one sample per
  filter correlation time

Every variant runs with default settings (no tuning) on the test seeds 0–4 of

* the phase-1 scenarios (SNR, dwell time),
* ``levels``: the phase-3 sub-level axis (1–4 levels per 1 ms event; adjacent
  depths 12 pA apart, about 1.6 σ),
* ``levels_close_long``: the same depths with 2 ms per level,
* ``levels_sep_long``: depths 60, 90, 30, 120 pA (≥ 30 pA, about 4 σ, apart)
  with 2 ms per level.

All recordings include the 1/f noise of the standard scenarios (30 % of the
noise RMS). Outputs ``results/dpgmm_ablation_runs.csv`` (every run, resumable)
and ``results/dpgmm_ablation_summary.csv`` (mean and SD over seeds).

    python benchmarks/dpgmm_ablation.py --workers 4
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "RAYON_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
os.environ.setdefault("PYTHONWARNINGS", "ignore")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from adapters import ABLATIONS  # noqa: E402
from common import evaluate  # noqa: E402
from scenarios import Scenario, make_recording, phase1_scenarios, phase3_scenarios  # noqa: E402

TEST_SEEDS = tuple(range(5))
SEPARATED = (60.0, 90.0, 30.0, 120.0)


def scenarios() -> list[Scenario]:
    out = phase1_scenarios() + phase3_scenarios(("levels",))
    for k in (1, 2, 3, 4):
        out.append(Scenario("levels_close_long", k, n_levels=(k,), dwell_mean_sec=2e-3 * k))
        out.append(Scenario("levels_sep_long", k, n_levels=(k,), dwell_mean_sec=2e-3 * k,
                            level_depths=SEPARATED))
    return out


def job(args):
    sc, seed, variant = args
    signal, sr, truth, _ = make_recording(sc, seed)
    fn, kw = ABLATIONS[variant]
    t0 = time.perf_counter()
    try:
        det, err = fn(signal, sr, **kw), ""
    except Exception as exc:
        det, err = [], f"{type(exc).__name__}: {exc}"[:300]
    return {"axis": sc.axis, "value": sc.value, "seed": seed, "method": variant,
            **evaluate(truth, det), "runtime_s": time.perf_counter() - t0, "error": err}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--axes", default="", help="comma-separated axes to run (default: all)")
    ap.add_argument("--out", default=str(HERE / "results"))
    args = ap.parse_args()
    out = Path(args.out)
    csv = out / "dpgmm_ablation_runs.csv"
    prev = pd.read_csv(csv).to_dict("records") if csv.exists() else []
    done = {(r["axis"], float(r["value"]), int(r["seed"]), r["method"]) for r in prev}
    axes = set(filter(None, args.axes.split(",")))
    jobs = [(sc, s, v) for sc in scenarios() if not axes or sc.axis in axes
            for s in TEST_SEEDS for v in ABLATIONS if (sc.axis, float(sc.value), s, v) not in done]
    print(f"{len(jobs)} jobs", flush=True)
    rows, t0 = prev, time.time()
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(job, j) for j in jobs]
        for k, fut in enumerate(as_completed(futures), 1):
            r = fut.result()
            rows.append(r)
            pd.DataFrame(rows).to_csv(csv, index=False)
            print(f"[{k}/{len(jobs)}] {r['method']} {r['axis']}={r['value']:g} seed {r['seed']}: "
                  f"F1 {r['f1']:.2f} level acc {r['level_acc']:.2f} ({time.time() - t0:.0f}s)", flush=True)
    runs = pd.DataFrame(rows)
    runs["f1_x_level_acc"] = runs.f1 * runs.level_acc.fillna(0.0)
    metrics = ["f1", "level_acc", "f1_x_level_acc", "bound_err_us", "depth_err", "runtime_s"]
    g = runs.groupby(["axis", "value", "method"])[metrics]
    summary = g.mean().join(g.std(), rsuffix="_sd").reset_index()
    summary["n_seeds"] = g.size().values
    summary.to_csv(out / "dpgmm_ablation_summary.csv", index=False, float_format="%.4g")
    print(summary.pivot_table(index=["axis", "value"], columns="method", values=["f1", "level_acc"])
          .round(2).to_string())


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
