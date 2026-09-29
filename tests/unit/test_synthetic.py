"""Tests for the synthetic generator's realism options and ground truth."""

from __future__ import annotations

import numpy as np
import pytest

from nano_ext.testing.synthetic import (
    SyntheticEventSpec,
    generate_synthetic_signal,
    level_sampler,
    poisson_event_train,
    truth_table,
)

SR = 250_000.0


def _one_event(**kw):
    ev = [SyntheticEventSpec(0.05, [(0.001, 100.0), (0.0005, 150.0)])]
    return generate_synthetic_signal(duration_sec=0.1, sampling_rate=SR, events=ev,
                                     white_noise_std=0.0, pink_noise_std=0.0, seed=1, **kw)


class TestBackwardCompatibility:
    def test_defaults_reproduce_previous_signal(self):
        a = generate_synthetic_signal(duration_sec=0.5, sampling_rate=50_000, seed=4)
        b = generate_synthetic_signal(duration_sec=0.5, sampling_rate=50_000, seed=4,
                                      hum_amplitude=0.0, hf_noise_std=0.0, filter_cutoff=None)
        np.testing.assert_array_equal(a.signal_data.signal, b.signal_data.signal)
        assert a.unfiltered_clean_signal is None and a.filter_delay_sec == 0.0


class TestAnalogFilter:
    def test_step_gets_rise_time_and_delay(self):
        res = _one_event(filter_cutoff=30_000.0)
        x = res.clean_signal
        i = int(0.05 * SR)
        # ideal step before the filter, gradual transition after it
        assert res.unfiltered_clean_signal[i] == 100.0
        assert 100.0 < x[i + 1] < 200.0
        assert abs(x[i + 20] - 100.0) < 1.0  # settled well inside the level
        # 10-90 % rise time of a 4-pole Bessel ≈ 0.34 / fc ≈ 11 µs
        y = (200.0 - x[i - 5:i + 30]) / 100.0
        t10, t90 = np.argmax(y > 0.1), np.argmax(y > 0.9)
        assert 1 <= t90 - t10 <= 5
        assert 5e-6 < res.filter_delay_sec < 20e-6

    def test_cutoff_above_nyquist_is_rejected(self):
        with pytest.raises(ValueError):
            _one_event(filter_cutoff=SR * 4)  # above the simulation grid's Nyquist

    def test_filtered_noise_is_band_limited(self):
        kw = dict(duration_sec=1.0, sampling_rate=SR, events=[], pink_noise_std=0.0, seed=2)
        raw = generate_synthetic_signal(white_noise_std=5.0, **kw).signal_data.signal
        filt = generate_synthetic_signal(white_noise_std=5.0, filter_cutoff=10_000.0,
                                         filter_noise=True, **kw).signal_data.signal
        assert filt.std() < 0.5 * raw.std()


class TestNoiseComponents:
    def test_hum_adds_mains_line(self):
        res = generate_synthetic_signal(duration_sec=1.0, sampling_rate=10_000, events=[],
                                        white_noise_std=0.5, pink_noise_std=0.0, seed=3,
                                        hum_frequency=60.0, hum_amplitude=4.0, hum_harmonics=3)
        x = res.signal_data.signal - 200.0
        spec = np.abs(np.fft.rfft(x)) / len(x) * 2
        f = np.fft.rfftfreq(len(x), 1 / 10_000)
        for k, amp in [(1, 4.0), (2, 2.0), (3, 4.0 / 3)]:
            assert spec[np.argmin(np.abs(f - 60.0 * k))] == pytest.approx(amp, rel=0.1)
        assert res.signal_data.metadata["hum_harmonics"] == 3

    def test_high_frequency_noise_slope(self):
        res = generate_synthetic_signal(duration_sec=2.0, sampling_rate=100_000, events=[],
                                        white_noise_std=0.0, pink_noise_std=0.0, seed=5,
                                        hf_noise_std=3.0, hf_noise_exponent=2.0)
        x = res.signal_data.signal - 200.0
        assert x.std() == pytest.approx(3.0, rel=0.02)
        p = np.abs(np.fft.rfft(x)) ** 2
        f = np.fft.rfftfreq(len(x), 1e-5)
        lo = p[(f > 1_000) & (f < 2_000)].mean()
        hi = p[(f > 10_000) & (f < 20_000)].mean()
        assert hi / lo == pytest.approx(100.0, rel=0.3)  # f² → ×100 per decade


class TestEventTrainAndTruth:
    def test_poisson_train_rate_and_no_overlap(self):
        rng = np.random.default_rng(0)
        make = level_sampler(200.0, 50.0, 1e-3, dwell="fixed")
        ev = poisson_event_train(20.0, 40.0, make, rng, min_gap_sec=1e-4)
        assert 700 < len(ev) < 900  # ≈ 40/s × 20 s, less the time spent in events
        starts = np.array([e.start_time for e in ev])
        ends = np.array([e.end_time for e in ev])
        assert np.all(starts[1:] >= ends[:-1] + 1e-4 - 1e-12)
        assert all(e.levels == [(1e-3, 150.0)] for e in ev)

    def test_level_sampler_levels_and_dwell(self):
        rng = np.random.default_rng(1)
        make = level_sampler(200.0, (40.0, 60.0), 2e-3, dwell="lognormal", n_levels=[2, 3],
                             level_depths=[50.0, 20.0])
        draws = [make(rng) for _ in range(2000)]
        assert {len(d) for d in draws} == {2, 3}
        assert all(d[0][1] == 150.0 and d[1][1] == 180.0 for d in draws)
        assert np.mean([sum(x for x, _ in d) for d in draws]) == pytest.approx(2e-3, rel=0.05)
        with pytest.raises(ValueError):
            level_sampler(200.0, 50.0, 1e-3, dwell="gamma")(rng)

    def test_truth_table_shifts_by_filter_delay(self):
        res = _one_event(filter_cutoff=30_000.0)
        (row,) = truth_table(res)
        assert row["start"] == pytest.approx(0.05 + res.filter_delay_sec)
        assert row["dwell"] == pytest.approx(0.0015)
        assert row["n_levels"] == 2 and row["level_currents"] == [100.0, 150.0]
        assert row["level_bounds"] == [pytest.approx(0.051 + res.filter_delay_sec)]
        # duration-weighted depth: (1 ms × 100 + 0.5 ms × 50) / 1.5 ms
        assert row["depth"] == pytest.approx(250.0 / 3)
