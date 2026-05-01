"""Tests for GMM+BIC threshold determination."""

import numpy as np
import pytest

from nano_ext.detection.threshold import (
    _gaussian_crossing,
    _identify_baseline_component,
    determine_threshold,
)

slow = pytest.mark.slow


class TestGaussianCrossing:
    def test_crossing_lies_between_means(self):
        x = _gaussian_crossing(0.0, 1.0, 0.5, -10.0, 1.0, 0.5)
        assert -10.0 <= x <= 0.0

    def test_equal_variance_equal_weight_is_midpoint(self):
        # Equal sigma, equal weight → crossing exactly at midpoint
        x = _gaussian_crossing(0.0, 1.0, 0.5, -10.0, 1.0, 0.5)
        assert abs(x - (-5.0)) < 1e-6

    def test_unequal_variance_between_means(self):
        x = _gaussian_crossing(0.0, 1.0, 0.7, -8.0, 2.0, 0.3)
        assert -8.0 <= x <= 0.0

    def test_symmetry(self):
        # Swapping the two components should give the same crossing
        x1 = _gaussian_crossing(0.0, 1.0, 0.5, -10.0, 1.0, 0.5)
        x2 = _gaussian_crossing(-10.0, 1.0, 0.5, 0.0, 1.0, 0.5)
        assert abs(x1 - x2) < 1e-6


class TestIdentifyBaselineComponent:
    def test_selects_component_closest_to_zero(self):
        means = np.array([-20.0, -1.0, 5.0])
        weights = np.array([0.2, 0.6, 0.2])
        idx = _identify_baseline_component(means, weights)
        assert idx == 1  # -1.0 is closest to 0

    def test_single_component(self):
        means = np.array([2.0])
        weights = np.array([1.0])
        idx = _identify_baseline_component(means, weights)
        assert idx == 0

    def test_ignores_low_weight_component(self):
        # Component at 0 has very low weight — should still be selected
        # (the function ranks by abs(mean) among candidates with weight >= min_weight)
        means = np.array([-0.1, -15.0])
        weights = np.array([0.9, 0.1])
        idx = _identify_baseline_component(means, weights)
        assert idx == 0  # closest to 0


class TestDetermineThreshold:
    @slow
    def test_single_gaussian_gives_one_component(self):
        rng = np.random.default_rng(0)
        data = rng.normal(0.0, 2.0, 10_000)
        result = determine_threshold(data, max_components=5, seed=0)
        assert result.n_components == 1

    @slow
    def test_single_gaussian_threshold_below_mean(self):
        rng = np.random.default_rng(0)
        data = rng.normal(0.0, 2.0, 10_000)
        result = determine_threshold(data, max_components=5, seed=0)
        assert result.threshold < -5.0

    @slow
    def test_two_component_threshold_between_peaks(self):
        rng = np.random.default_rng(1)
        data = np.concatenate([
            rng.normal(0.0, 1.0, 5_000),
            rng.normal(-20.0, 1.5, 2_000),
        ])
        result = determine_threshold(data, max_components=5, seed=1)
        assert result.n_components >= 2
        assert -20.0 < result.threshold < 0.0

    @slow
    def test_result_arrays_match_n_components(self):
        rng = np.random.default_rng(0)
        data = rng.normal(0.0, 1.0, 5_000)
        result = determine_threshold(data, max_components=4, seed=0)
        k = result.n_components
        assert len(result.component_means) == k
        assert len(result.component_stds) == k
        assert len(result.component_weights) == k

    @slow
    def test_bic_scores_length(self):
        rng = np.random.default_rng(0)
        data = rng.normal(0.0, 1.0, 3_000)
        result = determine_threshold(data, max_components=4, seed=0)
        assert len(result.bic_scores) == 4

    def test_unknown_criterion_raises(self):
        data = np.random.default_rng(0).normal(0, 1, 500)
        with pytest.raises(ValueError, match="criterion"):
            determine_threshold(data, criterion="unknown")

    @slow
    def test_aic_criterion_accepted(self):
        data = np.random.default_rng(0).normal(0.0, 1.0, 3_000)
        result = determine_threshold(data, max_components=3, criterion="aic", seed=0)
        assert result.threshold < 0.0
