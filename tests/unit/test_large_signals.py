"""Tests for the memory / speed work on long recordings.

Covers the memory-mapped ABF and binary readers, implied time arrays,
the numpy-returning (float32 / float64) Rust baseline kernel, chunked
filtering, dtype-preserving baseline estimation, reproducible thresholds
and process-parallel sub-level analysis.
"""

from __future__ import annotations

import numpy as np
import pytest

from nano_ext.models import DetectionConfig, SignalData


# ---------------------------------------------------------------------------
# ABF reader
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def abf_file(tmp_path_factory):
    pyabf = pytest.importorskip("pyabf")
    from pyabf import abfWriter

    rng = np.random.default_rng(0)
    sweeps = (200.0 + rng.normal(0, 5, (3, 20_000))).astype(np.float32)
    sweeps[1, 5000:6000] -= 80.0
    path = tmp_path_factory.mktemp("abf") / "rec.abf"
    abfWriter.writeABF1(sweeps, str(path), 250_000)
    return path


def _pyabf_concat(path, sweeps=None):
    import pyabf
    abf = pyabf.ABF(str(path))
    idx = range(abf.sweepCount) if sweeps is None else sweeps
    out = []
    for i in idx:
        abf.setSweep(i)
        out.append(np.array(abf.sweepY))
    return np.concatenate(out)


class TestABFReader:
    def test_matches_pyabf_all_sweeps(self, abf_file):
        from nano_ext.io.abf_reader import read_abf
        sd = read_abf(abf_file)
        assert sd.signal.dtype == np.float32
        np.testing.assert_array_equal(sd.signal, _pyabf_concat(abf_file))
        assert sd.sampling_rate == 250_000
        assert sd.metadata["data_format"] == "int16"

    def test_matches_pyabf_single_sweep(self, abf_file):
        from nano_ext.io.abf_reader import read_abf
        sd = read_abf(abf_file, sweep=1)
        np.testing.assert_array_equal(sd.signal, _pyabf_concat(abf_file, [1]))

    def test_raw_channel_is_memory_mapped_int16(self, abf_file):
        import pyabf
        from nano_ext.io.abf_reader import abf_channel_scaling, abf_raw_channel
        abf = pyabf.ABF(str(abf_file), loadData=False)
        raw = abf_raw_channel(abf, 0)
        assert raw.dtype == np.int16
        assert isinstance(raw.base, np.memmap) or isinstance(raw, np.memmap)
        gain, offset = abf_channel_scaling(abf, 0)
        scaled = raw[:100].astype(np.float32) * np.float32(gain) + np.float32(offset)
        np.testing.assert_array_equal(scaled, _pyabf_concat(abf_file)[:100])

    def test_invalid_channel_and_sweep(self, abf_file):
        from nano_ext.io.abf_reader import read_abf
        with pytest.raises(ValueError):
            read_abf(abf_file, channel=3)
        with pytest.raises(ValueError):
            read_abf(abf_file, sweep=9)


# ---------------------------------------------------------------------------
# Binary reader
# ---------------------------------------------------------------------------

class TestBinaryReader:
    def test_int16_scaled_to_float32(self, tmp_path):
        from nano_ext.io.binary_reader import read_binary
        raw = np.array([0, 1, -2, 1000], dtype=np.int16)
        path = tmp_path / "x.bin"
        path.write_bytes(b"HDR!" + raw.tobytes())
        sd = read_binary(path, sampling_rate=1e5, dtype="int16", header_bytes=4,
                         scale_factor=0.5, offset=10.0)
        assert sd.signal.dtype == np.float32
        np.testing.assert_allclose(sd.signal, raw * 0.5 + 10.0)

    def test_max_samples_and_trailing_bytes(self, tmp_path):
        from nano_ext.io.binary_reader import read_binary
        path = tmp_path / "y.bin"
        path.write_bytes(np.arange(10, dtype=np.float32).tobytes() + b"\x00")
        sd = read_binary(path, sampling_rate=1e5, dtype="float32", max_samples=4)
        np.testing.assert_array_equal(sd.signal, [0, 1, 2, 3])

    def test_empty_file(self, tmp_path):
        from nano_ext.io.binary_reader import read_binary
        path = tmp_path / "z.bin"
        path.write_bytes(b"")
        assert len(read_binary(path, sampling_rate=1e5).signal) == 0


# ---------------------------------------------------------------------------
# Implied time arrays
# ---------------------------------------------------------------------------

