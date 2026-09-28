"""Tests for the GUI back end (service layer, downsampling, view helpers).

The Dash app itself is only smoke-tested (layout builds, callbacks
register); browser interaction is exercised separately with Playwright.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from nano_ext.models import DetectionConfig, EventDirection, SignalData


# ---------------------------------------------------------------------------
# Downsampling
# ---------------------------------------------------------------------------

class TestDownsample:
    def test_minmax_keeps_short_spike(self):
        from nano_ext.gui.downsample import minmax_indices
        y = np.zeros(1_000_000, dtype=np.float32)
        y[123_456] = -50.0  # a single-sample blockade
        idx = minmax_indices(y, 2000)
        assert len(idx) <= 2100
        assert 123_456 in set(idx.tolist())
        assert np.all(np.diff(idx) > 0)

    def test_envelope_fallback(self):
        from nano_ext.gui.downsample import _envelope_indices
        y = np.sin(np.linspace(0, 50, 100_000))
        y[40_000] = 9.0
        idx = _envelope_indices(y, 500)
        assert 40_000 in set(idx.tolist())

    def test_short_input_unchanged(self):
        from nano_ext.gui.downsample import minmax_indices
        np.testing.assert_array_equal(minmax_indices(np.arange(10.0), 100), np.arange(10))

    def test_trace_index_view_window(self):
        from nano_ext.gui.downsample import TraceIndex
        sd = SignalData(signal=np.arange(100_000, dtype=np.float32), sampling_rate=1000.0)
        ti = TraceIndex(sd)
        v = ti.view({"x": sd.signal}, 10.0, 20.0, n_out=200)
        assert v["n_raw"] == 10_000
        assert v["t"][0] >= 10.0 and v["t"][-1] < 20.0
        np.testing.assert_allclose(v["x"], v["t"] * 1000.0)

    def test_gap_separators_for_concatenated_signal(self):
        from nano_ext.gui.downsample import TraceIndex
        from nano_ext.preprocessing.segments import concatenate_signal_ranges
        sd = SignalData(signal=np.arange(10_000, dtype=np.float32), sampling_rate=100.0)
        cat, _ = concatenate_signal_ranges(sd, [(0.0, 30.0), (60.0, 100.0)])
        v = TraceIndex(cat).view({"x": cat.signal}, None, None, n_out=10_000)
        assert np.isnan(v["t"]).sum() == 1  # one gap between the two ranges
        gap = int(np.flatnonzero(np.isnan(v["t"]))[0])
        assert v["t"][gap - 1] < 30.0 and v["t"][gap + 1] >= 60.0


# ---------------------------------------------------------------------------
# Service layer
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def demo_abf(tmp_path_factory):
    pytest.importorskip("pyabf")
    from pyabf import abfWriter
    from nano_ext.testing.synthetic import SyntheticEventSpec, generate_synthetic_signal

    events = [SyntheticEventSpec(start_time=0.05 + 0.04 * i,
                                 levels=[(0.003, 120.0)] if i % 2 else [(0.002, 150.0)])
              for i in range(20)]
    sig = generate_synthetic_signal(duration_sec=1.0, sampling_rate=100_000,
                                    events=events, seed=0).signal_data.signal
    folder = tmp_path_factory.mktemp("rec")
    path = folder / "demo.abf"
    abfWriter.writeABF1(sig[None, :].astype(np.float32), str(path), 100_000)
    (folder / "sub").mkdir()
    (folder / "notes.txt").write_text("not a recording")
    return path


class TestService:
    def test_list_directory(self, demo_abf):
        from nano_ext.gui.service import list_directory
        folder, dirs, files = list_directory(demo_abf.parent)
        assert [d.name for d in dirs] == ["sub"]
        assert [f.name for f in files] == ["demo.abf"]
        with pytest.raises(NotADirectoryError):
            list_directory(demo_abf)

    def test_recording_info_and_load(self, demo_abf):
        from nano_ext.gui.service import load_recording, recording_info
        info = recording_info(demo_abf)
        assert info["format"] == "abf"
        assert info["sampling_rate_hz"] == 100_000
        assert info["duration_sec"] == pytest.approx(1.0)
        assert all(ch.isprintable() for ch in info["channel_names"][0])
        sd = load_recording(demo_abf)
        assert len(sd.signal) == 100_000 and sd.signal.dtype == np.float32

    def test_binary_needs_sampling_rate(self, tmp_path):
        from nano_ext.gui.service import load_recording
        p = tmp_path / "x.bin"
        np.zeros(100, np.int16).tofile(p)
        with pytest.raises(ValueError):
            load_recording(p)
        assert len(load_recording(p, sampling_rate=1e5).signal) == 100

    def test_build_config_manual(self):
        from nano_ext.gui.service import build_config
        cfg = build_config({
            "auto_tune": False, "event_direction": "both", "filter_cutoff_khz": 20,
            "baseline_window_sec": 2, "min_event_duration_ms": 0.1, "merge_gap_ms": "",
            "threshold_method": "dpgmm", "sublevel_method": "bocpd", "hmm_method": "sticky_hdp",
            "cluster_events": True, "n_jobs": 2,
        })
        assert cfg.event_direction == EventDirection.BOTH
        assert cfg.filter_cutoff == 20_000
        assert cfg.baseline_window_sec == 2
        assert cfg.min_event_duration_sec == pytest.approx(1e-4)
        assert cfg.merge_gap_sec is None
        assert cfg.threshold_method == "dpgmm" and cfg.sublevel_method == "bocpd"
        assert cfg.hmm_analysis and cfg.hmm_method == "sticky_hdp"
        assert cfg.cluster_events and cfg.n_jobs == 2

    def test_build_config_auto_tune_keeps_explicit(self):
        from nano_ext.gui.service import build_config
        sd = SignalData(signal=np.random.default_rng(0).normal(200, 5, 200_000).astype(np.float32),
                        sampling_rate=100_000)
        cfg = build_config({"auto_tune": True, "baseline_window_sec": 0.3}, sd)
        assert cfg.baseline_window_sec == 0.3              # explicit wins
        assert cfg.min_event_duration_sec is not None       # filled by auto-tune
        assert not cfg.hmm_analysis

    def test_build_config_prefiltered(self):
        from nano_ext.gui.service import build_config
        cfg = build_config({"auto_tune": False, "apply_filter": False, "filter_cutoff_khz": 10})
        assert not cfg.apply_filter and cfg.pre_applied_filter_cutoff == 10_000

    def test_normalise_ranges(self):
        from nano_ext.gui.service import normalise_ranges
        out = normalise_ranges([(5, 3), (2, 4), (9, 12), ("x", 1), (-1, 0.5)], 10.0)
        assert out == [(0.0, 0.5), (2.0, 5.0), (9.0, 10.0)]

    def test_job_runs_in_background(self, demo_abf):
        from nano_ext.gui import service
        sd = service.load_recording(demo_abf)
        cfg = service.build_config({"auto_tune": True, "cluster_events": True, "n_jobs": 1}, sd)
        job = service.Job()
        thread = service.start_job(job, signal_data=sd, config=cfg, bayes_stats=True,
                                   exclude_ranges=[(0.5, 0.52)])
        thread.join(120)
        assert job.status == "done", job.error
        assert job.result.n_events >= 15
        assert job.steps and job.elapsed > 0
        assert job.stats is not None and "all" in job.stats
        rows = service.events_rows(job.result)
        assert len(rows) == job.result.n_events and {"id", "duration_ms", "cluster"} <= set(rows[0])
        assert service.events_csv(job.result).startswith("event_id,")
        assert service.clusters_csv(job.result).startswith("cluster_id,")
        assert service.stats_json(job.stats).startswith("{")
        assert any("Methods:" in line for line in service.summary_lines(job.result, cfg))

    def test_job_error_is_reported(self):
        from nano_ext.gui import service
        job = service.Job()
        sd = SignalData(signal=np.zeros(10, np.float32), sampling_rate=1e5)
        service.start_job(job, signal_data=sd, config=DetectionConfig(),
                          analysis_range=(5.0, 6.0)).join(30)
        assert job.status == "error" and "ValueError" in job.error


# ---------------------------------------------------------------------------
# App helpers and smoke test
# ---------------------------------------------------------------------------

dash = pytest.importorskip("dash")


class TestAppHelpers:
    def test_parse_relayout(self):
        from nano_ext.gui.app import parse_relayout
        assert parse_relayout({"xaxis.range[0]": 1, "xaxis.range[1]": 2}, None) == [1.0, 2.0]
        assert parse_relayout({"xaxis.range": [3, 4]}, None) == [3.0, 4.0]
        assert parse_relayout({"xaxis.autorange": True}, [1, 2]) is None
        assert parse_relayout({"yaxis.range[0]": 0}, [1, 2]) == [1, 2]
        assert parse_relayout(None, [1, 2]) == [1, 2]

    def test_selection_range(self):
        from nano_ext.gui.app import selection_range
        assert selection_range({"range": {"x": [5, 2]}}) == [2.0, 5.0]
        assert selection_range({"points": []}) is None
        assert selection_range(None) is None

    def test_rows_roundtrip(self):
        from nano_ext.gui.app import ranges_from_rows, rows_from_ranges
        assert ranges_from_rows(rows_from_ranges([(1.0, 2.5)])) == [(1.0, 2.5)]

    def test_status_text(self):
        from nano_ext.gui import service
        from nano_ext.gui.app import status_text
        job = service.Job(status="running", step="Filter", started=time.time())
        assert "Filter" in status_text(job)[0]
        assert status_text(service.Job(status="error", error="Boom\ntrace"))[1] == "status error"

    def test_main_view_before_and_after_run(self, demo_abf):
        from nano_ext.gui import service
        from nano_ext.gui.app import GuiState, main_view
        from nano_ext.gui.downsample import TraceIndex
        st = GuiState(str(demo_abf.parent))
        fig, info = main_view(st, None, None)
        assert info == {}
        st.sd = service.load_recording(demo_abf)
        st.index = TraceIndex(st.sd)
        fig, info = main_view(st, None, None, exclude_ranges=[(0.2, 0.3)])
        assert info["raw_samples"] == 100_000 and len(fig.data) == 1
        job = service.Job()
        service.start_job(job, signal_data=st.sd,
                          config=service.build_config({"n_jobs": 1}, st.sd)).join(120)
        st.job = job
        fig, info = main_view(st, 0.0, 0.5, selected_event=0)
        names = [t.name for t in fig.data]
        assert {"Filtered", "Baseline", "Threshold", "Events"} <= set(names)
        assert info["events_visible"] >= 1

    def test_browse_options(self, demo_abf):
        from nano_ext.gui.app import browse_options
        folder, opts = browse_options(str(demo_abf.parent))
        values = [o["value"] for o in opts]
        assert values[0].startswith("dir:")          # parent
        assert "file:" + str(demo_abf) in values
        assert not any(v.endswith("notes.txt") for v in values)

    def test_create_app(self, demo_abf):
        from nano_ext.gui import create_app
        app = create_app(folder=str(demo_abf.parent), path=str(demo_abf))
        assert len(app.callback_map) >= 12
        assert app._nano_state.folder == str(demo_abf.parent.resolve())


class TestFigures:
    def test_histogram_scatter_dwell(self, demo_abf):
        from nano_ext.gui import figures, service
        sd = service.load_recording(demo_abf)
        job = service.Job()
        service.start_job(job, signal_data=sd, bayes_stats=True,
                          config=service.build_config({"n_jobs": 1, "cluster_events": True}, sd)).join(120)
        res = job.result
        assert len(figures.histogram_figure(res, service.residual_sample(res)).data) >= 3
        assert len(figures.scatter_figure(res).data) >= 1
        assert len(figures.dwell_figure(res, job.stats).data) == 2
        assert figures.event_figure(res, 0).layout.height is None
