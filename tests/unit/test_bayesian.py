"""Tests for the Bayesian / nonparametric methods.

Covers the Dirichlet-process GMM helper, DPGMM thresholding, BOCPD,
DPGMM / BOCPD sub-level analysis, the sticky HDP-HMM, event clustering and
Bayesian event statistics.
"""

from __future__ import annotations

import numpy as np
import pytest

from nano_ext.models import DetectionConfig, Event, EventType, HMMResult


def _make_event(start_idx, end_idx, sampling_rate, **kw) -> Event:
    defaults = dict(
        mean_current=50.0, std_current=1.0, baseline_current=100.0,
        depth=50.0, relative_depth=0.5, area=5.0,
    )
    defaults.update(kw)
    return Event(
        start_idx=start_idx,
        end_idx=end_idx,
        start_time=start_idx / sampling_rate,
        end_time=end_idx / sampling_rate,
        duration=(end_idx - start_idx) / sampling_rate,
        **defaults,
    )


# ---------------------------------------------------------------------------
# HMM BIC regression (score() returns the total log-likelihood)
# ---------------------------------------------------------------------------

class TestHMMBICRegression:
    def test_white_noise_selects_one_state(self):
        pytest.importorskip("hmmlearn")
        from nano_ext.detection.hmm import fit_hmm_event
        for seed in range(3):
            x = np.random.default_rng(seed).normal(0.0, 1.0, 2000)
            assert fit_hmm_event(x, sampling_rate=1e4).n_states == 1

    def test_log_likelihood_is_total(self):
        pytest.importorskip("hmmlearn")
        from nano_ext.detection.hmm import fit_hmm_event
        x = np.random.default_rng(0).normal(0.0, 1.0, 1000)
        res = fit_hmm_event(x, sampling_rate=1e4, max_states=1)
        # Total Gaussian log-likelihood of 1000 unit-variance samples ~ -1419
        assert -1600 < res.log_likelihood < -1250


# ---------------------------------------------------------------------------
# DPGMM helper
# ---------------------------------------------------------------------------

class TestFitDPGMM1D:
    def test_single_gaussian_one_component(self):
        from nano_ext.detection.bayes_mixture import fit_dpgmm_1d
        x = np.random.default_rng(0).normal(0.0, 1.0, 3000)
        fit = fit_dpgmm_1d(x, max_components=8)
        assert fit.n_components == 1
        assert abs(fit.means[0]) < 0.1

    def test_two_separated_gaussians(self):
        from nano_ext.detection.bayes_mixture import fit_dpgmm_1d
        rng = np.random.default_rng(1)
        x = np.concatenate([rng.normal(0, 1, 2000), rng.normal(-20, 1, 500)])
        fit = fit_dpgmm_1d(x, max_components=8)
        assert fit.n_components == 2
        assert np.allclose(np.sort(fit.means), [-20, 0], atol=0.3)
        assert np.isclose(fit.weights.sum(), 1.0)

    def test_predict_labels_match_components(self):
        from nano_ext.detection.bayes_mixture import fit_dpgmm_1d
        rng = np.random.default_rng(2)
        x = np.concatenate([rng.normal(0, 1, 1000), rng.normal(-20, 1, 1000)])
        fit = fit_dpgmm_1d(x, max_components=6)
        labels = fit.predict(np.array([0.0, -20.0]))
        assert fit.means[labels[0]] == pytest.approx(0.0, abs=0.3)
        assert fit.means[labels[1]] == pytest.approx(-20.0, abs=0.3)

    def test_means_sorted_ascending(self):
        from nano_ext.detection.bayes_mixture import fit_dpgmm_1d
        rng = np.random.default_rng(3)
        x = np.concatenate([rng.normal(5, 1, 800), rng.normal(-15, 1, 800)])
        fit = fit_dpgmm_1d(x)
        assert np.all(np.diff(fit.means) > 0)

    def test_unimodal_check(self):
        from nano_ext.detection.bayes_mixture import _is_unimodal
        assert _is_unimodal(0.5, 0.0, 1.0, 0.5, 1.0, 1.0)      # 1 sigma apart
        assert not _is_unimodal(0.5, 0.0, 1.0, 0.5, 6.0, 1.0)  # 6 sigma apart

    def test_thinning_step(self):
        from nano_ext.detection.bayes_mixture import thinning_step
        assert thinning_step(100_000, 10_000) == 5
        assert thinning_step(100_000, None) == 1
        assert thinning_step(100_000, 200_000) == 1