class TestSignalTime:
    def test_implied_time_not_stored(self):
        sd = SignalData(signal=np.zeros(5, np.float32), sampling_rate=10.0)
        assert not sd.has_explicit_time
        np.testing.assert_allclose(sd.time, [0, 0.1, 0.2, 0.3, 0.4])
        np.testing.assert_allclose(sd.time_at([1, 3]), [0.1, 0.3])
        np.testing.assert_allclose(sd.time_at(slice(2, 4)), [0.2, 0.3])

    def test_explicit_time_kept(self):
        t = np.array([5.0, 6.0, 7.0])
        sd = SignalData(signal=np.zeros(3), sampling_rate=1.0, time=t)
        assert sd.has_explicit_time
        assert sd.time is t

    def test_time_offset_and_index_map(self):
        sd = SignalData(signal=np.zeros(3), sampling_rate=10.0, time_offset=2.0)
        np.testing.assert_allclose(sd.time, [2.0, 2.1, 2.2])
        sd = SignalData(signal=np.zeros(3), sampling_rate=10.0,
                        index_map=np.array([0, 1, 50]))
        np.testing.assert_allclose(sd.time, [0.0, 0.1, 5.0])
        np.testing.assert_allclose(sd.time_at([2]), [5.0])

    def test_crop_uses_offset(self):
        from nano_ext.preprocessing.segments import crop_signal_to_range
        sd = SignalData(signal=np.arange(100, dtype=np.float32), sampling_rate=10.0)
        cropped, off = crop_signal_to_range(sd, 2.0, 5.0)
        assert not cropped.has_explicit_time
        assert cropped.time[0] == pytest.approx(2.0)
        assert np.shares_memory(cropped.signal, sd.signal)


# ---------------------------------------------------------------------------
# Rust baseline kernel
# ---------------------------------------------------------------------------

