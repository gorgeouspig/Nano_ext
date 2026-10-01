"""Phase 2 benchmark: external tools, and every method tuned with the same budget.

Stages (each resumable; results go to ``results/``):

1. **tune** – every method runs 12 parameter settings (``GRIDS``) on two
   *tuning* recordings per scenario (seeds 100, 101; at most 10 s long).
   → ``phase2_tuning.csv``
2. **select** – per method, the setting with the best mean tuning score is
   chosen (a) separately for every scenario and (b) once for all scenarios
   ("global"), for two objectives: event detection (F1) and sub-levels
   (F1 × sub-level count accuracy, methods that report levels only).
   → ``phase2_selected.csv``
3. **test** – default and selected settings run on the *test* recordings of
   phase 1 (seeds 0–4, never used for tuning). Default-setting rows of the
   phase-1 methods are taken from ``phase1_runs.csv`` (same recordings, same
   code). → ``phase2_test.csv``, ``phase2_summary.csv``, ``phase2.png``

    bash benchmarks/external/setup.sh              # MOSAIC, Nano Trees, AutoNanopore
    python benchmarks/run_phase2.py --workers 4    # all stages (several hours)
    python benchmarks/run_phase2.py --stage test --workers 4
"""

from __future__ import annotations

import argparse
import itertools
import json
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

from adapters import METHODS  # noqa: E402
from common import evaluate  # noqa: E402
from scenarios import make_recording, phase1_scenarios  # noqa: E402

TUNING_SEEDS = (100, 101)
TEST_SEEDS = tuple(range(5))
TUNING_MAX_SEC = 10.0
LEVEL_METHODS = {"nano_ext[dpgmm]", "nano_ext[gmm]", "pelt", "mosaic", "threshold+nanotrees"}


def _grid(**axes) -> list[dict]:
    keys = list(axes)
    return [dict(zip(keys, vals)) for vals in itertools.product(*axes.values())]


_NANO = _grid(min_event_duration_sec=[None, 10e-6, 30e-6, 100e-6], filter_cutoff=[10e3, None, 60e3])
GRIDS = {  # 12 settings per method; None means the method's automatic default
    "nano_ext[dpgmm]": [{"threshold_method": "dpgmm", "sublevel_method": "dpgmm", **g} for g in _NANO],
    "nano_ext[gmm]": [{"threshold_method": "gmm", "sublevel_method": "gmm", **g} for g in _NANO],
    "threshold": _grid(k_sigma=[3.0, 4.0, 5.0, 6.0], k_exit=[0.5, 1.0, 2.0]),
    "pelt": _grid(penalty_factor=[0.5, 1.0, 2.0, 4.0, 8.0, 16.0], k_sigma=[2.0, 3.0]),
    "mosaic": _grid(eventThreshold=[3.0, 4.0, 5.0, 6.0], StepSize=[2.0, 3.0, 5.0]),
    "threshold+nanotrees": _grid(k_sigma=[4.0, 5.0],
                                 smallest_sublevel_pa=[5.0, 10.0, 20.0, 40.0, 80.0, 600.0]),
    "autonanopore": _grid(window_size_ms=[2, 5, 10, 30], theta=[0.5, 1.0, 1.5]),
    "rolling_median_2gmm": _grid(window_sec=[0.2, 1.0, 5.0], posterior=[0.5, 0.99],
                                 merge_gap_sec=[20e-6, 1e-3]),
}
for _m, _g in GRIDS.items():
    assert len(_g) == 12, _m


def _clean(params: dict) -> dict:
    return {k: v for k, v in params.items() if v is not None}


def _run(name: str, params: dict, signal, sr, truth) -> dict:
    fn, defaults = METHODS[name]
    t0 = time.perf_counter()
    try:
        det, err = fn(signal, sr, **{**defaults, **_clean(params)}), ""
    except Exception as exc:
        det, err = [], f"{type(exc).__name__}: {exc}"[:300]
    return {**evaluate(truth, det), "runtime_s": time.perf_counter() - t0, "error": err}


def _score(df: pd.DataFrame, objective: str) -> pd.Series:
    return df["f1"] if objective == "f1" else df["f1"] * df["level_acc"].fillna(0.0)


# ---------------------------------------------------------------------------
# stage 1: tuning
# ---------------------------------------------------------------------------

def tune_job(args):
    sc, seed, name = args
    signal, sr, truth, meta = make_recording(sc, seed, TUNING_MAX_SEC)
    rows = []
    for cid, params in enumerate(GRIDS[name]):
        rows.append({"axis": sc.axis, "value": sc.value, "seed": seed, "method": name, "config": cid,
                     "params": json.dumps(params), **_run(name, params, signal, sr, truth)})
    return rows


# ---------------------------------------------------------------------------
# stage 2: selection
# ---------------------------------------------------------------------------

