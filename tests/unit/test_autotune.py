"""Tests for automated parameter tuning (detection/autotune.py)."""

import numpy as np
import pytest

from nano_ext.detection.autotune import estimate_noise_floor, suggest_config
from nano_ext.models import DetectionConfig, SignalData


def _make_signal(n=10_000, sr=100_000.0, noise_std=0.01, signal_range=1.0):
    rng = np.random.default_rng(42)
    signal = rng.normal(loc=0.0, scale=noise_std, size=n).astype(np.float32)
    # Stretch to desired range so noise_fraction = noise_std / signal_range
    signal = signal + np.linspace(0, signal_range * 0.5, n, dtype=np.float32)
    return SignalData(signal=signal, sampling_rate=sr, units="pA")


class TestEstimateNoiseFloor:
    def test_white_noise_estimate(self):
        rng = np.random.default_rng(0)
        true_std = 0.05
        signal = rng.normal(0, true_std, 50_000).astype(np.float32)
        est = estimate_noise_floor(signal)
        # MAD-based estimate should be within 20% of true std for white noise
        assert abs(est - true_std) / true_std < 0.20

    def test_short_signal(self):
        rng = np.random.default_rng(1)
        signal = rng.normal(0, 0.1, 500).astype(np.float32)
        est = estimate_noise_floor(signal)
        assert est > 0

    def test_long_signal_subsampled(self):
        # Signal longer than 100k: should subsample and still return a value
        rng = np.random.default_rng(2)
        signal = rng.normal(0, 0.02, 200_000).astype(np.float32)
        est = estimate_noise_floor(signal)
        assert est > 0

    def test_returns_float(self):
        signal = np.ones(1000, dtype=np.float32)
        # Constant signal → all diffs are 0 → noise_floor = 0
        est = estimate_noise_floor(signal)
        assert isinstance(est, float)
        assert est == 0.0


class TestSuggestConfig:
    def test_returns_detection_config(self):
        sd = _make_signal()
        cfg = suggest_config(sd)
        assert isinstance(cfg, DetectionConfig)

    def test_filter_cutoff_is_sr_over_10(self):
        sr = 200_000.0
        sd = _make_signal(sr=sr)
        cfg = suggest_config(sd)
        assert cfg.filter_cutoff == pytest.approx(sr / 10.0)

    def test_clean_signal_short_duration(self):
        # Very clean signal: noise_fraction < 0.02 → duration_factor = 2.0
        rng = np.random.default_rng(3)
        sr = 100_000.0
        signal = rng.normal(0, 0.001, 100_000).astype(np.float32)
        # Add a large constant to make signal_range large
        signal = signal + np.linspace(0, 5.0, 100_000, dtype=np.float32)
        sd = SignalData(signal=signal, sampling_rate=sr, units="pA")
        cfg = suggest_config(sd)
        expected_cutoff = sr / 10.0
        assert cfg.min_event_duration_sec == pytest.approx(2.0 / expected_cutoff)

    def test_noisy_signal_long_duration(self):
        # Noisy signal: noise_fraction > 0.05 → duration_factor = 5.0
        rng = np.random.default_rng(4)
        sr = 100_000.0
        signal = rng.normal(0, 0.5, 100_000).astype(np.float32)
        # Keep signal_range small so noise_fraction is large
        signal = signal + 0.01 * np.linspace(0, 1.0, 100_000, dtype=np.float32)
        sd = SignalData(signal=signal, sampling_rate=sr, units="pA")
        cfg = suggest_config(sd)
        expected_cutoff = sr / 10.0
        assert cfg.min_event_duration_sec == pytest.approx(5.0 / expected_cutoff)

    def test_overrides_respected(self):
        sd = _make_signal()
        cfg = suggest_config(sd, gmm_max_components=3, bic_criterion="aic")
        assert cfg.gmm_max_components == 3
        assert cfg.bic_criterion == "aic"

    def test_baseline_window_clamped(self):
        # 0.1s signal → 10% = 0.01s, clamped to min 1.0s
        sd = _make_signal(n=10_000, sr=100_000.0)  # 0.1s duration
        cfg = suggest_config(sd)
        assert cfg.baseline_window_sec >= 1.0

    def test_baseline_window_capped(self):
        # 100s signal → 10% = 10s, capped to max 5.0s
        sd = _make_signal(n=10_000_000, sr=100_000.0)  # 100s
        cfg = suggest_config(sd)
        assert cfg.baseline_window_sec <= 5.0

    def test_merge_gap_positive(self):
        sd = _make_signal()
        cfg = suggest_config(sd)
        assert cfg.merge_gap_sec > 0