# ---------------------------------------------------------------------------
# DPGMM threshold
# ---------------------------------------------------------------------------

class TestDPGMMThreshold:
    def test_threshold_between_baseline_and_events(self):
        from nano_ext.detection.threshold import determine_threshold
        rng = np.random.default_rng(0)
        residual = np.concatenate([rng.normal(0, 1, 20_000), rng.normal(-30, 1.5, 600)])
        res = determine_threshold(residual, max_components=8, method="dpgmm", seed=0)
        assert res.n_components == 2
        assert -30 < res.threshold < -3
        assert res.bic_scores == []
        assert abs(res.component_means[res.baseline_component_idx]) < 0.5

    def test_unknown_method_raises(self):
        from nano_ext.detection.threshold import determine_threshold
        with pytest.raises(ValueError):
            determine_threshold(np.zeros(100), method="nope")


# ---------------------------------------------------------------------------
# BOCPD
# ---------------------------------------------------------------------------

class TestBOCPD:
    def test_detects_single_step(self):
        from nano_ext.detection.bocpd import bocpd
        rng = np.random.default_rng(0)
        x = np.concatenate([rng.normal(0, 1, 300), rng.normal(6, 1, 300)])
        res = bocpd(x, hazard=1 / 300)
        assert len(res.changepoints) == 1
        assert abs(res.changepoints[0] - 300) <= 3

    def test_detects_multiple_steps(self):
        from nano_ext.detection.bocpd import bocpd
        rng = np.random.default_rng(1)
        x = np.concatenate([
            rng.normal(0, 1, 200), rng.normal(-8, 1, 200), rng.normal(-3, 1, 200),
        ])
        res = bocpd(x, hazard=1 / 200)
        assert len(res.changepoints) == 2
        assert abs(res.changepoints[0] - 200) <= 3
        assert abs(res.changepoints[1] - 400) <= 3

    def test_constant_signal_no_changepoint(self):
        from nano_ext.detection.bocpd import bocpd
        x = np.random.default_rng(2).normal(0, 1, 1000)
        res = bocpd(x, hazard=1 / 500)
        assert res.changepoints == []

    def test_merge_short_segment(self):
        from nano_ext.detection.bocpd import _merge_short
        x = np.concatenate([np.zeros(100), np.full(2, 5.0), np.full(100, 6.0)])
        assert _merge_short(x, [100, 102], 5) == [100]

    def test_map_run_length_shape(self):
        from nano_ext.detection.bocpd import bocpd
        x = np.random.default_rng(3).normal(0, 1, 50)
        res = bocpd(x, hazard=0.01)
        assert res.map_run_length.shape == (50,)

    def test_invalid_hazard(self):
        from nano_ext.detection.bocpd import bocpd
        with pytest.raises(ValueError):
            bocpd(np.zeros(10), hazard=0.0)

    def test_robust_noise_std(self):
        from nano_ext.detection.bocpd import robust_noise_std
        rng = np.random.default_rng(4)
        x = np.concatenate([rng.normal(0, 2, 2000), rng.normal(50, 2, 2000)])
        assert robust_noise_std(x) == pytest.approx(2.0, rel=0.1)


# ---------------------------------------------------------------------------
# Sub-level methods
# ---------------------------------------------------------------------------

SR = 10_000.0
FC = 1_000.0


def _two_level_event(seed=0):
    rng = np.random.default_rng(seed)
    n = 6000
    baseline = 100.0 * np.ones(n)
    signal = baseline.copy()
    signal[1000:3000] = 50.0
    signal[3000:5000] = 20.0
    signal += rng.normal(0, 1.0, n)
    return signal, baseline, _make_event(1000, 5000, SR)


def _flat_event(seed=0):
    rng = np.random.default_rng(seed)
    n = 5000
    baseline = 100.0 * np.ones(n)
    signal = baseline.copy()
    signal[1000:3000] = 50.0
    signal += rng.normal(0, 1.0, n)
    return signal, baseline, _make_event(1000, 3000, SR)


