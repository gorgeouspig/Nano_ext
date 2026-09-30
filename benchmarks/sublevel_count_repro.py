"""Reproduce the sub-level level-count figure quoted in the README.

Synthetic recording: 30 s at 250 kHz, 200 pA open pore, white (5 pA) + 1/f
(2 pA) noise, ~217 events arriving every 30 ms + Exp(100 ms). Half are
single-level (110 pA), half two-level (80 pA then 130 pA); every level lasts
1 ms + Exp(3 ms). Detected events are matched to true ones by start time
(within 1 ms); the score is the fraction of matched events whose number of
sub-levels equals the true one.

    python benchmarks/sublevel_count_repro.py [--threshold-method gmm|dpgmm] [--jobs -1]

The README figure (100 % for ``dpgmm`` vs 66 % for ``gmm`` sub-levels) was
measured with the v1.1 threshold default, ``--threshold-method gmm``.
"""

from __future__ import annotations

import argparse
import time
import warnings

import numpy as np

from nano_ext.models import DetectionConfig
from nano_ext.pipeline import run_pipeline
from nano_ext.testing.synthetic import SyntheticEventSpec, generate_synthetic_signal


def make_recording():
    rng = np.random.default_rng(7)
    events, t = [], 0.2
    while t < 29.5:
        d = rng.exponential(0.003) + 0.001
        if rng.random() < 0.5:
            events.append(SyntheticEventSpec(t, [(d, 110.0)]))
        else:
            events.append(SyntheticEventSpec(t, [(d, 80.0), (rng.exponential(0.003) + 0.001, 130.0)]))
        t += rng.exponential(0.1) + 0.03
    sd = generate_synthetic_signal(duration_sec=30, sampling_rate=250_000, events=events, seed=2).signal_data
    sd.signal = sd.signal.astype(np.float32)
    return sd, events


def score(result, events):
    truth_start = np.array([e.start_time for e in events])
    truth_lv = np.array([e.n_levels for e in events])
    det = np.array([e.start_time for e in result.events])
    lv = np.array([e.n_levels for e in result.events])
    j = np.clip(np.searchsorted(truth_start, det), 1, len(events) - 1)
    j = np.where(np.abs(truth_start[j - 1] - det) < np.abs(truth_start[j] - det), j - 1, j)
    ok = np.abs(truth_start[j] - det) < 0.001
    found, true = lv[ok], truth_lv[j[ok]]
    return {
        "matched": int(ok.sum()),
        "accuracy": float(np.mean(found == true)),
        "acc_1level": float(np.mean(found[true == 1] == 1)),
        "acc_2level": float(np.mean(found[true == 2] == 2)),
        "mean_levels": float(found.mean()),
        "true_mean_levels": float(true.mean()),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--threshold-method", default="gmm", choices=["gmm", "dpgmm"])
    ap.add_argument("--methods", default="gmm,dpgmm,bocpd")
    ap.add_argument("--jobs", type=int, default=-1)
    args = ap.parse_args()
    warnings.simplefilter("ignore")

    sd, events = make_recording()
    print(f"{len(events)} true events; threshold method: {args.threshold_method}")
    for m in args.methods.split(","):
        t0 = time.time()
        cfg = DetectionConfig(threshold_method=args.threshold_method, sublevel_method=m,
                              n_jobs=args.jobs, baseline_window_sec=1.0)
        s = score(run_pipeline(sd, cfg), events)
        print(f"{m:6s} matched {s['matched']}/{len(events)}  level-count accuracy {s['accuracy']:.2f} "
              f"(1-level {s['acc_1level']:.2f}, 2-level {s['acc_2level']:.2f})  "
              f"mean levels found {s['mean_levels']:.2f} (true {s['true_mean_levels']:.2f})  "
              f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
