"""Tests for GMM+BIC-based sub-level analysis."""

import numpy as np
import pytest

from nano_ext.models import Event, EventType, DetectionConfig
from nano_ext.detection.sublevel import analyze_sublevels, _fit_gmm_bic

slow = pytest.mark.slow


def _make_event(start_idx: int, end_idx: int, sampling_rate: float, baseline_current: float = 100.0) -> Event:
    """Helper: construct a minimal Event object."""
    return Event(
        start_idx=start_idx,
        end_idx=end_idx,
        start_time=start_idx / sampling_rate,
        end_time=end_idx / sampling_rate,
        duration=(end_idx - start_idx) / sampling_rate,
        mean_current=50.0,
        std_current=1.0,
        baseline_current=baseline_current,
        depth=50.0,
        relative_depth=0.5,
        area=5.0,
    )


SAMPLING_RATE = 10000.0
FILTER_CUTOFF = 1000.0  # Hz


def _flat_signal(level: float, n: int, noise_std: float = 1.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return level * np.ones(n) + rng.normal(0, noise_std, n)


class TestFitGmmBic:
    @slow
    def test_single_level_selects_k1(self):
        signal = _flat_signal(50.0, 500, noise_std=1.0)
        k, gmm = _fit_gmm_bic(signal, max_components=5)
        assert k == 1

    @slow
    def test_two_levels_selects_k2(self):
        rng = np.random.default_rng(42)
        signal = np.concatenate([
            50.0 * np.ones(300) + rng.normal(0, 1.0, 300),
            20.0 * np.ones(300) + rng.normal(0, 1.0, 300),
        ])
        k, gmm = _fit_gmm_bic(signal, max_components=5)
        assert k == 2


class TestAnalyzeSublevels:
    @slow
    def test_single_level_event_unchanged(self):
        """A flat event must remain single-level after analysis."""
        n_total = 5000
        sr = SAMPLING_RATE
        baseline_arr = 100.0 * np.ones(n_total)

        # Event from sample 1000 to 3000 (200 ms), constant 50 pA
        signal = baseline_arr.copy()
        rng = np.random.default_rng(1)
        signal[1000:3000] = 50.0
        signal += rng.normal(0, 1.0, n_total)

        event = _make_event(1000, 3000, sr)
        updated = analyze_sublevels(
            event=event,
            signal=signal,
            baseline=baseline_arr,
            sampling_rate=sr,
            filter_cutoff=FILTER_CUTOFF,
            max_levels=5,
            min_segment_samples=50,
        )

        assert updated.n_levels == 1
        assert updated.event_type == EventType.SINGLE
        assert updated.sublevels == []

    @slow
    def test_two_level_event_detected(self):
        """An event with a clear step transition must yield n_levels == 2."""
        n_total = 10000
        sr = SAMPLING_RATE
        baseline_arr = 100.0 * np.ones(n_total)

        # Event: 1000-5000 (400 ms), first half 50 pA, second half 20 pA
        signal = baseline_arr.copy()
        rng = np.random.default_rng(2)
        signal[1000:3000] = 50.0
        signal[3000:5000] = 20.0
        signal += rng.normal(0, 1.0, n_total)

        event = _make_event(1000, 5000, sr)
        updated = analyze_sublevels(
            event=event,
            signal=signal,
            baseline=baseline_arr,
            sampling_rate=sr,
            filter_cutoff=FILTER_CUTOFF,
            max_levels=5,
            min_segment_samples=50,
        )

        assert updated.n_levels == 2
        assert updated.event_type == EventType.MULTI_LEVEL
        assert len(updated.sublevels) == 2

        # Sub-levels should be in chronological order
        assert updated.sublevels[0].start_time < updated.sublevels[1].start_time

        # Mean currents should be distinct (~50 pA and ~20 pA)
        means = sorted(sl.mean_current for sl in updated.sublevels)
        assert means[0] < 35.0  # closer to 20 pA
        assert means[1] > 40.0  # closer to 50 pA

    @slow
    def test_event_too_short_after_trim_returns_single(self):
        """When edge trimming leaves too few samples, the event is kept single."""
        n_total = 2000
        sr = SAMPLING_RATE
        baseline_arr = 100.0 * np.ones(n_total)
        signal = baseline_arr.copy()
        signal[500:600] = 50.0  # 10 ms event, very short relative to filter

        event = _make_event(500, 600, sr)
        updated = analyze_sublevels(
            event=event,
            signal=signal,
            baseline=baseline_arr,
            sampling_rate=sr,
            filter_cutoff=FILTER_CUTOFF,  # edge trim = 20 samples each side
            max_levels=5,
            min_segment_samples=50,
        )
        # After trimming 20+20 = 40 samples from 100 samples, only 60 remain.
        # 60 < 2 * 50 → returns unchanged
        assert updated.n_levels == 1

    @slow
    def test_no_filter_cutoff_no_trimming(self):
        """Without filter_cutoff, no edge trimming is applied."""
        n_total = 5000
        sr = SAMPLING_RATE
        baseline_arr = 100.0 * np.ones(n_total)
        signal = baseline_arr.copy()
        rng = np.random.default_rng(3)
        signal[1000:2500] = 50.0
        signal[2500:4000] = 20.0
        signal += rng.normal(0, 1.0, n_total)

        event = _make_event(1000, 4000, sr)
        updated = analyze_sublevels(
            event=event,
            signal=signal,
            baseline=baseline_arr,
            sampling_rate=sr,
            filter_cutoff=None,
            max_levels=5,
            min_segment_samples=50,
        )
        assert updated.n_levels == 2
        assert updated.event_type == EventType.MULTI_LEVEL