@pytest.mark.parametrize("method", ["dpgmm", "bocpd"])
class TestSublevelMethods:
    def test_two_levels_found(self, method):
        from nano_ext.detection.sublevel import analyze_sublevels
        signal, baseline, ev = _two_level_event()
        out = analyze_sublevels(ev, signal, baseline, SR, filter_cutoff=FC, method=method)
        assert out.event_type == EventType.MULTI_LEVEL
        assert out.n_levels == 2
        means = sorted(sl.mean_current for sl in out.sublevels)
        assert means == pytest.approx([20.0, 50.0], abs=1.0)
        boundary = out.sublevels[0].end_idx
        assert abs(boundary - 3000) <= 20

    def test_flat_event_single_level(self, method):
        from nano_ext.detection.sublevel import analyze_sublevels
        signal, baseline, ev = _flat_event()
        out = analyze_sublevels(ev, signal, baseline, SR, filter_cutoff=FC, method=method)
        assert out.event_type == EventType.SINGLE
        assert out.n_levels == 1

    def test_level_index_orders_by_current(self, method):
        from nano_ext.detection.sublevel import analyze_sublevels
        signal, baseline, ev = _two_level_event(seed=5)
        out = analyze_sublevels(ev, signal, baseline, SR, filter_cutoff=FC, method=method)
        by_level = {sl.level_index: sl.mean_current for sl in out.sublevels}
        assert by_level[0] < by_level[1]


def test_sublevel_unknown_method():
    from nano_ext.detection.sublevel import analyze_sublevels
    signal, baseline, ev = _flat_event()
    with pytest.raises(ValueError):
        analyze_sublevels(ev, signal, baseline, SR, method="nope")


def test_bocpd_groups_revisited_level():
    """A level visited twice (A-B-A) keeps one level index."""
    from nano_ext.detection.sublevel import analyze_sublevels
    rng = np.random.default_rng(9)
    n = 8000
    baseline = 100.0 * np.ones(n)
    signal = baseline.copy()
    signal[1000:3000] = 50.0
    signal[3000:5000] = 20.0
    signal[5000:7000] = 50.0
    signal += rng.normal(0, 1.0, n)
    ev = _make_event(1000, 7000, SR)
    out = analyze_sublevels(ev, signal, baseline, SR, filter_cutoff=FC, method="bocpd")
    assert out.n_levels == 3
    assert [sl.level_index for sl in out.sublevels] == [1, 0, 1]


# ---------------------------------------------------------------------------
# Sticky HDP-HMM
# ---------------------------------------------------------------------------

def _switching_signal(seed=0, n_segments=8, seg_len=250, levels=(0.0, -10.0)):
    rng = np.random.default_rng(seed)
    parts = [np.full(seg_len, levels[i % len(levels)]) for i in range(n_segments)]
    return np.concatenate(parts) + rng.normal(0, 1.0, n_segments * seg_len)


class TestStickyHDPHMM:
    def test_two_state_switching(self):
        from nano_ext.detection.hdphmm import fit_sticky_hdp_hmm
        x = _switching_signal()
        res = fit_sticky_hdp_hmm(x, sampling_rate=SR, n_iter=150, burn_in=75)
        assert isinstance(res, HMMResult)
        assert res.method == "sticky_hdp"
        assert res.n_states == 2
        assert res.state_means == pytest.approx([-10.0, 0.0], abs=0.5)
        # Dwell times should be close to the true 250-sample segments.
        assert np.median(np.concatenate(res.dwell_times_s)) == pytest.approx(0.025, rel=0.1)

    def test_noise_is_one_state(self):
        from nano_ext.detection.hdphmm import fit_sticky_hdp_hmm
        x = np.random.default_rng(1).normal(0, 1, 1500)
        res = fit_sticky_hdp_hmm(x, sampling_rate=SR, n_iter=150, burn_in=75)
        assert res.n_states == 1
        assert max(res.n_states_posterior, key=res.n_states_posterior.get) == 1

    def test_outputs_consistent(self):
        from nano_ext.detection.hdphmm import fit_sticky_hdp_hmm
        x = _switching_signal(seed=2, levels=(0.0, -6.0, -12.0), n_segments=6)
        res = fit_sticky_hdp_hmm(x, sampling_rate=SR, filter_cutoff=2_500.0,
                                 n_iter=150, burn_in=75)
        assert len(res.state_sequence) == len(x)
        assert res.transition_matrix.shape == (res.n_states, res.n_states)
        assert np.allclose(res.transition_matrix.sum(axis=1), 1.0)
        assert len(res.dwell_times_s) == res.n_states
        assert np.all(np.diff(res.state_means) > 0)
        assert np.isnan(res.bic)
        assert sum(res.n_states_posterior.values()) == pytest.approx(1.0)

    def test_too_short_returns_none(self):
        from nano_ext.detection.hdphmm import fit_sticky_hdp_hmm
        assert fit_sticky_hdp_hmm(np.zeros(5), sampling_rate=SR) is None

    def test_analyze_events_dispatch(self):
        from nano_ext.detection.hmm import analyze_events_hmm
        x = _switching_signal(seed=3)
        ev = _make_event(0, len(x), SR)
        (out,) = analyze_events_hmm([ev], x, SR, max_states=5, method="sticky_hdp")
        assert out.hmm_result is not None
        assert out.hmm_result.method == "sticky_hdp"

    def test_analyze_events_unknown_method(self):
        from nano_ext.detection.hmm import analyze_events_hmm
        with pytest.raises(ValueError):
            analyze_events_hmm([], np.zeros(10), SR, method="nope")


