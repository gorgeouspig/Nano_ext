"""Tests for HMM-based event analysis (Phase 6.2)."""

from __future__ import annotations

import numpy as np
import pytest

from nano_ext.testing.synthetic import generate_synthetic_signal, SyntheticEventSpec
from nano_ext.models import DetectionConfig, HMMResult
from nano_ext.pipeline import run_pipeline


hmmlearn = pytest.importorskip("hmmlearn", reason="hmmlearn not installed — skipping HMM tests")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _two_state_signal(n: int = 500) -> np.ndarray:
    """Two-state signal: baseline=0 for first half, blockade=-50 for second half."""
    rng = np.random.default_rng(7)
    sig = np.concatenate([
        rng.normal(0.0, 2.0, n // 2),
        rng.normal(-50.0, 2.0, n - n // 2),
    ])
    return sig.astype(np.float64)


def _pipeline_result_with_hmm():
    events = [
        SyntheticEventSpec(start_time=0.01 + i * 0.02, levels=[(0.003, 150.0)])
        for i in range(3)
    ]
    syn = generate_synthetic_signal(
        duration_sec=0.1, sampling_rate=50_000, events=events, seed=0,
    )
    config = DetectionConfig(
        apply_filter=False,
        baseline_window_sec=0.03,
        min_event_duration_sec=0.0005,
        gmm_max_components=3,
        hmm_analysis=True,
        hmm_max_states=3,
    )
    return run_pipeline(syn.signal_data, config=config, analyze_sublevel=False, verbose=False)


def _pipeline_result_no_hmm():
    events = [
        SyntheticEventSpec(start_time=0.01 + i * 0.02, levels=[(0.003, 150.0)])
        for i in range(2)
    ]
    syn = generate_synthetic_signal(
        duration_sec=0.08, sampling_rate=20_000, events=events, seed=1,
    )
    config = DetectionConfig(
        apply_filter=False,
        baseline_window_sec=0.03,
        min_event_duration_sec=0.001,
        gmm_max_components=3,
    )
    return run_pipeline(syn.signal_data, config=config, analyze_sublevel=False, verbose=False)


# ---------------------------------------------------------------------------
# fit_hmm_event
# ---------------------------------------------------------------------------

class TestFitHMMEvent:
    def test_returns_hmm_result(self):
        from nano_ext.detection.hmm import fit_hmm_event
        sig = _two_state_signal()
        result = fit_hmm_event(sig, sampling_rate=10_000.0)
        assert result is not None
        assert isinstance(result, HMMResult)

    def test_n_states_at_least_one(self):
        from nano_ext.detection.hmm import fit_hmm_event
        sig = _two_state_signal()
        result = fit_hmm_event(sig, sampling_rate=10_000.0)
        assert result.n_states >= 1

    def test_n_states_within_max(self):
        from nano_ext.detection.hmm import fit_hmm_event
        sig = _two_state_signal()
        result = fit_hmm_event(sig, sampling_rate=10_000.0, max_states=3)
        assert result.n_states <= 3

    def test_two_state_signal_prefers_two_states(self):
        """A clearly two-level signal should yield 2 states (BIC preference)."""
        from nano_ext.detection.hmm import fit_hmm_event
        rng = np.random.default_rng(0)
        n = 1000
        sig = np.concatenate([
            rng.normal(0.0, 0.5, n // 2),
            rng.normal(-100.0, 0.5, n // 2),
        ])
        result = fit_hmm_event(sig.astype(np.float64), sampling_rate=10_000.0)
        assert result is not None
        assert result.n_states >= 2

    def test_state_means_shape(self):
        from nano_ext.detection.hmm import fit_hmm_event
        sig = _two_state_signal()
        result = fit_hmm_event(sig, sampling_rate=10_000.0)
        assert result.state_means.shape == (result.n_states,)

    def test_state_stds_positive(self):
        from nano_ext.detection.hmm import fit_hmm_event
        sig = _two_state_signal()
        result = fit_hmm_event(sig, sampling_rate=10_000.0)
        assert np.all(result.state_stds > 0)

    def test_transition_matrix_row_stochastic(self):
        from nano_ext.detection.hmm import fit_hmm_event
        sig = _two_state_signal()
        result = fit_hmm_event(sig, sampling_rate=10_000.0)
        row_sums = result.transition_matrix.sum(axis=1)
        assert np.allclose(row_sums, 1.0, atol=1e-6)

    def test_state_sequence_length(self):
        from nano_ext.detection.hmm import fit_hmm_event
        sig = _two_state_signal(n=400)
        result = fit_hmm_event(sig, sampling_rate=10_000.0)
        assert result is not None
        assert len(result.state_sequence) == len(sig)

    def test_dwell_times_list_length(self):
        from nano_ext.detection.hmm import fit_hmm_event
        sig = _two_state_signal()
        result = fit_hmm_event(sig, sampling_rate=10_000.0)
        assert len(result.dwell_times_s) == result.n_states

    def test_too_short_returns_none(self):
        from nano_ext.detection.hmm import fit_hmm_event
        sig = np.array([1.0, 2.0, 3.0])  # fewer than 10 samples
        result = fit_hmm_event(sig, sampling_rate=10_000.0)
        assert result is None

    def test_bic_is_finite(self):
        from nano_ext.detection.hmm import fit_hmm_event
        sig = _two_state_signal()
        result = fit_hmm_event(sig, sampling_rate=10_000.0)
        assert np.isfinite(result.bic)


# ---------------------------------------------------------------------------
# analyze_events_hmm
# ---------------------------------------------------------------------------

class TestAnalyzeEventsHMM:
    def test_sets_hmm_result_attribute(self):
        from nano_ext.detection.hmm import analyze_events_hmm
        result = _pipeline_result_no_hmm()
        if not result.events:
            pytest.skip("No events detected")
        events = analyze_events_hmm(
            result.events, result.filtered_signal,
            result.signal_data.sampling_rate, max_states=3,
        )
        for ev in events:
            assert hasattr(ev, "hmm_result")
            assert ev.hmm_result is None or isinstance(ev.hmm_result, HMMResult)

    def test_returns_same_length_list(self):
        from nano_ext.detection.hmm import analyze_events_hmm
        result = _pipeline_result_no_hmm()
        n = len(result.events)
        events = analyze_events_hmm(
            result.events, result.filtered_signal,
            result.signal_data.sampling_rate,
        )
        assert len(events) == n


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------

class TestHMMPipelineIntegration:
    def test_hmm_flag_runs_without_error(self):
        result = _pipeline_result_with_hmm()
        assert result is not None

    def test_events_have_hmm_result_field(self):
        result = _pipeline_result_with_hmm()
        for ev in result.events:
            assert hasattr(ev, "hmm_result")

    def test_hmm_result_type_or_none(self):
        result = _pipeline_result_with_hmm()
        for ev in result.events:
            assert ev.hmm_result is None or isinstance(ev.hmm_result, HMMResult)

    def test_no_hmm_flag_leaves_hmm_result_none(self):
        result = _pipeline_result_no_hmm()
        for ev in result.events:
            assert ev.hmm_result is None