def _reference_kernel(signal, mask, window, percentile):
    """Direct-path reference of the Rust kernel semantics (small inputs)."""
    n = len(signal)
    half = window // 2
    valid_all = signal[mask & ~np.isnan(signal)]
    fallback = np.sort(valid_all)[len(valid_all) // 2] if len(valid_all) else 0.0
    base = np.empty(n)
    for c in range(n):
        s, e = max(0, c - half), min(n, c + half)
        w = signal[s:e][mask[s:e] & ~np.isnan(signal[s:e])]
        if len(w) == 0:
            base[c] = fallback
        else:
            srt = np.sort(w)
            # Rust rounds half away from zero (Python round() is half-to-even)
            k = int(np.floor(percentile / 100 * (len(srt) - 1) + 0.5))
            base[c] = srt[min(k, len(srt) - 1)]
    sw = max(3, window // 10)
    sw += 1 - sw % 2
    if n >= sw:
        hs = sw // 2
        out = np.empty(n)
        for i in range(n):
            s, e = max(0, i - hs), min(n, i + hs + 1)
            out[i] = base[s:e].mean()
        base = out
    return base


class TestBaselineKernel:
    @pytest.mark.parametrize("percentile", [10.0, 50.0, 90.0])
    def test_matches_reference(self, percentile):
        from nano_ext import local_baseline_percentile
        rng = np.random.default_rng(0)
        x = rng.normal(100, 5, 600)
        m = rng.random(600) > 0.3
        m[:40] = False  # fully masked windows exercise the fallback
        out = local_baseline_percentile(x, m, 31, percentile)
        assert isinstance(out, np.ndarray) and out.dtype == np.float64
        np.testing.assert_allclose(out, _reference_kernel(x, m, 31, percentile), atol=1e-9)

    def test_float32_variant(self):
        from nano_ext import local_baseline_percentile
        from nano_ext._nano_ext import local_baseline_percentile_f32
        rng = np.random.default_rng(1)
        x = rng.normal(100, 5, 50_000)
        m = rng.random(50_000) > 0.1
        a = local_baseline_percentile(x, m, 4001, 90.0)
        b = local_baseline_percentile_f32(x.astype(np.float32), m, 4001, 90.0)
        assert b.dtype == np.float32
        np.testing.assert_allclose(b, a, atol=1e-4)

    def test_nan_samples_ignored(self):
        from nano_ext import local_baseline_percentile
        x = np.full(200, 7.0)
        x[::3] = np.nan
        out = local_baseline_percentile(x, np.ones(200, bool), 21, 50.0)
        np.testing.assert_allclose(out, 7.0)


# ---------------------------------------------------------------------------
# Filtering and baseline
# ---------------------------------------------------------------------------

class TestChunkedFilter:
    def test_chunked_matches_single_pass(self):
        from nano_ext.preprocessing.filters import lowpass_filter
        rng = np.random.default_rng(0)
        x = 100 + rng.normal(0, 5, 300_000)
        x[100_000:101_000] -= 50
        full = lowpass_filter(x, 250_000, 25_000, chunk_samples=10**9)
        for chunk in (100_000, 12_345):
            chunked = lowpass_filter(x, 250_000, 25_000, chunk_samples=chunk)
            np.testing.assert_allclose(chunked, full, atol=1e-9)

    def test_dtype_preserved(self):
        from nano_ext.preprocessing.filters import lowpass_filter
        x = np.random.default_rng(1).normal(0, 1, 5000)
        assert lowpass_filter(x.astype(np.float32), 1e5, 1e4).dtype == np.float32
        assert lowpass_filter(x, 1e5, 1e4).dtype == np.float64


class TestBaselineDtype:
    @pytest.mark.parametrize("detrend", ["none", "polynomial"])
    def test_float32_matches_float64(self, detrend):
        from nano_ext.preprocessing.baseline import estimate_baseline
        rng = np.random.default_rng(0)
        x = 200 + rng.normal(0, 3, 200_000) + np.linspace(0, 5, 200_000)
        x[50_000:52_000] -= 80
        r64 = estimate_baseline(x, 100_000, window_sec=0.2, detrend_method=detrend)
        r32 = estimate_baseline(x.astype(np.float32), 100_000, window_sec=0.2, detrend_method=detrend)
        assert r32.residual.dtype == np.float32
        assert r32.local_baseline.dtype == np.float32
        np.testing.assert_allclose(r32.residual, r64.residual, atol=1e-3)
        assert r32.noise_std == pytest.approx(r64.noise_std, rel=1e-5)

    def test_no_detrend_trend_is_zero_view(self):
        from nano_ext.preprocessing.baseline import estimate_baseline
        x = np.random.default_rng(0).normal(0, 1, 20_000).astype(np.float32)
        res = estimate_baseline(x, 10_000, window_sec=0.1)
        assert len(res.trend) == len(x)
        assert not np.any(res.trend)

    def test_estimate_noise_matches_numpy_median(self):
        from nano_ext.preprocessing.baseline import _estimate_noise
        rng = np.random.default_rng(0)
        for n in (1001, 1000):
            x = rng.normal(0, 2, n)
            expect = np.median(np.abs(x - np.median(x))) * 1.4826
            before = x.copy()
            assert _estimate_noise(x) == pytest.approx(expect, rel=1e-12)
            np.testing.assert_array_equal(x, before)  # not modified by default

    def test_within_matches_abs(self):
        from nano_ext.preprocessing.baseline import _within
        x = np.array([-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, np.nan], dtype=np.float32)
        np.testing.assert_array_equal(_within(x, 1.0), np.abs(x) < 1.0)


# ---------------------------------------------------------------------------
# Pipeline: reproducibility and parallel sub-levels
# ---------------------------------------------------------------------------

def _synthetic(seed=0):
    from nano_ext.testing.synthetic import SyntheticEventSpec, generate_synthetic_signal
    events = [
        SyntheticEventSpec(start_time=0.02 + i * 0.03,
                           levels=[(0.004, 120.0), (0.004, 60.0)] if i % 2 else [(0.006, 110.0)])
        for i in range(10)
    ]
    return generate_synthetic_signal(
        duration_sec=0.35, sampling_rate=50_000, events=events, seed=seed,
    ).signal_data


def _event_keys(result):
    return [
        (e.start_idx, e.end_idx, e.n_levels, [(s.start_idx, s.end_idx, s.start_time) for s in e.sublevels])
        for e in result.events
    ]


class TestPipelineReproducibility:
    def test_repeated_runs_identical(self):
        from nano_ext.pipeline import run_pipeline
        sd = _synthetic()
        cfg = DetectionConfig(baseline_window_sec=0.05, min_event_duration_sec=0.001,
                              gmm_max_components=4)
        a = run_pipeline(sd, cfg)
        b = run_pipeline(sd, cfg)
        assert a.threshold_result.threshold == b.threshold_result.threshold
        assert _event_keys(a) == _event_keys(b)

    def test_parallel_sublevels_identical(self):
        from nano_ext.pipeline import run_pipeline
        sd = _synthetic(seed=1)
        base = dict(baseline_window_sec=0.05, min_event_duration_sec=0.001, gmm_max_components=4)
        seq = run_pipeline(sd, DetectionConfig(**base, n_jobs=1))
        par = run_pipeline(sd, DetectionConfig(**base, n_jobs=2))
        assert seq.n_events == 10
        assert _event_keys(seq) == _event_keys(par)