# ---------------------------------------------------------------------------
# Event clustering
# ---------------------------------------------------------------------------

def _population_events(seed=0, n_per=60):
    """Two populations: shallow/short and deep/long."""
    rng = np.random.default_rng(seed)
    events = []
    for rel, tau in [(0.3, 2e-4), (0.7, 5e-3)]:
        for _ in range(n_per):
            dur = tau * np.exp(rng.normal(0, 0.3))
            r = rel + rng.normal(0, 0.02)
            events.append(_make_event(
                0, max(1, int(dur * 1e5)), 1e5,
                relative_depth=r, depth=r * 100.0,
            ))
    return events


class TestClusterEvents:
    def test_two_populations(self):
        from nano_ext.analysis.clustering import cluster_events
        events = _population_events()
        res = cluster_events(events)
        assert res.n_clusters == 2
        assert res.counts.sum() == len(events)
        # Every event of one population ends up together.
        first, second = res.labels[:60], res.labels[60:]
        assert len(set(first)) == 1 and len(set(second)) == 1
        assert first[0] != second[0]

    def test_assigns_events(self):
        from nano_ext.analysis.clustering import cluster_events
        events = _population_events(seed=1)
        cluster_events(events)
        assert all(ev.cluster_id is not None for ev in events)
        assert all(0.0 <= ev.cluster_prob <= 1.0 for ev in events)

    def test_single_population(self):
        from nano_ext.analysis.clustering import cluster_events
        rng = np.random.default_rng(2)
        events = [
            _make_event(0, 100, 1e5, relative_depth=0.5 + rng.normal(0, 0.02))
            for _ in range(80)
        ]
        res = cluster_events(events, assign=False)
        assert res.n_clusters == 1
        assert events[0].cluster_id is None

    def test_means_in_feature_units(self):
        from nano_ext.analysis.clustering import cluster_events
        res = cluster_events(_population_events(seed=3))
        j = res.feature_names.index("relative_depth")
        assert sorted(res.means[:, j]) == pytest.approx([0.3, 0.7], abs=0.03)

    def test_few_and_no_events(self):
        from nano_ext.analysis.clustering import cluster_events
        assert cluster_events([]).n_clusters == 0
        res = cluster_events([_make_event(0, 10, 1e5), _make_event(0, 20, 1e5)])
        assert res.n_clusters == 1

    def test_unknown_feature(self):
        from nano_ext.analysis.clustering import cluster_events
        with pytest.raises(ValueError):
            cluster_events(_population_events(), features=("nope",))

    def test_summary_table(self):
        from nano_ext.analysis.clustering import cluster_events
        table = cluster_events(_population_events(seed=4)).summary_table()
        assert list(table.columns[:3]) == ["cluster_id", "n_events", "weight"]
        assert "relative_depth_mean" in table.columns

    def test_csv_columns(self, tmp_path):
        import pandas as pd
        from nano_ext.analysis.clustering import cluster_events
        from nano_ext.outputs.csv_writer import write_events_to_csv
        events = _population_events(seed=5)
        write_events_to_csv(events, tmp_path / "a.csv", 1e5)
        assert "cluster_id" not in pd.read_csv(tmp_path / "a.csv").columns
        cluster_events(events)
        write_events_to_csv(events, tmp_path / "b.csv", 1e5)
        df = pd.read_csv(tmp_path / "b.csv")
        assert {"cluster_id", "cluster_prob"} <= set(df.columns)


# ---------------------------------------------------------------------------
# Bayesian statistics
# ---------------------------------------------------------------------------

class TestCaptureRate:
    def test_posterior_mean_and_interval(self):
        from nano_ext.analysis.bayes_stats import capture_rate_posterior
        post = capture_rate_posterior(100, 10.0)
        assert post.mean == pytest.approx(100.5 / 10.0)
        assert post.lower < 10.0 < post.upper
        # Poisson: relative width ~ 2 * 1.96 / sqrt(n)
        assert (post.upper - post.lower) / post.mean == pytest.approx(0.39, abs=0.05)

    def test_zero_events(self):
        from nano_ext.analysis.bayes_stats import capture_rate_posterior
        post = capture_rate_posterior(0, 5.0)
        assert post.lower >= 0.0
        assert post.upper > 0.0

    def test_invalid_time(self):
        from nano_ext.analysis.bayes_stats import capture_rate_posterior
        with pytest.raises(ValueError):
            capture_rate_posterior(3, 0.0)


