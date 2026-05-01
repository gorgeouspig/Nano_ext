"""Tests for baseline estimation helpers and estimate_baseline."""

import numpy as np
import pytest

from nano_ext.preprocessing.baseline import (
    _estimate_noise,
    _estimate_trend,
    estimate_baseline,
)

slow = pytest.mark.slow

SR = 10_000.0  # 10 kHz


class TestEstimateNoise:
    def test_mad_gaussian(self):
        rng = np.random.default_rng(0)
        noise = rng.normal(0.0, 5.0, 10_000)
        std = _estimate_noise(noise, method="mad")
        assert abs(std - 5.0) < 0.5

    def test_std_method(self):
        rng = np.random.default_rng(0)
        noise = rng.normal(0.0, 3.0, 10_000)
        std = _estimate_noise(noise, method="std")
        assert abs(std - 3.0) < 0.3

    def test_empty_returns_fallback(self):
        std = _estimate_noise(np.array([]), method="mad")
        assert std == 1.0

    def test_mad_robust_to_outliers(self):
        # MAD should be unaffected by a few extreme outliers
        rng = np.random.default_rng(1)
        noise = rng.normal(0.0, 2.0, 10_000)
        noise[::100] = 1000.0  # inject large outliers
        std = _estimate_noise(noise, method="mad")
        assert abs(std - 2.0) < 0.5

    def test_unknown_method_raises(self):
        with pytest.raises(ValueError):
            _estimate_noise(np.array([1.0, 2.0]), method="unknown")


class TestEstimateTrend:
    def test_none_returns_zeros(self):
        sig = np.random.default_rng(0).normal(100.0, 1.0, 1_000)
        trend = _estimate_trend(sig, SR, method="none", order=1)
        np.testing.assert_array_equal(trend, np.zeros(len(sig)))

    def test_linear_recovers_slope(self):
        n = 10_000
        t = np.arange(n) / SR
        sig = 5.0 * t + 100.0  # pure linear, no noise
        trend = _estimate_trend(sig, SR, method="linear", order=1)
        np.testing.assert_allclose(trend, sig, atol=0.5)

    def test_polynomial_recovers_quadratic(self):
        n = 5_000
        t = np.arange(n) / SR
        sig = 2.0 * t**2 + 3.0 * t + 50.0
        trend = _estimate_trend(sig, SR, method="polynomial", order=2)
        np.testing.assert_allclose(trend, sig, atol=5.0)

    def test_spline_smooth(self):
        n = 5_000
        t = np.arange(n) / SR
        sig = np.sin(2 * np.pi * 0.5 * t) * 10 + 100.0
        trend = _estimate_trend(sig, SR, method="spline", order=3)
        assert len(trend) == n

    def test_unknown_method_raises(self):
        sig = np.ones(100)
        with pytest.raises(ValueError):
            _estimate_trend(sig, SR, method="wavelet", order=1)


class TestEstimateBaseline:
    # Use small window_sec so window_samples stays small → Rust call stays fast
    WINDOW = 0.05  # 500 samples at 10 kHz

    @slow
    def test_flat_signal_baseline_near_signal(self):
        """Flat signal with noise: baseline should be close to the signal level."""
        n = 5_000
        rng = np.random.default_rng(0)
        sig = 100.0 * np.ones(n) + rng.normal(0.0, 1.0, n)
        result = estimate_baseline(sig, SR, detrend_method="none", window_sec=self.WINDOW)
        np.testing.assert_allclose(result.local_baseline, 100.0, atol=5.0)

    @slow
    def test_residual_equals_signal_minus_baseline(self):
        """residual should equal signal − local_baseline when detrend='none'."""
        n = 5_000
        rng = np.random.default_rng(1)
        sig = 100.0 * np.ones(n) + rng.normal(0.0, 1.0, n)
        result = estimate_baseline(sig, SR, detrend_method="none", window_sec=self.WINDOW)
        np.testing.assert_allclose(
            result.residual,
            sig - result.local_baseline,
            atol=1e-6,
        )

    @slow
    def test_noise_std_close_to_true_std(self):
        """Noise estimate should be within 50% of true sigma."""
        n = 10_000
        rng = np.random.default_rng(2)
        true_std = 2.0
        sig = 100.0 * np.ones(n) + rng.normal(0.0, true_std, n)
        result = estimate_baseline(sig, SR, detrend_method="none", window_sec=self.WINDOW)
        assert abs(result.noise_std - true_std) / true_std < 0.5

    @slow
    def test_baseline_result_has_correct_shapes(self):
        n = 2_000
        sig = np.random.default_rng(3).normal(100.0, 1.0, n)
        result = estimate_baseline(sig, SR, detrend_method="none", window_sec=self.WINDOW)
        assert len(result.local_baseline) == n
        assert len(result.trend) == n
        assert len(result.residual) == n
        assert isinstance(result.noise_std, float)

    @slow
    def test_events_do_not_contaminate_baseline(self):
        """Events in the signal should not pull the baseline estimate down much."""
        n = 10_000
        rng = np.random.default_rng(4)
        sig = 100.0 * np.ones(n) + rng.normal(0.0, 1.0, n)
        sig[2_000:2_500] = 50.0
        sig[6_000:6_500] = 50.0
        result = estimate_baseline(sig, SR, detrend_method="none", window_sec=self.WINDOW)
        mid_baseline = result.local_baseline[3_500:5_000]
        np.testing.assert_allclose(mid_baseline, 100.0, atol=5.0)
