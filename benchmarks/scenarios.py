"""Synthetic benchmark scenarios (seeded; nothing is written to disk).

Every recording is sampled at 250 kHz behind a 4-pole Bessel low-pass
(30 kHz), which also band-limits the noise as in a real amplifier. Events
arrive as a Poisson process; half are single-level, half two-level (the
second level blocks 60 % as much as the first). SNR is the first-level
blockade depth divided by the total RMS noise of the recording.

Phase 1 varies one parameter at a time around a centre point:

* ``snr``   : 2, 3, 5, 8, 15 (dwell 1 ms)
* ``dwell`` : 10 µs … 100 ms mean event duration (SNR 8); the filter's
  10–90 % rise time is ≈ 11 µs, so this spans ~1–10 000 rise times.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from nano_ext.testing.synthetic import (
    generate_synthetic_signal,
    level_sampler,
    poisson_event_train,
    truth_table,
)

SR = 250_000.0
BASELINE = 200.0  # pA
DEPTH = 60.0  # pA, first-level blockade
FILTER_CUTOFF = 30_000.0
FILTER_ORDER = 4
PINK_FRACTION = 0.3  # share of the noise RMS that is 1/f


@dataclass(frozen=True)
class Scenario:
    axis: str
    value: float
    snr: float = 8.0
    dwell_mean_sec: float = 1e-3
    hum_amplitude: float = 0.0  # pA
    drift_rate: float = 0.0  # pA/s

    @property
    def rate_hz(self) -> float:
        return min(50.0, 0.2 / self.dwell_mean_sec)  # events cover ~≤ 20 % of the time

    @property
    def duration_sec(self) -> float:
        return float(min(30.0, max(4.0, 120.0 / self.rate_hz)))  # ~≥ 100 events if possible

    def label(self) -> str:
        return f"{self.axis}={self.value:g}"


def phase1_scenarios(quick: bool = False) -> list[Scenario]:
    snrs = [2, 3, 5, 8, 15] if not quick else [3, 8]
    dwells = [10e-6, 30e-6, 100e-6, 300e-6, 1e-3, 10e-3, 100e-3] if not quick else [30e-6, 1e-3, 10e-3]
    return ([Scenario("snr", s, snr=s) for s in snrs]
            + [Scenario("dwell", d, dwell_mean_sec=d) for d in dwells])


def _white_gain(cutoff: float, order: int, fs: float) -> float:
    """RMS gain of the Bessel filter for white noise sampled at *fs*."""
    from scipy.signal import bessel, sosfreqz

    sos = bessel(order, cutoff, btype="low", norm="mag", fs=fs, output="sos")
    _, h = sosfreqz(sos, worN=8192, fs=fs)
    return float(np.sqrt(np.mean(np.abs(h) ** 2)))


def make_recording(sc: Scenario, seed: int):
    """(signal float32, sampling rate, truth table, generator metadata)."""
    rng = np.random.default_rng(seed)
    make = level_sampler(BASELINE, DEPTH, sc.dwell_mean_sec, dwell="lognormal", dwell_sigma=0.5,
                         n_levels=[1, 2], level_depths=[DEPTH, 0.6 * DEPTH])
    events = poisson_event_train(sc.duration_sec, sc.rate_hz, make, rng,
                                 min_gap_sec=max(20e-6, 0.2 * sc.dwell_mean_sec))
    sigma = DEPTH / sc.snr
    sigma_pink = PINK_FRACTION * sigma
    sigma_white_out = np.sqrt(sigma ** 2 - sigma_pink ** 2)
    # noise is filtered with the signal: scale the white part so its RMS after
    # the filter is sigma_white_out (1/f noise is essentially unaffected)
    gain = _white_gain(FILTER_CUTOFF, FILTER_ORDER, SR)
    res = generate_synthetic_signal(
        duration_sec=sc.duration_sec, sampling_rate=SR, baseline_current=BASELINE,
        white_noise_std=sigma_white_out / gain, pink_noise_std=sigma_pink,
        events=events, drift_rate=sc.drift_rate, seed=seed + 1000,
        filter_cutoff=FILTER_CUTOFF, filter_order=FILTER_ORDER, filter_noise=True,
        hum_amplitude=sc.hum_amplitude,
    )
    noise = res.signal_data.signal - res.clean_signal
    meta = {**asdict(sc), "seed": seed, "duration_sec": sc.duration_sec, "rate_hz": sc.rate_hz,
            "noise_rms": float(noise.std()), "n_events": len(events),
            "filter_delay_us": res.filter_delay_sec * 1e6}
    return res.signal_data.signal.astype(np.float32), SR, truth_table(res), meta
