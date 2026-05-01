"""Tests for lowpass_filter and auto_cutoff."""

import numpy as np
import pytest

from nano_ext.preprocessing.filters import auto_cutoff, lowpass_filter

SR = 100_000.0  # 100 kHz


class TestLowpassFilter:
    def test_bessel_preserves_length(self):
        sig = np.random.default_rng(0).normal(0, 1, 10_000)
        out = lowpass_filter(sig, SR, cutoff=10_000.0, filter_type="bessel")
        assert len(out) == len(sig)

    def test_butterworth_preserves_length(self):
        sig = np.random.default_rng(0).normal(0, 1, 10_000)
        out = lowpass_filter(sig, SR, cutoff=10_000.0, filter_type="butterworth")
        assert len(out) == len(sig)

    def test_dc_signal_passthrough(self):
        sig = np.ones(10_000) * 50.0
        out = lowpass_filter(sig, SR, cutoff=10_000.0)
        np.testing.assert_allclose(out, sig, atol=1e-6)

    def test_attenuates_above_cutoff(self):
        # DC + high-frequency component well above cutoff
        n = 100_000
        t = np.arange(n) / SR
        dc = 100.0
        high = 5.0 * np.sin(2 * np.pi * 40_000 * t)  # 40 kHz >> cutoff 10 kHz
        out = lowpass_filter(dc + high, SR, cutoff=10_000.0)
        # Residual after removing DC should be much smaller than original amplitude
        assert np.std(out - dc) < 1.0

    def test_default_cutoff_is_fs_over_10(self):
        # When cutoff=None, should default to SR/10 without raising
        sig = np.random.default_rng(0).normal(0, 1, 5_000)
        out = lowpass_filter(sig, SR, cutoff=None)
        assert len(out) == len(sig)

    def test_cutoff_at_nyquist_raises(self):
        sig = np.ones(1_000)
        with pytest.raises(ValueError, match="Nyquist"):
            lowpass_filter(sig, SR, cutoff=SR / 2)

    def test_cutoff_above_nyquist_raises(self):
        sig = np.ones(1_000)
        with pytest.raises(ValueError, match="Nyquist"):
            lowpass_filter(sig, SR, cutoff=SR / 2 + 1)

    def test_negative_cutoff_raises(self):
        sig = np.ones(1_000)
        with pytest.raises(ValueError):
            lowpass_filter(sig, SR, cutoff=-100.0)

    def test_zero_cutoff_raises(self):
        sig = np.ones(1_000)
        with pytest.raises(ValueError):
            lowpass_filter(sig, SR, cutoff=0.0)

    def test_unknown_filter_type_raises(self):
        sig = np.ones(1_000)
        with pytest.raises(ValueError, match="filter_type"):
            lowpass_filter(sig, SR, cutoff=1_000.0, filter_type="chebyshev")


class TestAutoCutoff:
    def test_default_factor(self):
        assert auto_cutoff(100_000.0) == 10_000.0

    def test_custom_factor(self):
        assert auto_cutoff(200_000.0, factor=20.0) == 10_000.0

    def test_proportional(self):
        assert auto_cutoff(50_000.0, factor=5.0) == 10_000.0
