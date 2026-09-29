"""Synthetic recordings for the GUI demo video (step 1 of 3).

Writes a small, realistic-looking data folder::

    <work>/nanopore/2026-09-14/            (empty, for the folder list)
    <work>/nanopore/2026-09-21/
    <work>/nanopore/2026-09-28/pore07_10min.abf     10 × 60 s sweeps, 250 kHz, 300 MB
                              /pore07_control.abf   30 s, no events
                              /pore06_iv_check.abf  20 s, no events
                              /notes.txt

``pore07_10min.abf`` holds two event populations (short single-level
blockades to 140 pA and two-level ones at 80 → 115 pA from a ~200 pA open
pore), a zap artifact at 243.0–243.6 s and a two-level showcase event at
exactly 250.000 s (4.5 ms at 80 pA, then 2.5 ms at 115 pA) that the video
zooms into.

    python scripts/demo_video/make_data.py [--work scripts/demo_video/_work]

Then run ``record.py`` and ``compose.py``.
"""

from __future__ import annotations

import argparse
import time
import warnings
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
SR, SWEEP, N_SWEEPS = 250_000, 60.0, 10


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work", default=str(HERE / "_work"), help="Working folder (default: %(default)s)")
    args = ap.parse_args()

    warnings.simplefilter("ignore")
    import pyabf.abfWriter as abf_writer
    from nano_ext.testing.synthetic import SyntheticEventSpec, generate_synthetic_signal

    root = Path(args.work) / "nanopore"
    day = root / "2026-09-28"
    for d in (root / "2026-09-14", root / "2026-09-21", day):
        d.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    rng = np.random.default_rng(11)
    sweeps = np.empty((N_SWEEPS, int(SR * SWEEP)), dtype=np.float32)
    for k in range(N_SWEEPS):
        events, t = [], 0.1
        while t < SWEEP - 0.1:
            g = k * SWEEP + t
            if abs(g - 250.0) < 0.15 or 242.8 < g < 243.8:  # room for the showcase event / artifact
                t += 0.2
                continue
            if rng.random() < 0.5:
                events.append(SyntheticEventSpec(t, [(rng.exponential(0.0008) + 0.0003, 140.0)]))
            else:
                events.append(SyntheticEventSpec(t, [(rng.exponential(0.004) + 0.001, 80.0),
                                                     (rng.exponential(0.002) + 0.001, 115.0)]))
            t += rng.exponential(0.18) + 0.02
        if k == 4:  # showcase event at 250.000 s (sweep 4 covers 240–300 s)
            events.append(SyntheticEventSpec(10.0, [(0.0045, 80.0), (0.0025, 115.0)]))
        sig = generate_synthetic_signal(duration_sec=SWEEP, sampling_rate=SR, events=events,
                                        seed=100 + k).signal_data.signal
        if k == 4:  # zap artifact 243.0–243.6 s
            a, b = int(3.0 * SR), int(3.6 * SR)
            sig[a:b] += 420 * np.sin(np.linspace(0, 70, b - a)) * np.hanning(b - a) ** 0.3
        sweeps[k] = sig
    abf_writer.writeABF1(sweeps, str(day / "pore07_10min.abf"), SR)

    for name, dur, seed in [("pore07_control.abf", 30, 7), ("pore06_iv_check.abf", 20, 8)]:
        s = generate_synthetic_signal(duration_sec=dur, sampling_rate=SR, events=[], seed=seed)
        abf_writer.writeABF1(s.signal_data.signal.astype(np.float32)[None, :], str(day / name), SR)
    (day / "notes.txt").write_text("pore07: 1 M KCl, +180 mV\n")
    print(f"wrote {day} in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