def select(tuning: pd.DataFrame) -> pd.DataFrame:
    out = []
    for objective in ("f1", "f1_x_level_acc"):
        t = tuning.assign(score=_score(tuning, objective))
        if objective != "f1":
            t = t[t.method.isin(LEVEL_METHODS)]
        per = t.groupby(["method", "axis", "value", "config"]).score.mean().reset_index()
        for (m, a, v), g in per.groupby(["method", "axis", "value"]):
            best = g.loc[g.score.idxmax()]
            out.append({"objective": objective, "mode": "per_scenario", "method": m, "axis": a, "value": v,
                        "config": int(best.config), "tuning_score": best.score})
        glob = per.groupby(["method", "config"]).score.mean().reset_index()
        for m, g in glob.groupby("method"):
            best = g.loc[g.score.idxmax()]
            out.append({"objective": objective, "mode": "global", "method": m, "axis": "", "value": np.nan,
                        "config": int(best.config), "tuning_score": best.score})
    sel = pd.DataFrame(out)
    sel["params"] = [json.dumps(GRIDS[m][c]) for m, c in zip(sel.method, sel.config)]
    return sel


def sensitivity(tuning: pd.DataFrame) -> pd.DataFrame:
    """Spread of the tuning F1 over the 12 settings, per method and scenario."""
    per = tuning.groupby(["method", "axis", "value", "config"]).f1.mean().reset_index()
    return per.groupby(["method", "axis", "value"]).f1.agg(
        f1_min="min", f1_median="median", f1_max="max").reset_index()


# ---------------------------------------------------------------------------
# stage 3: test
# ---------------------------------------------------------------------------

def configs_to_test(sel: pd.DataFrame, name: str, axis: str, value: float) -> dict:
    """{setting label: params} for one method and scenario."""
    out = {"default": None}
    for _, r in sel[sel.method == name].iterrows():
        if r["mode"] == "global" or (r.axis == axis and np.isclose(r.value, value)):
            out[f"tuned_{r['mode']}_{r.objective}"] = GRIDS[name][int(r.config)]
    return out


MAX_RUN_SEC = 1800.0  # settings expected to take longer per test recording are not run


def estimated_runtime(tuning: pd.DataFrame, name: str, axis: str, value: float, params) -> float:
    """Test-recording run time predicted from tuning (scaled by recording length)."""
    if params is None:
        return 0.0
    cid = GRIDS[name].index(params)
    t = tuning[(tuning.method == name) & (tuning.axis == axis) & np.isclose(tuning.value, value)
               & (tuning.config == cid)].runtime_s.mean()
    sc = next(s for s in phase1_scenarios() if s.axis == axis and np.isclose(s.value, value))
    return float(t) * sc.duration_sec / min(sc.duration_sec, TUNING_MAX_SEC)


def test_job(args):
    sc, seed, name, settings, skip_default, estimates = args
    signal, sr, truth, meta = make_recording(sc, seed)
    done, rows = {}, []
    for label, params in settings.items():
        if label == "default" and skip_default:
            continue
        key = json.dumps(params, sort_keys=True)
        if key not in done and estimates.get(key, 0.0) > MAX_RUN_SEC:
            done[key] = {**evaluate(truth, []), "f1": np.nan, "recall": np.nan, "precision": np.nan,
                         "f1_any": np.nan, "runtime_s": np.nan,
                         "error": f"skipped: estimated {estimates[key]:.0f} s per recording"}
        if key not in done:
            done[key] = _run(name, params or {}, signal, sr, truth)
        rows.append({"axis": sc.axis, "value": sc.value, "seed": seed, "method": name, "setting": label,
                     "params": json.dumps(params), **done[key]})
    return rows


# ---------------------------------------------------------------------------

def _parallel(jobs, fn, workers, out_csv: Path, previous: list):
    rows, t0 = list(previous), time.time()
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(fn, j) for j in jobs]
        for k, fut in enumerate(as_completed(futures), 1):  # save each job as soon as it ends
            r = fut.result()
            rows += r
            pd.DataFrame(rows).to_csv(out_csv, index=False)
            if r:
                best = max(x["f1"] for x in r)
                print(f"[{k}/{len(jobs)}] {r[0]['method']} {r[0]['axis']}={r[0]['value']:g} seed {r[0]['seed']}"
                      f": best F1 {best:.2f} ({time.time() - t0:.0f}s)", flush=True)
    return pd.DataFrame(rows)


def _done_keys(csv: Path, keys) -> tuple[set, list]:
    if not csv.exists():
        return set(), []
    d = pd.read_csv(csv)
    return {tuple(r) for r in d[keys].itertuples(index=False)}, d.to_dict("records")


