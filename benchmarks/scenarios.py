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

Phase 3 (``phase3_scenarios``) adds event rate, filter cutoff, drift, mains
hum and the number of sub-levels, again one at a time around the centre.
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
    hum_amplitude: float = 0.0  # pA (50 Hz with 3 harmonics when > 0)
    drift_rate: float = 0.0  # pA/s, linear drift
    drift_poly: tuple = ()  # polynomial drift coefficients (pA, time in s); overrides drift_rate
    rate: float | None = None  # events per second; default depends on the dwell time
    filter_cutoff: float = FILTER_CUTOFF
    n_levels: tuple = (1, 2)  # sub-level counts drawn uniformly per event
    level_depths: tuple = ()  # sub-level depths (pA) in order; () uses the default 60, 36, 48, 24

    @property
    def rate_hz(self) -> float:
        if self.rate is not None:
            return self.rate
        return min(50.0, 0.2 / self.dwell_mean_sec)  # events cover ~≤ 20 % of the time

    @property
    def duration_sec(self) -> float:
        cap = 60.0 if self.rate is not None else 30.0
        return float(min(cap, max(4.0, 120.0 / self.rate_hz)))  # ~≥ 100 events if possible

    def label(self) -> str:
        return f"{self.axis}={self.value:g}"


def phase1_scenarios(quick: bool = False) -> list[Scenario]:
    snrs = [2, 3, 5, 8, 15] if not quick else [3, 8]
    dwells = [10e-6, 30e-6, 100e-6, 300e-6, 1e-3, 10e-3, 100e-3] if not quick else [30e-6, 1e-3, 10e-3]
    return ([Scenario("snr", s, snr=s) for s in snrs]
            + [Scenario("dwell", d, dwell_mean_sec=d) for d in dwells])


def phase3_scenarios(axes=("rate", "cutoff", "drift", "hum", "levels")) -> list[Scenario]:
    """One-at-a-time variations around the phase-1 centre point (SNR 8, 1 ms)."""
    out = []
    if "rate" in axes:  # events per second
        out += [Scenario("rate", r, rate=r) for r in (1.0, 3.0, 10.0, 50.0, 200.0)]
    if "cutoff" in axes:  # amplifier low-pass (Hz); noise RMS kept at SNR 8
        out += [Scenario("cutoff", f, filter_cutoff=f) for f in (10e3, 30e3, 100e3)]
    if "drift" in axes:  # 0: none, 1: linear 5 pA/s, 2: quadratic wander peaking at +20 pA after 2 s
        out += [Scenario("drift", 0.0), Scenario("drift", 1.0, drift_rate=5.0),
                Scenario("drift", 2.0, drift_poly=(-5.0, 20.0, 0.0))]
    if "hum" in axes:  # 50 Hz mains amplitude (pA), + harmonics at 1/k
        out += [Scenario("hum", a, hum_amplitude=a) for a in (0.0, 3.0, 10.0)]
    if "levels" in axes:  # every event has this many sub-levels
        out += [Scenario("levels", k, n_levels=(k,)) for k in (1, 2, 3, 4)]
    return out


def _white_gain(cutoff: float, order: int, fs: float) -> float:
    """RMS gain of the Bessel filter for white noise sampled at *fs*."""
    from scipy.signal import bessel, sosfreqz

    sos = bessel(order, cutoff, btype="low", norm="mag", fs=fs, output="sos")
    _, h = sosfreqz(sos, worN=8192, fs=fs)
    return float(np.sqrt(np.mean(np.abs(h) ** 2)))


def make_recording(sc: Scenario, seed: int, max_duration_sec: float | None = None):
    """(signal float32, sampling rate, truth table, generator metadata).

    ``max_duration_sec`` shortens the recording (used for parameter tuning).
    """
    rng = np.random.default_rng(seed)
    duration = sc.duration_sec if max_duration_sec is None else min(sc.duration_sec, max_duration_sec)
    # sub-level depths: 60, 36, 48, 24 pA (adjacent levels differ by ≥ 12 pA)
    depths = list(sc.level_depths) or [DEPTH, 0.6 * DEPTH, 0.8 * DEPTH, 0.4 * DEPTH]
    make = level_sampler(BASELINE, DEPTH, sc.dwell_mean_sec, dwell="lognormal", dwell_sigma=0.5,
                         n_levels=list(sc.n_levels), level_depths=depths[:max(2, max(sc.n_levels))])
    events = poisson_event_train(duration, sc.rate_hz, make, rng,
                                 min_gap_sec=max(20e-6, 0.2 * sc.dwell_mean_sec))
    sigma = DEPTH / sc.snr
    sigma_pink = PINK_FRACTION * sigma
    sigma_white_out = np.sqrt(sigma ** 2 - sigma_pink ** 2)
    # noise is filtered with the signal: scale the white part so its RMS after
    # the filter is sigma_white_out (1/f noise is essentially unaffected)
    gain = _white_gain(sc.filter_cutoff, FILTER_ORDER, SR)
    drift = ({"drift_type": "polynomial", "drift_coefficients": list(sc.drift_poly)} if sc.drift_poly
             else {"drift_rate": sc.drift_rate})
    res = generate_synthetic_signal(
        duration_sec=duration, sampling_rate=SR, baseline_current=BASELINE,
        white_noise_std=sigma_white_out / gain, pink_noise_std=sigma_pink,
        events=events, seed=seed + 1000, **drift,
        filter_cutoff=sc.filter_cutoff, filter_order=FILTER_ORDER, filter_noise=True,
        hum_amplitude=sc.hum_amplitude, hum_harmonics=3,
    )
    noise = res.signal_data.signal - res.clean_signal
    meta = {**asdict(sc), "seed": seed, "duration_sec": duration, "rate_hz": sc.rate_hz,
            "noise_rms": float(noise.std()), "n_events": len(events),
            "filter_delay_us": res.filter_delay_sec * 1e6}
    return res.signal_data.signal.astype(np.float32), SR, truth_table(res), meta
