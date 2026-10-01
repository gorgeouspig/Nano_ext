"""Shortest detectable event behind a 10 kHz amplifier filter.

Recordings are sampled at 250 kHz behind a 4-pole 10 kHz Bessel filter that
also shapes the noise (white + 1/f + capacitive f² noise, mixed so the f²
part dominates above a few kHz as in a real headstage). Single-level events
of nearly fixed duration (lognormal, σ 0.1) are detected by Nano_ext with the
filter declared as already applied and ``min_event_duration_sec = k / fc``.

Outputs:

* ``results/short_event_limit.csv`` – recall (any overlap and IoU ≥ 0.5),
  false events per second, median measured / true depth per condition
* ``results/short_event_limit_attenuation.csv`` – noise-free peak blockade
  of a filtered rectangular pulse vs its duration

    python benchmarks/short_event_limit.py --workers 4
"""

from __future__ import annotations

import argparse
import itertools
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "RAYON_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from adapters import nano_ext  # noqa: E402
from common import evaluate, match_events  # noqa: E402
from nano_ext.testing.synthetic import (  # noqa: E402
    SyntheticEventSpec,
    generate_synthetic_signal,
    level_sampler,
    poisson_event_train,
    truth_table,
)

SR = 250_000.0
FC = 10_000.0
BASELINE = 200.0
DEPTH = 100.0
DURATION = 10.0
RATE = 10.0
NOISE_MIX = {"white_noise_std": 1.0, "pink_noise_std": 0.3, "hf_noise_std": 3.0}  # before the filter

DWELLS_US = (20, 30, 50, 70, 100, 150, 200, 300, 500, 1000)
SNRS = (5, 10, 20, 40)
FACTORS = (0.5, 1.0, 2.0, 3.0, 5.0)  # min_event_duration = k / fc
SEEDS = (0, 1)


def _signal(events, noise_scale, seed):
    noise = {k: v * noise_scale for k, v in NOISE_MIX.items()}
    return generate_synthetic_signal(
        duration_sec=DURATION, sampling_rate=SR, baseline_current=BASELINE, events=events,
        seed=seed, filter_cutoff=FC, filter_order=4, filter_noise=True, hf_noise_exponent=2.0,
        **noise)


def noise_scale_for(sigma: float) -> float:
    """Scale of NOISE_MIX giving output noise RMS *sigma* behind the filter."""
    res = _signal([], 1.0, seed=12345)
    return sigma / float(np.std(res.signal_data.signal - res.clean_signal))


def run_one(args):
    dwell_us, snr, k, seed, scale = args
    rng = np.random.default_rng(seed)
    make = level_sampler(BASELINE, DEPTH, dwell_us * 1e-6, dwell="lognormal", dwell_sigma=0.1)
    events = poisson_event_train(DURATION, RATE, make, rng, min_gap_sec=2e-3)
    res = _signal(events, scale, seed + 1000)
    truth = truth_table(res)
    det = nano_ext(res.signal_data.signal.astype(np.float32), SR, apply_filter=False,
                   pre_applied_filter_cutoff=FC, min_event_duration_sec=k / FC)
    m = evaluate(truth, det)
    any_m = match_events(truth, det, 1e-12)
    matched = {j for _, j, _ in any_m}
    depth_ratio = [det[j]["depth"] / truth[i]["depth"] for i, j, _ in any_m if truth[i]["depth"]]
    return {"dwell_us": dwell_us, "snr": snr, "k": k, "min_dur_us": 1e6 * k / FC, "seed": seed,
            "n_true": len(truth), "n_det": len(det),
            "recall_any": len(any_m) / max(1, len(truth)), "recall_iou": m["recall"],
            "false_per_s": (len(det) - len(matched)) / DURATION,
            "depth_ratio": float(np.median(depth_ratio)) if depth_ratio else np.nan}


def attenuation() -> pd.DataFrame:
    rows = []
    for d_us in np.unique(np.r_[np.arange(5, 100, 5), np.arange(100, 1001, 50)]):
        ev = [SyntheticEventSpec(start_time=0.01, levels=[(d_us * 1e-6, BASELINE - DEPTH)])]
        res = generate_synthetic_signal(duration_sec=0.03, sampling_rate=SR, baseline_current=BASELINE,
                                        white_noise_std=0.0, pink_noise_std=0.0, events=ev, seed=0,
                                        filter_cutoff=FC, filter_order=4)
        y = res.clean_signal
        rows.append({"dwell_us": d_us, "peak_fraction": float((BASELINE - y.min()) / DEPTH),
                     "fwhm_us": 1e6 * float((y < BASELINE - 0.5 * (BASELINE - y.min())).sum()) / SR})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--out", default=str(HERE / "results"))
    args = ap.parse_args()
    out = Path(args.out)
    attenuation().to_csv(out / "short_event_limit_attenuation.csv", index=False, float_format="%.4g")
    scales = {snr: noise_scale_for(DEPTH / snr) for snr in SNRS}
    jobs = [(d, s, k, seed, scales[s]) for d, s, k, seed in itertools.product(DWELLS_US, SNRS, FACTORS, SEEDS)]
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        rows = list(ex.map(run_one, jobs, chunksize=1))
    df = pd.DataFrame(rows).groupby(["snr", "k", "min_dur_us", "dwell_us"], as_index=False).mean(numeric_only=True)
    df.drop(columns="seed").to_csv(out / "short_event_limit.csv", index=False, float_format="%.3g")
    print(df.pivot_table(index=["snr", "k"], columns="dwell_us", values="recall_any").round(2).to_string())


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
