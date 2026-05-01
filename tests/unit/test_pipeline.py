"""End-to-end integration tests for run_pipeline."""

import numpy as np
import pytest

from nano_ext.models import DetectionConfig, EventDirection, SignalData
from nano_ext.pipeline import PipelineResult, run_pipeline

slow = pytest.mark.slow

SR = 10_000.0  # 10 kHz


def _synthetic_signal(
    duration_sec: float = 2.0,
    baseline: float = 100.0,
    event_amplitude: float = -60.0,
    event_intervals_sec: list | None = None,
    noise_std: float = 1.0,
    seed: int = 42,
) -> SignalData:
    rng = np.random.default_rng(seed)
    n = int(duration_sec * SR)
    sig = baseline * np.ones(n) + rng.normal(0.0, noise_std, n)
    if event_intervals_sec:
        for start_s, end_s in event_intervals_sec:
            s, e = int(start_s * SR), int(end_s * SR)
            sig[s:e] = baseline + event_amplitude + rng.normal(0.0, noise_std, e - s)
    return SignalData(signal=sig, sampling_rate=SR)


def _fast_config(**kwargs) -> DetectionConfig:
    defaults = dict(
        filter_cutoff=2_000.0,
        gmm_max_components=3,
        event_direction=EventDirection.DOWN,
        min_event_duration_sec=0.02,
        baseline_window_sec=0.5,
    )
    defaults.update(kwargs)
    return DetectionConfig(**defaults)


class TestRunPipeline:
    @slow
    def test_result_type(self):
        sd = _synthetic_signal(duration_sec=0.5, event_intervals_sec=[])
        result = run_pipeline(sd, config=_fast_config(), analyze_sublevel=False)
        assert isinstance(result, PipelineResult)

    @slow
    def test_result_arrays_same_length_as_signal(self):
        sd = _synthetic_signal(duration_sec=0.5, event_intervals_sec=[])
        result = run_pipeline(sd, config=_fast_config(), analyze_sublevel=False)
        n = sd.n_samples
        assert len(result.filtered_signal) == n
        assert len(result.baseline_result.local_baseline) == n
        assert len(result.baseline_result.residual) == n

    @slow
    def test_no_events_pure_noise(self):
        """Pure Gaussian noise should produce zero detected events."""
        sd = _synthetic_signal(duration_sec=1.0, event_intervals_sec=[])
        result = run_pipeline(sd, config=_fast_config(), analyze_sublevel=False)
        assert result.n_events == 0

    @slow
    def test_three_events_all_detected(self):
        """Three clear, well-separated events should all be detected."""
        events_sec = [(0.3, 0.4), (0.8, 0.9), (1.4, 1.5)]
        sd = _synthetic_signal(
            duration_sec=2.0,
            event_amplitude=-60.0,
            event_intervals_sec=events_sec,
            noise_std=1.0,
        )
        result = run_pipeline(sd, config=_fast_config(), analyze_sublevel=False)
        assert result.n_events == 3

    @slow
    def test_event_count_increases_with_more_events(self):
        """More injected events → more detected events."""
        sd_few = _synthetic_signal(
            duration_sec=3.0,
            event_intervals_sec=[(0.5, 0.6), (1.5, 1.6)],
        )
        sd_many = _synthetic_signal(
            duration_sec=3.0,
            event_intervals_sec=[
                (0.3, 0.4), (0.7, 0.8), (1.2, 1.3),
                (1.7, 1.8), (2.2, 2.3),
            ],
        )
        r_few = run_pipeline(sd_few, config=_fast_config(), analyze_sublevel=False)
        r_many = run_pipeline(sd_many, config=_fast_config(), analyze_sublevel=False)
        assert r_many.n_events > r_few.n_events

    @slow
    def test_summary_string_contains_key_fields(self):
        sd = _synthetic_signal(duration_sec=0.5, event_intervals_sec=[])
        result = run_pipeline(sd, config=_fast_config(), analyze_sublevel=False)
        summary = result.summary()
        assert "Events detected" in summary
        assert "Threshold" in summary
        assert "Noise std" in summary

    @slow
    def test_n_multilevel_without_sublevel_analysis(self):
        """With analyze_sublevel=False, no events should be multi-level."""
        events_sec = [(0.3, 0.5), (0.9, 1.1)]
        sd = _synthetic_signal(duration_sec=2.0, event_intervals_sec=events_sec)
        result = run_pipeline(sd, config=_fast_config(), analyze_sublevel=False)
        assert result.n_multilevel == 0

    @slow
    def test_filter_skipped_when_apply_filter_false(self):
        """With apply_filter=False, filtered_signal should equal raw signal."""
        sd = _synthetic_signal(duration_sec=0.5, event_intervals_sec=[])
        config = _fast_config(apply_filter=False, pre_applied_filter_cutoff=2_000.0)
        result = run_pipeline(sd, config=config, analyze_sublevel=False)
        np.testing.assert_array_equal(result.filtered_signal, sd.signal)