def plot(summary: pd.DataFrame, sens: pd.DataFrame, out_png: Path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    methods = list(GRIDS)
    colors = dict(zip(methods, plt.cm.tab10.colors))
    rows = [("f1", "default", "F1 (IoU ≥ 0.5), default settings"),
            ("f1", "tuned_global_f1", "F1, one tuned setting for all scenarios"),
            ("f1", "tuned_per_scenario_f1", "F1, tuned per scenario"),
            ("level_acc", "tuned_global_f1_x_level_acc", "Sub-level accuracy, tuned for levels (global)")]
    fig, axs = plt.subplots(len(rows) + 1, 2, figsize=(11, 3.1 * (len(rows) + 1)), squeeze=False)
    for c, (axis, xlabel) in enumerate([("snr", "SNR"), ("dwell", "Mean dwell time (s)")]):
        for r, (metric, setting, title) in enumerate(rows):
            ax = axs[r, c]
            d = summary[(summary.axis == axis) & (summary.setting == setting)]
            for m in methods:
                dm = d[d.method == m].sort_values("value")
                if dm.empty or dm[metric].isna().all():
                    continue
                ax.plot(dm.value, dm[metric], marker="o", ms=3.5, color=colors[m], label=m)
            ax.set_xscale("log")
            ax.set_ylim(-0.03, 1.03)
            ax.set_title(title, fontsize=9.5)
            ax.grid(alpha=0.3)
            if axis == "snr":
                ticks = sorted(summary[summary.axis == "snr"].value.unique())
                ax.set_xticks(ticks, [f"{t:g}" for t in ticks])
                ax.minorticks_off()
        ax = axs[len(rows), c]
        d = sens[sens.axis == axis]
        for m in methods:
            dm = d[d.method == m].sort_values("value")
            if dm.empty:
                continue
            ax.fill_between(dm.value, dm.f1_min, dm.f1_max, color=colors[m], alpha=0.12)
            ax.plot(dm.value, dm.f1_median, color=colors[m], lw=1)
        ax.set_xscale("log")
        if axis == "snr":
            ticks = sorted(d.value.unique())
            ax.set_xticks(ticks, [f"{t:g}" for t in ticks])
            ax.minorticks_off()
        ax.set_ylim(-0.03, 1.03)
        ax.set_title("Tuning F1 over the 12 settings (median, range)", fontsize=9.5)
        ax.set_xlabel(xlabel)
        ax.grid(alpha=0.3)
    axs[0, 0].legend(fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", choices=["all", "tune", "test", "report"], default="all")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--methods", default=",".join(GRIDS))
    ap.add_argument("--out", default=str(HERE / "results"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    methods = args.methods.split(",")
    scenarios = phase1_scenarios()

    if args.stage in ("all", "tune"):
        csv = out / "phase2_tuning.csv"
        done, prev = _done_keys(csv, ["axis", "value", "seed", "method"])
        jobs = [(sc, s, m) for m in methods for sc in scenarios for s in TUNING_SEEDS
                if (sc.axis, sc.value, s, m) not in done]
        print(f"tuning: {len(jobs)} jobs × 12 settings")
        _parallel(jobs, tune_job, args.workers, csv, prev)

    tuning = pd.read_csv(out / "phase2_tuning.csv")
    sel = select(tuning)
    sel.to_csv(out / "phase2_selected.csv", index=False, float_format="%.4g")
    sens = sensitivity(tuning)
    sens.to_csv(out / "phase2_sensitivity.csv", index=False, float_format="%.4g")

    if args.stage in ("all", "test"):
        csv = out / "phase2_test.csv"
        done, prev = _done_keys(csv, ["axis", "value", "seed", "method"])
        phase1 = pd.read_csv(out / "phase1_runs.csv")
        reuse = phase1[phase1.method.isin(methods)].assign(setting="default", params="null")
        jobs = []
        for m in methods:
            has_phase1 = m in set(phase1.method)
            for sc in scenarios:
                settings = configs_to_test(sel, m, sc.axis, sc.value)
                estimates = {json.dumps(p, sort_keys=True): estimated_runtime(tuning, m, sc.axis, sc.value, p)
                             for p in settings.values()}
                for s in TEST_SEEDS:
                    if (sc.axis, sc.value, s, m) not in done:
                        jobs.append((sc, s, m, settings, has_phase1, estimates))
        if not prev:
            prev = reuse.to_dict("records")
        print(f"test: {len(jobs)} jobs")
        _parallel(jobs, test_job, args.workers, csv, prev)

    if not (out / "phase2_test.csv").exists():
        return
    test = pd.read_csv(out / "phase2_test.csv")
    metrics = ["f1", "f1_any", "recall", "precision", "dwell_err", "depth_err", "level_acc", "bound_err_us",
               "runtime_s"]
    g = test.groupby(["axis", "value", "method", "setting"])[metrics]
    summary = g.mean().join(g.std(), rsuffix="_sd").reset_index()
    summary["n_seeds"] = g.size().values
    summary.to_csv(out / "phase2_summary.csv", index=False, float_format="%.4g")
    plot(summary, sens, out / "phase2.png")
    print(summary[summary.setting.isin(["default", "tuned_global_f1"])]
          .pivot_table(index=["axis", "value"], columns=["setting", "method"], values="f1").round(2).to_string())


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
