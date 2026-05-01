"""Tests for event detection: _find_runs, _merge_intervals, detect_events."""

import numpy as np
import pytest

from nano_ext.detection.events import _find_runs, _merge_intervals, detect_events
from nano_ext.models import EventDirection

SR = 10_000.0  # 10 kHz


class TestFindRuns:
    def test_empty_array(self):
        assert _find_runs(np.array([], dtype=bool)) == []

    def test_all_false(self):
        assert _find_runs(np.zeros(100, dtype=bool)) == []

    def test_all_true(self):
        assert _find_runs(np.ones(100, dtype=bool)) == [(0, 100)]

    def test_single_run_in_middle(self):
        mask = np.zeros(100, dtype=bool)
        mask[30:60] = True
        assert _find_runs(mask) == [(30, 60)]

    def test_run_at_start(self):
        mask = np.zeros(50, dtype=bool)
        mask[0:10] = True
        assert _find_runs(mask) == [(0, 10)]

    def test_run_at_end(self):
        mask = np.zeros(50, dtype=bool)
        mask[40:50] = True
        assert _find_runs(mask) == [(40, 50)]

    def test_multiple_runs(self):
        mask = np.zeros(100, dtype=bool)
        mask[10:20] = True
        mask[50:70] = True
        assert _find_runs(mask) == [(10, 20), (50, 70)]

    def test_single_sample_run(self):
        mask = np.zeros(10, dtype=bool)
        mask[5] = True
        assert _find_runs(mask) == [(5, 6)]


class TestMergeIntervals:
    def test_empty(self):
        assert _merge_intervals([], max_gap=10) == []

    def test_single_interval(self):
        assert _merge_intervals([(0, 10)], max_gap=5) == [(0, 10)]

    def test_no_merge_when_gap_large(self):
        intervals = [(0, 10), (50, 60)]
        assert _merge_intervals(intervals, max_gap=5) == [(0, 10), (50, 60)]

    def test_merge_when_gap_small(self):
        intervals = [(0, 10), (13, 20)]
        assert _merge_intervals(intervals, max_gap=5) == [(0, 20)]

    def test_merge_adjacent_zero_gap(self):
        intervals = [(0, 10), (10, 20)]
        assert _merge_intervals(intervals, max_gap=0) == [(0, 20)]

    def test_chain_merge(self):
        intervals = [(0, 10), (12, 20), (22, 30)]
        assert _merge_intervals(intervals, max_gap=3) == [(0, 30)]

    def test_partial_merge(self):
        intervals = [(0, 10), (12, 20), (30, 40)]
        # First two merge (gap=2 ≤ 3), third does not (gap=10 > 3)
        assert _merge_intervals(intervals, max_gap=3) == [(0, 20), (30, 40)]


class TestDetectEvents:
    def _flat_residual(self, n: int, noise_std: float = 0.5, seed: int = 0) -> np.ndarray:
        return np.random.default_rng(seed).normal(0.0, noise_std, n)

    def test_no_events_for_threshold_not_crossed(self):
        residual = self._flat_residual(10_000, noise_std=0.5)
        baseline = np.zeros(10_000)
        events = detect_events(
            residual=residual,
            sampling_rate=SR,
            threshold=-100.0,  # far below any noise sample
            baseline=baseline,
            direction=EventDirection.DOWN,
        )
        assert events == []

    def test_single_downward_event_detected(self):
        n = 10_000
        rng = np.random.default_rng(1)
        residual = rng.normal(0.0, 0.5, n)
        residual[3_000:5_000] += -50.0  # clear downward event
        baseline = np.zeros(n)
        events = detect_events(
            residual=residual,
            sampling_rate=SR,
            threshold=-10.0,
            baseline=baseline,
            direction=EventDirection.DOWN,
            min_event_duration_sec=0.05,
        )
        assert len(events) == 1
        assert abs(events[0].start_idx - 3_000) < 100
        assert abs(events[0].end_idx - 5_000) < 100

    def test_event_too_short_is_discarded(self):
        n = 5_000
        rng = np.random.default_rng(2)
        residual = rng.normal(0.0, 0.1, n)
        residual[1_000:1_010] = -50.0  # 10-sample event = 1 ms
        baseline = np.zeros(n)
        events = detect_events(
            residual=residual,
            sampling_rate=SR,
            threshold=-10.0,
            baseline=baseline,
            direction=EventDirection.DOWN,
            min_event_duration_sec=0.05,  # 500 samples min
        )
        assert events == []

    def test_upward_event_detected_with_up_direction(self):
        n = 10_000
        rng = np.random.default_rng(3)
        residual = rng.normal(0.0, 0.5, n)
        residual[2_000:4_000] += 50.0  # upward spike
        baseline = np.zeros(n)
        events = detect_events(
            residual=residual,
            sampling_rate=SR,
            threshold=-10.0,  # for UP direction, abs(threshold) is used
            baseline=baseline,
            direction=EventDirection.UP,
            min_event_duration_sec=0.05,
        )
        assert len(events) == 1

    def test_event_statistics(self):
        n = 10_000
        rng = np.random.default_rng(4)
        baseline_arr = 100.0 * np.ones(n)
        residual = rng.normal(0.0, 0.5, n)
        residual[2_000:4_000] += -50.0

        events = detect_events(
            residual=residual,
            sampling_rate=SR,
            threshold=-10.0,
            baseline=baseline_arr,
            direction=EventDirection.DOWN,
            min_event_duration_sec=0.05,
        )
        assert len(events) == 1
        ev = events[0]
        assert ev.duration > 0.0
        assert abs(ev.depth - 50.0) < 5.0
        assert abs(ev.baseline_current - 100.0) < 1.0
        assert ev.area > 0.0
        assert ev.relative_depth > 0.0

    def test_nearby_events_merged(self):
        n = 20_000
        rng = np.random.default_rng(5)
        residual = rng.normal(0.0, 0.5, n)
        # Two events separated by a tiny gap
        residual[3_000:4_000] += -50.0
        residual[4_010:5_000] += -50.0
        baseline = np.zeros(n)
        events = detect_events(
            residual=residual,
            sampling_rate=SR,
            threshold=-10.0,
            baseline=baseline,
            direction=EventDirection.DOWN,
            min_event_duration_sec=0.05,
            merge_gap_sec=0.005,  # 50 samples — spans the 10-sample gap
        )
        assert len(events) == 1

    def test_two_separate_events(self):
        n = 20_000
        rng = np.random.default_rng(6)
        residual = rng.normal(0.0, 0.5, n)
        residual[2_000:3_000] += -50.0
        residual[12_000:13_000] += -50.0
        baseline = np.zeros(n)
        events = detect_events(
            residual=residual,
            sampling_rate=SR,
            threshold=-10.0,
            baseline=baseline,
            direction=EventDirection.DOWN,
            min_event_duration_sec=0.05,
            merge_gap_sec=0.01,
        )
        assert len(events) == 2
