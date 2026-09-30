"""Matching and metrics of the benchmark suite (benchmarks/common.py)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "benchmarks"))
from common import evaluate, interval_iou, match_events, robust_baseline  # noqa: E402


def ev(a, b, depth=50.0, n=1, bounds=()):
    return {"start": a, "end": b, "depth": depth, "n_levels": n, "level_bounds": list(bounds)}


def test_interval_iou():
    assert interval_iou(0, 2, 1, 3) == pytest.approx(1 / 3)
    assert interval_iou(0, 1, 1, 2) == 0.0


def test_matching_is_one_to_one_and_respects_iou():
    truth = [ev(0.0, 1.0), ev(2.0, 3.0), ev(5.0, 6.0)]
    det = [ev(0.1, 1.0), ev(0.0, 0.2), ev(2.0, 2.2), ev(8.0, 9.0)]
    m = match_events(truth, det, 0.5)
    assert [(i, j) for i, j, _ in m] == [(0, 0)]
    assert len(match_events(truth, det, 1e-12)) == 2  # any overlap: (0, 0) and (1, 2)


def test_evaluate_metrics():
    truth = [ev(0.0, 1.0, 50, 2, [0.5]), ev(2.0, 3.0, 40), ev(4.0, 5.0, 30)]
    det = [ev(0.0, 1.1, 55, 2, [0.52]), ev(2.0, 3.0, 40, 2, [2.5]), ev(7.0, 7.5)]
    m = evaluate(truth, det)
    assert (m["tp"], m["recall"], m["precision"]) == (2, pytest.approx(2 / 3), pytest.approx(2 / 3))
    assert m["dwell_err"] == pytest.approx(0.05)  # median of 0.1 and 0.0
    assert m["level_acc"] == 0.5
    assert m["bound_err_us"] == pytest.approx(20_000.0)


def test_methods_without_levels_report_nan():
    m = evaluate([ev(0, 1)], [dict(ev(0, 1), n_levels=None)])
    assert np.isnan(m["level_acc"]) and m["f1"] == 1.0


def test_robust_baseline_ignores_events_and_follows_drift():
    rng = np.random.default_rng(0)
    n, sr = 2_000_000, 100_000.0  # 20 s
    true = 100.0 + np.linspace(0, 5, n)
    x = true + rng.normal(0, 1, n)
    for a in range(1_000, n, 20_000):  # 5 ms events every 200 ms
        x[a:a + 500] -= 50.0
    x[500_000:540_000] -= 50.0  # one long (0.4 s) event
    base = robust_baseline(x, sr)
    assert np.max(np.abs(base - true)) < 0.5
