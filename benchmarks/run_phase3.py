"""Phase 3 benchmark: more scenario axes with default and tuned settings.

Every method runs with its default setting and with the single ("global")
settings chosen in phase 2 for detection and for sub-levels
(``results/phase2_selected.csv``; nothing is re-tuned), on scenarios that vary
one more property at a time around the phase-1 centre (SNR 8, mean dwell 1 ms):

* ``rate``   – events per second: 1, 3, 10, 50, 200
* ``cutoff`` – amplifier low-pass: 10, 30, 100 kHz
* ``drift``  – none, linear 5 pA/s, quadratic wander (+20 pA)
* ``hum``    – 50 Hz mains (+ harmonics) of 0, 3, 10 pA
* ``levels`` – every event has 1, 2, 3 or 4 sub-levels

    python benchmarks/run_phase3.py --axes rate --workers 4
    python benchmarks/run_phase3.py --workers 4          # all axes

Writes ``results/phase3_runs.csv`` (resumable), ``phase3_summary.csv`` and
``phase3.png``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "RAYON_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
os.environ.setdefault("PYTHONWARNINGS", "ignore")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from run_phase2 import GRIDS, TEST_SEEDS, TUNING_MAX_SEC, _done_keys, _parallel, test_job  # noqa: E402
from scenarios import phase3_scenarios  # noqa: E402

AXES = ("rate", "cutoff", "drift", "hum", "levels")
LABELS = {"rate": "Events per second", "cutoff": "Amplifier low-pass (Hz)",
          "drift": "Drift (0 none, 1 linear, 2 quadratic)", "hum": "Mains hum amplitude (pA)",
          "levels": "Sub-levels per event"}


def global_settings(sel: pd.DataFrame, name: str) -> dict:
    out = {"default": None}
    for _, r in sel[(sel.method == name) & (sel["mode"] == "global")].iterrows():
        out[f"tuned_global_{r.objective}"] = GRIDS[name][int(r.config)]
    return out


def estimate(tuning: pd.DataFrame, name: str, params, duration: float) -> float:
    """Run time predicted from the phase-2 centre scenario, scaled by length."""
    if params is None:
        return 0.0
    cid = GRIDS[name].index(params)
    t = tuning[(tuning.method == name) & (tuning.axis == "snr") & np.isclose(tuning.value, 8.0)
               & (tuning.config == cid)].runtime_s.mean()
    return float(t) * duration / min(4.0, TUNING_MAX_SEC)


def plot(summary: pd.DataFrame, out_png: Path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    methods = list(GRIDS)
    colors = dict(zip(methods, plt.cm.tab10.colors))
    axes = [a for a in AXES if a in set(summary.axis)]
    rows = [("f1", "tuned_global_f1", "F1, one tuned setting"), ("f1", "default", "F1, default settings"),
            ("level_acc", "tuned_global_f1_x_level_acc", "Sub-level accuracy, one tuned setting")]
    fig, axs = plt.subplots(len(rows), len(axes), figsize=(3.6 * len(axes), 2.9 * len(rows)), squeeze=False)
    for c, axis in enumerate(axes):
        for r, (metric, setting, title) in enumerate(rows):
            ax = axs[r, c]
            d = summary[(summary.axis == axis) & (summary.setting == setting)]
            for m in methods:
                dm = d[d.method == m].sort_values("value")
                if dm.empty or dm[metric].isna().all():
                    continue
                ax.plot(dm.value, dm[metric], marker="o", ms=3.5, color=colors[m], label=m)
            if axis in ("rate", "cutoff"):
                ax.set_xscale("log")
            ticks = sorted(d.value.unique())
            ax.set_xticks(ticks, [f"{t:g}" for t in ticks])
            ax.minorticks_off()
            ax.set_ylim(-0.03, 1.03)
            ax.set_title(title, fontsize=9)
            ax.grid(alpha=0.3)
            if r == len(rows) - 1:
                ax.set_xlabel(LABELS[axis], fontsize=8.5)
    axs[0, 0].legend(fontsize=6.5, ncol=1)
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--axes", default=",".join(AXES))
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--methods", default=",".join(GRIDS))
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    methods = args.methods.split(",")
    csv = out / "phase3_runs.csv"
    if not args.report_only:
        sel = pd.read_csv(out / "phase2_selected.csv")
        tuning = pd.read_csv(out / "phase2_tuning.csv")
        done, prev = _done_keys(csv, ["axis", "value", "seed", "method"])
        jobs = []
        for sc in phase3_scenarios(tuple(args.axes.split(","))):
            for m in methods:
                settings = global_settings(sel, m)
                est = {json.dumps(p, sort_keys=True): estimate(tuning, m, p, sc.duration_sec)
                       for p in settings.values()}
                jobs += [(sc, s, m, settings, False, est) for s in TEST_SEEDS
                         if (sc.axis, sc.value, s, m) not in done]
        print(f"{len(jobs)} jobs")
        _parallel(jobs, test_job, args.workers, csv, prev)
    runs = pd.read_csv(csv)
    metrics = ["f1", "f1_any", "recall", "precision", "dwell_err", "depth_err", "level_acc", "bound_err_us",
               "runtime_s"]
    g = runs.groupby(["axis", "value", "method", "setting"])[metrics]
    summary = g.mean().join(g.std(), rsuffix="_sd").reset_index()
    summary["n_seeds"] = g.size().values
    summary.to_csv(out / "phase3_summary.csv", index=False, float_format="%.4g")
    plot(summary, out / "phase3.png")
    print(summary[summary.setting == "tuned_global_f1"]
          .pivot_table(index=["axis", "value"], columns="method", values="f1").round(2).to_string())


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
