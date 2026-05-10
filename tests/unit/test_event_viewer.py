"""Tests for the per-event waveform viewer (Phase 6.1)."""

from __future__ import annotations

import numpy as np
import pytest

from nano_ext.testing.synthetic import generate_synthetic_signal, SyntheticEventSpec
from nano_ext.models import DetectionConfig, SignalData
from nano_ext.pipeline import run_pipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _result(n_events: int = 3, sr: float = 50_000, seed: int = 0):
    """Return a PipelineResult with the given number of injected events."""
    if n_events == 0:
        syn = generate_synthetic_signal(
            duration_sec=0.05, sampling_rate=sr, events=[], seed=seed,
        )
    else:
        events = [
            SyntheticEventSpec(
                start_time=i * 0.008 + 0.003,
                levels=[(0.002, 150.0)],  # baseline ≈ 200, depth ≈ 50 pA
            )
            for i in range(n_events)
        ]
        syn = generate_synthetic_signal(
            duration_sec=0.05, sampling_rate=sr, events=events, seed=seed,
        )
    config = DetectionConfig(
        apply_filter=False,
        baseline_window_sec=0.02,
        min_event_duration_sec=0.0005,
        gmm_max_components=4,
    )
    return run_pipeline(syn.signal_data, config=config, analyze_sublevel=False, verbose=False)


# ---------------------------------------------------------------------------
# make_event_figure
# ---------------------------------------------------------------------------

class TestMakeEventFigure:
    def test_returns_figure(self):
        pytest.importorskip("plotly")
        from nano_ext.outputs.event_viewer import make_event_figure
        result = _result(n_events=3)
        if not result.events:
            pytest.skip("No events detected in fixture")
        fig = make_event_figure(result, 0)
        assert fig is not None
        assert hasattr(fig, "data")

    def test_figure_has_two_rows(self):
        pytest.importorskip("plotly")
        from nano_ext.outputs.event_viewer import make_event_figure
        result = _result(n_events=3)
        if not result.events:
            pytest.skip("No events detected in fixture")
        fig = make_event_figure(result, 0)
        yaxes = {trace.yaxis for trace in fig.data}
        assert len(yaxes) >= 2

    def test_out_of_range_raises(self):
        pytest.importorskip("plotly")
        from nano_ext.outputs.event_viewer import make_event_figure
        result = _result(n_events=3)
        if not result.events:
            pytest.skip("No events detected in fixture")
        with pytest.raises(IndexError):
            make_event_figure(result, len(result.events) + 100)

    def test_empty_result_returns_empty_figure(self):
        pytest.importorskip("plotly")
        from nano_ext.outputs.event_viewer import make_event_figure
        result = _result(n_events=0)
        fig = make_event_figure(result, 0)
        assert fig is not None

    def test_padding_gives_more_points(self):
        """Larger padding should give more x-axis points in the first trace."""
        pytest.importorskip("plotly")
        from nano_ext.outputs.event_viewer import make_event_figure
        result = _result(n_events=3)
        if not result.events:
            pytest.skip("No events detected in fixture")
        fig0 = make_event_figure(result, 0, padding_ms=0.1)
        fig1 = make_event_figure(result, 0, padding_ms=2.0)
        n0 = len(fig0.data[0].x)
        n1 = len(fig1.data[0].x)
        assert n1 >= n0


# ---------------------------------------------------------------------------
# make_event_grid
# ---------------------------------------------------------------------------

class TestMakeEventGrid:
    def test_returns_figure(self):
        pytest.importorskip("plotly")
        from nano_ext.outputs.event_viewer import make_event_grid
        result = _result(n_events=3)
        if not result.events:
            pytest.skip("No events detected in fixture")
        fig = make_event_grid(result)
        assert fig is not None
        assert hasattr(fig, "data")

    def test_empty_result_returns_empty_figure(self):
        pytest.importorskip("plotly")
        from nano_ext.outputs.event_viewer import make_event_grid
        result = _result(n_events=0)
        fig = make_event_grid(result)
        assert fig is not None

    def test_n_cols_accepted(self):
        pytest.importorskip("plotly")
        from nano_ext.outputs.event_viewer import make_event_grid
        result = _result(n_events=3)
        if len(result.events) < 2:
            pytest.skip("Need at least 2 events")
        fig2 = make_event_grid(result, n_cols=2)
        fig3 = make_event_grid(result, n_cols=3)
        assert fig2 is not None
        assert fig3 is not None

    def test_max_events_limits_scatter_traces(self):
        pytest.importorskip("plotly")
        from nano_ext.outputs.event_viewer import make_event_grid
        result = _result(n_events=3)
        n_events = len(result.events)
        if n_events < 2:
            pytest.skip("Need at least 2 events")
        max_ev = 1
        fig = make_event_grid(result, max_events=max_ev)
        scatter_traces = [t for t in fig.data if t.type == "scatter"]
        assert len(scatter_traces) <= max_ev


# ---------------------------------------------------------------------------
# dump_event_waveform
# ---------------------------------------------------------------------------

class TestDumpEventWaveform:
    def test_npz_export(self, tmp_path):
        from nano_ext.outputs.event_viewer import dump_event_waveform
        result = _result(n_events=3)
        if not result.events:
            pytest.skip("No events detected in fixture")
        out = tmp_path / "ev0"
        dump_event_waveform(result, 0, out, fmt="npz")
        npz_path = out.with_suffix(".npz")
        assert npz_path.exists()
        data = np.load(npz_path)
        assert "time_s" in data
        assert "signal" in data
        assert "baseline" in data
        assert "residual" in data
        assert len(data["time_s"]) == len(data["signal"]) == len(data["baseline"])

    def test_csv_export(self, tmp_path):
        pytest.importorskip("pandas")
        from nano_ext.outputs.event_viewer import dump_event_waveform
        result = _result(n_events=3)
        if not result.events:
            pytest.skip("No events detected in fixture")
        out = tmp_path / "ev0"
        dump_event_waveform(result, 0, out, fmt="csv")
        csv_path = out.with_suffix(".csv")
        assert csv_path.exists()
        import pandas as pd
        df = pd.read_csv(csv_path)
        assert list(df.columns) == ["time_s", "signal", "baseline", "residual"]
        assert len(df) > 0

    def test_unknown_format_raises(self, tmp_path):
        from nano_ext.outputs.event_viewer import dump_event_waveform
        result = _result(n_events=3)
        if not result.events:
            pytest.skip("No events detected in fixture")
        with pytest.raises(ValueError, match="Unknown format"):
            dump_event_waveform(result, 0, tmp_path / "ev", fmt="json")

    def test_exported_window_covers_event(self, tmp_path):
        """The exported time_s should start before and end after the event."""
        from nano_ext.outputs.event_viewer import dump_event_waveform
        result = _result(n_events=3)
        if not result.events:
            pytest.skip("No events detected in fixture")
        ev = result.events[0]
        out = tmp_path / "ev0"
        dump_event_waveform(result, 0, out, fmt="npz", padding_ms=0.5)
        data = np.load(out.with_suffix(".npz"))
        t = data["time_s"]
        assert t[0] <= ev.start_time
        assert t[-1] >= ev.end_time
