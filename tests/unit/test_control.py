"""Tests for negative control noise characterisation (preprocessing/control.py)."""

import numpy as np
import pytest

from nano_ext.models import DetectionConfig, SignalData
from nano_ext.preprocessing.control import ControlStats, compute_control_stats


def _make_control(n=50_000, sr=100_000.0, noise_std=0.02, offset=1.0):
    rng = np.random.default_rng(99)
    signal = (offset + rng.normal(0, noise_std, n)).astype(np.float32)
    return SignalData(signal=signal, sampling_rate=sr, units="pA")


class TestControlStats:
    def test_is_dataclass(self):
        stats = ControlStats(
            noise_std=0.01,
            baseline_mean=1.0,
            baseline_std=0.001,
            sampling_rate=100_000.0,
            n_samples=50_000,
        )
        assert stats.noise_std == 0.01
        assert stats.n_samples == 50_000


class TestComputeControlStats:
    def test_returns_control_stats(self):
        ctrl = _make_control()
        result = compute_control_stats(ctrl)
        assert isinstance(result, ControlStats)

    def test_noise_std_positive(self):
        ctrl = _make_control()
        result = compute_control_stats(ctrl)
        assert result.noise_std > 0

    def test_noise_std_scales_with_input(self):
        # Higher input noise → higher output noise_std (relative ordering preserved)
        ctrl_low = _make_control(noise_std=0.005)
        ctrl_high = _make_control(noise_std=0.05)
        result_low = compute_control_stats(ctrl_low)
        result_high = compute_control_stats(ctrl_high)
        assert result_low.noise_std < result_high.noise_std

    def test_baseline_mean_close_to_offset(self):
        offset = 2.5
        ctrl = _make_control(offset=offset, noise_std=0.01)
        result = compute_control_stats(ctrl)
        # baseline_mean is mean of the local_baseline, should be close to offset
        assert abs(result.baseline_mean - offset) < 0.1

    def test_sampling_rate_preserved(self):
        sr = 200_000.0
        ctrl = _make_control(sr=sr)
        result = compute_control_stats(ctrl)
        assert result.sampling_rate == sr

    def test_n_samples_correct(self):
        n = 30_000
        ctrl = _make_control(n=n)
        result = compute_control_stats(ctrl)
        assert result.n_samples == n

    def test_with_default_config(self):
        ctrl = _make_control()
        result = compute_control_stats(ctrl, config=None)
        assert isinstance(result, ControlStats)

    def test_with_explicit_config(self):
        ctrl = _make_control()
        config = DetectionConfig(apply_filter=True, filter_cutoff=10_000.0)
        result = compute_control_stats(ctrl, config=config)
        assert result.noise_std > 0

    def test_no_filter_path(self):
        ctrl = _make_control()
        config = DetectionConfig(apply_filter=False)
        result = compute_control_stats(ctrl, config=config)
        assert result.noise_std > 0

    def test_baseline_std_nonnegative(self):
        ctrl = _make_control()
        result = compute_control_stats(ctrl)
        assert result.baseline_std >= 0
