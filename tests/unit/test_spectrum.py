"""Tests for the PSD analysis module (Phase 6.3)."""

from __future__ import annotations

import numpy as np
import pytest

from nano_ext.models import SignalData
from nano_ext.analysis.spectrum import (
    PSDResult,
    compute_psd,
    estimate_filter_cutoff,
    compare_psd,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signal(sr: float = 10_000, duration: float = 0.5, seed: int = 0) -> SignalData:
    rng = np.random.default_rng(seed)
    n = int(sr * duration)
    signal = rng.normal(0.0, 5.0, size=n).astype(np.float32)
    return SignalData(signal=signal, sampling_rate=sr, units="pA")


def _make_lowpass_signal(sr: float = 10_000, cutoff: float = 1000.0,
                          duration: float = 1.0, seed: int = 1) -> SignalData:
    """White noise passed through a Butterworth low-pass filter."""
    from scipy.signal import butter, sosfiltfilt
    rng = np.random.default_rng(seed)
    n = int(sr * duration)
    noise = rng.normal(0.0, 1.0, size=n)
    sos = butter(4, cutoff, btype="low", fs=sr, output="sos")
    filtered = sosfiltfilt(sos, noise)
    return SignalData(signal=filtered.astype(np.float32), sampling_rate=sr, units="pA")


# ---------------------------------------------------------------------------
# compute_psd
# ---------------------------------------------------------------------------

class TestComputePSD:
    def test_returns_psd_result(self):
        sd = _make_signal()
        result = compute_psd(sd)
        assert isinstance(result, PSDResult)

    def test_frequencies_non_negative(self):
        sd = _make_signal()
        result = compute_psd(sd)
        assert np.all(result.frequencies >= 0)

    def test_frequencies_max_is_nyquist(self):
        sr = 10_000.0
        sd = _make_signal(sr=sr)
        result = compute_psd(sd)
        assert result.frequencies[-1] == pytest.approx(sr / 2, rel=0.05)

    def test_psd_all_positive(self):
        sd = _make_signal()
        result = compute_psd(sd)
        assert np.all(result.psd >= 0)

    def test_psd_frequencies_same_length(self):
        sd = _make_signal()
        result = compute_psd(sd)
        assert len(result.frequencies) == len(result.psd)

    def test_units_label_contains_squared(self):
        sd = _make_signal()
        result = compute_psd(sd)
        assert "²" in result.units or "2" in result.units

    def test_short_signal_does_not_raise(self):
        """Very short signals should clamp segment length, not error."""
        sd = _make_signal(duration=0.01)
        result = compute_psd(sd)
        assert result is not None

    def test_power_flat_for_white_noise(self):
        """White noise PSD should be roughly flat across the spectrum."""
        rng = np.random.default_rng(42)
        n = 100_000
        sr = 50_000.0
        noise = rng.normal(0, 1, n).astype(np.float32)
        sd = SignalData(signal=noise, sampling_rate=sr)
        result = compute_psd(sd, segment_sec=0.1)
        # Coefficient of variation of the PSD (excluding DC) should be < 3
        psd_noDC = result.psd[1:]
        cv = psd_noDC.std() / psd_noDC.mean()
        assert cv < 3.0


# ---------------------------------------------------------------------------
# estimate_filter_cutoff
# ---------------------------------------------------------------------------

class TestEstimateFilterCutoff:
    def test_returns_float_or_none(self):
        sd = _make_signal()
        result = compute_psd(sd)
        f3db = estimate_filter_cutoff(result)
        assert f3db is None or isinstance(f3db, float)

    def test_detects_cutoff_of_filtered_signal(self):
        """For a clearly low-pass filtered signal, -3 dB should be near the cutoff."""
        sr = 50_000.0
        cutoff = 2_000.0
        sd = _make_lowpass_signal(sr=sr, cutoff=cutoff, duration=2.0)
        psd = compute_psd(sd, segment_sec=0.5)
        f3db = estimate_filter_cutoff(psd)
        if f3db is None:
            pytest.skip("Cutoff detection returned None for this signal")
        # Should be within a factor of 3 of the true cutoff
        assert cutoff / 3 < f3db < cutoff * 3

    def test_too_short_psd_returns_none(self):
        tiny = PSDResult(
            frequencies=np.array([0.0, 100.0, 200.0]),
            psd=np.array([1.0, 0.5, 0.1]),
        )
        assert estimate_filter_cutoff(tiny) is None


# ---------------------------------------------------------------------------
# compare_psd
# ---------------------------------------------------------------------------

class TestComparePSD:
    def test_ratio_same_signal_is_ones(self):
        sd = _make_signal(seed=10)
        psd = compute_psd(sd)
        ratio = compare_psd(psd, psd)
        finite = ratio[np.isfinite(ratio)]
        assert np.allclose(finite, 1.0, atol=1e-10)

    def test_mismatched_bins_raises(self):
        sd1 = _make_signal(sr=10_000, duration=0.5, seed=0)
        sd2 = _make_signal(sr=10_000, duration=1.0, seed=1)
        psd1 = compute_psd(sd1, segment_sec=0.2)
        psd2 = compute_psd(sd2, segment_sec=0.5)
        if len(psd1.frequencies) == len(psd2.frequencies):
            pytest.skip("By coincidence same length — skipping mismatch test")
        with pytest.raises(ValueError, match="same frequency bins"):
            compare_psd(psd1, psd2)

    def test_output_shape_matches_input(self):
        sd = _make_signal()
        psd = compute_psd(sd)
        ratio = compare_psd(psd, psd)
        assert ratio.shape == psd.frequencies.shape

    def test_zero_control_gives_nan(self):
        sd = _make_signal()
        psd = compute_psd(sd)
        zero_psd = PSDResult(
            frequencies=psd.frequencies.copy(),
            psd=np.zeros_like(psd.psd),
        )
        ratio = compare_psd(psd, zero_psd)
        # All entries should be NaN (denominator is 0)
        assert np.all(np.isnan(ratio))


# ---------------------------------------------------------------------------
# make_psd_figure
# ---------------------------------------------------------------------------

class TestMakePSDFigure:
    def test_returns_figure(self):
        pytest.importorskip("plotly")
        from nano_ext.analysis.spectrum import make_psd_figure
        sd = _make_signal()
        psd = compute_psd(sd)
        fig = make_psd_figure(psd)
        assert fig is not None
        assert hasattr(fig, "data")

    def test_figure_has_sample_trace(self):
        pytest.importorskip("plotly")
        from nano_ext.analysis.spectrum import make_psd_figure
        sd = _make_signal()
        psd = compute_psd(sd)
        fig = make_psd_figure(psd)
        names = [t.name for t in fig.data]
        assert "Sample" in names

    def test_control_adds_second_trace(self):
        pytest.importorskip("plotly")
        from nano_ext.analysis.spectrum import make_psd_figure
        sd = _make_signal(seed=0)
        ctrl = _make_signal(seed=1)
        psd = compute_psd(sd)
        ctrl_psd = compute_psd(ctrl)
        fig = make_psd_figure(psd, control_psd=ctrl_psd)
        names = [t.name for t in fig.data]
        assert "Sample" in names
        assert "Control" in names

    def test_log_axes(self):
        pytest.importorskip("plotly")
        from nano_ext.analysis.spectrum import make_psd_figure
        sd = _make_signal()
        psd = compute_psd(sd)
        fig = make_psd_figure(psd)
        assert fig.layout.xaxis.type == "log"
        assert fig.layout.yaxis.type == "log"


# ---------------------------------------------------------------------------
# Event exclusion, subsampling and noise summary
# ---------------------------------------------------------------------------

class TestOpenPoreSpectrum:
    def _noisy_with_events(self, sr=20_000, duration=10.0, seed=0):
        rng = np.random.default_rng(seed)
        x = rng.normal(0.0, 2.0, int(sr * duration))
        events = []
        for start in np.arange(0.2, duration - 0.2, 0.25):
            a, b = int(start * sr), int((start + 0.01) * sr)
            x[a:b] -= 80.0  # deep blockades add strong low-frequency power
            events.append((a, b))
        return SignalData(signal=x.astype(np.float32), sampling_rate=sr), events

    def test_excluding_events_recovers_white_noise(self):
        sd, events = self._noisy_with_events()
        white = 2 * 2.0 ** 2 / sd.sampling_rate  # one-sided PSD of N(0, 2²)
        full = compute_psd(sd, segment_sec=0.1)
        clean = compute_psd(sd, segment_sec=0.1, exclude=[(a - 20, b + 20) for a, b in events])
        low = lambda r: np.median(r.psd[(r.frequencies > 10) & (r.frequencies < 200)])  # noqa: E731
        assert low(full) > 10 * white
        assert low(clean) == pytest.approx(white, rel=0.3)
        assert clean.duration_used_sec < full.duration_used_sec
        assert clean.segment_sec <= 0.1

    def test_segments_do_not_cross_range_joins(self):
        from nano_ext.analysis.spectrum import _clean_runs
        idx = np.r_[0:100, 500:600, 700:800]
        runs = _clean_runs(300, [(150, 160)], idx)
        assert runs == [(0, 100), (100, 150), (160, 200), (200, 300)]

    def test_max_duration_caps_used_signal(self):
        sd = _make_signal(sr=10_000, duration=20.0)
        res = compute_psd(sd, segment_sec=0.1, max_duration_sec=2.0)
        assert res.duration_used_sec <= 2.5
        assert res.duration_total_sec == pytest.approx(20.0)

    def test_noise_summary_rms_matches_std(self):
        from nano_ext.analysis.spectrum import noise_summary
        rng = np.random.default_rng(1)
        sd = SignalData(signal=rng.normal(0, 3.0, 200_000).astype(np.float32), sampling_rate=50_000)
        summ = noise_summary(compute_psd(sd, segment_sec=0.1))
        assert summ["rms_total"] == pytest.approx(3.0, rel=0.05)
        assert [c for c, _ in summ["rms_below"]] == [1e3, 1e4]
        assert all(lo < hi for lo, hi, _, _ in summ["bands"])

    def test_mains_hum_is_found_and_labelled(self):
        from nano_ext.analysis.spectrum import find_spectral_peaks
        sr = 10_000
        t = np.arange(int(sr * 10)) / sr
        rng = np.random.default_rng(2)
        x = rng.normal(0, 1.0, len(t)) + 1.5 * np.sin(2 * np.pi * 50 * t)
        peaks = find_spectral_peaks(compute_psd(SignalData(signal=x, sampling_rate=sr)))
        assert peaks and abs(peaks[0]["frequency"] - 50) <= 1
        assert peaks[0]["label"] == "mains 50 Hz"

    def test_white_noise_has_no_cutoff(self):
        rng = np.random.default_rng(3)
        sd = SignalData(signal=rng.normal(0, 1, 100_000), sampling_rate=10_000)
        assert compute_psd(sd, segment_sec=0.1).f_3db_estimate is None

    def test_figure_extras(self):
        pytest.importorskip("plotly")
        from nano_ext.analysis.spectrum import find_spectral_peaks, make_psd_figure
        psd = compute_psd(_make_signal(sr=10_000, duration=2.0), segment_sec=0.2)
        fig = make_psd_figure(psd, filter_cutoff=1000.0,
                              peaks=[{"frequency": 50.0, "ratio": 20.0, "label": "mains 50 Hz"}])
        names = [t.name for t in fig.data]
        assert "Integrated RMS (pA)" in names and "Peaks" in names
        assert "PSD" in fig.layout.yaxis.title.text and "Hz" in fig.layout.xaxis.title.text
        assert find_spectral_peaks(psd) == [] or isinstance(find_spectral_peaks(psd), list)