class TestDwellTimeMixture:
    def test_close_time_constants(self):
        from nano_ext.analysis.bayes_stats import dwell_time_mixture
        rng = np.random.default_rng(0)
        t = np.concatenate([rng.exponential(1e-3, 400), rng.exponential(3e-3, 400)])
        post = dwell_time_mixture(t, t_min=0.0, n_iter=1500, burn_in=500)
        assert post.n_components == 2

    def test_single_exponential(self):
        from nano_ext.analysis.bayes_stats import dwell_time_mixture
        rng = np.random.default_rng(0)
        t = 1e-4 + rng.exponential(1e-3, 400)
        post = dwell_time_mixture(t, t_min=1e-4, n_iter=1500, burn_in=500)
        assert post.n_components == 1
        assert post.tau_mean[0] == pytest.approx(1e-3, rel=0.15)
        # The posterior is centred on the sample mean (the exponential MLE).
        mle = (t - 1e-4).mean()
        assert post.tau_lower[0] < mle < post.tau_upper[0]

    def test_two_time_constants(self):
        from nano_ext.analysis.bayes_stats import dwell_time_mixture
        rng = np.random.default_rng(1)
        t = np.concatenate([rng.exponential(1e-4, 400), rng.exponential(1e-2, 400)])
        post = dwell_time_mixture(t, t_min=0.0, n_iter=2000, burn_in=700)
        assert post.n_components == 2
        assert post.tau_mean[0] == pytest.approx(1e-4, rel=0.3)
        assert post.tau_mean[1] == pytest.approx(1e-2, rel=0.3)
        assert post.weight_mean.sum() == pytest.approx(1.0)
        assert sum(post.n_components_probs.values()) == pytest.approx(1.0)

    def test_too_few(self):
        from nano_ext.analysis.bayes_stats import dwell_time_mixture
        with pytest.raises(ValueError):
            dwell_time_mixture([1e-3])


def test_summarize_event_statistics_json():
    import json
    from nano_ext.analysis.bayes_stats import summarize_event_statistics
    from nano_ext.analysis.clustering import cluster_events
    events = _population_events(seed=6)
    cluster_events(events)
    stats = summarize_event_statistics(events, observation_time=10.0)
    json.dumps(stats)  # must be serialisable
    assert stats["all"]["capture_rate"]["n_events"] == len(events)
    assert "dwell_time" in stats["all"]
    assert set(stats["clusters"]) == {"0", "1"}


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------

def _pipeline(**cfg):
    from nano_ext.pipeline import run_pipeline
    from nano_ext.testing.synthetic import SyntheticEventSpec, generate_synthetic_signal
    events = [
        SyntheticEventSpec(start_time=0.02 + i * 0.03, levels=[(0.004, 120.0), (0.004, 60.0)])
        for i in range(5)
    ]
    syn = generate_synthetic_signal(
        duration_sec=0.2, sampling_rate=50_000, events=events, seed=0,
    )
    config = DetectionConfig(
        baseline_window_sec=0.05, min_event_duration_sec=0.001,
        gmm_max_components=4, **cfg,
    )
    return run_pipeline(syn.signal_data, config=config)


class TestPipelineIntegration:
    def test_dpgmm_threshold_and_sublevels(self):
        res = _pipeline(threshold_method="dpgmm", sublevel_method="dpgmm")
        assert res.n_events == 5
        assert res.n_multilevel >= 4

    def test_bocpd_sublevels(self):
        res = _pipeline(sublevel_method="bocpd")
        assert res.n_events == 5
        assert res.n_multilevel >= 4

    def test_sticky_hdp_and_clustering(self):
        res = _pipeline(hmm_analysis=True, hmm_method="sticky_hdp", cluster_events=True)
        assert all(ev.hmm_result is not None for ev in res.events)
        assert res.cluster_result is not None
        assert res.cluster_result.n_clusters >= 1
        assert len(res.cluster_result.labels) == res.n_events
        assert "Populations:" in res.summary()

    def test_defaults_unchanged(self):
        res = _pipeline()
        assert res.cluster_result is None
        assert all(ev.cluster_id is None for ev in res.events)
