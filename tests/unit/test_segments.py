"""Tests for preprocessing/segments.py — signal range cropping and artifact exclusion."""

import numpy as np
import pytest

from nano_ext.models import SignalData
from nano_ext.preprocessing.segments import (
    build_keep_ranges,
    concatenate_signal_ranges,
    crop_signal_to_range,
    insert_nan_at_gaps,
    restore_event_timestamps,
    restore_event_timestamps_mapped,
)
from nano_ext.models import Event, EventDirection, EventType, SubLevel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signal(duration_sec: float = 10.0, sr: float = 1000.0) -> SignalData:
    n = int(duration_sec * sr)
    signal = np.arange(n, dtype=np.float64)  # deterministic ramp for easy verification
    return SignalData(signal=signal, sampling_rate=sr)


def _make_event(start_idx: int, end_idx: int, sr: float) -> Event:
    return Event(
        start_idx=start_idx,
        end_idx=end_idx,
        start_time=start_idx / sr,
        end_time=end_idx / sr,
        duration=(end_idx - start_idx) / sr,
        mean_current=-10.0,
        std_current=1.0,
        baseline_current=100.0,
        depth=10.0,
        relative_depth=0.1,
        area=0.001,
        direction=EventDirection.DOWN,
    )


def _make_event_with_sublevel(start_idx: int, end_idx: int, sr: float) -> Event:
    ev = _make_event(start_idx, end_idx, sr)
    sl = SubLevel(
        start_idx=start_idx + 5,
        end_idx=end_idx - 5,
        start_time=(start_idx + 5) / sr,
        end_time=(end_idx - 5) / sr,
        duration=(end_idx - start_idx - 10) / sr,
        mean_current=-10.0,
        std_current=1.0,
        level_index=0,
    )
    ev.sublevels = [sl]
    ev.n_levels = 2
    return ev


# ---------------------------------------------------------------------------
# crop_signal_to_range
# ---------------------------------------------------------------------------

class TestCropSignalToRange:
    def test_basic_crop(self):
        sd = _make_signal(duration_sec=10.0, sr=1000.0)
        cropped, offset = crop_signal_to_range(sd, t_start=2.0, t_end=5.0)
        assert offset == 2000
        assert len(cropped.signal) == 3000
        # First sample should be original sample 2000
        assert cropped.signal[0] == pytest.approx(2000.0)

    def test_time_array_is_absolute(self):
        """Time array should reflect original-file timestamps, not start at 0."""
        sd = _make_signal(duration_sec=10.0, sr=1000.0)
        cropped, offset = crop_signal_to_range(sd, t_start=2.0, t_end=5.0)
        assert cropped.time[0] == pytest.approx(2.0, abs=1e-6)
        assert cropped.time[-1] == pytest.approx(4.999, abs=0.002)

    def test_sampling_rate_preserved(self):
        sd = _make_signal(sr=250_000.0)
        cropped, _ = crop_signal_to_range(sd, t_start=1.0, t_end=3.0)
        assert cropped.sampling_rate == 250_000.0

    def test_metadata_preserved(self):
        sd = _make_signal()
        sd.metadata["source"] = "test"
        sd.units = "nA"
        cropped, _ = crop_signal_to_range(sd, t_start=1.0, t_end=2.0)
        assert cropped.units == "nA"
        assert cropped.metadata["source"] == "test"

    def test_clamp_to_recording_bounds(self):
        """t_start < 0 or t_end > duration should clamp to valid range."""
        sd = _make_signal(duration_sec=10.0, sr=1000.0)
        # t_start beyond 0, t_end beyond recording end
        cropped, offset = crop_signal_to_range(sd, t_start=0.0, t_end=20.0)
        assert offset == 0
        assert len(cropped.signal) == 10_000  # full signal

    def test_raises_if_start_ge_end(self):
        sd = _make_signal()
        with pytest.raises(ValueError, match="strictly less than"):
            crop_signal_to_range(sd, t_start=5.0, t_end=5.0)

    def test_raises_if_range_outside_recording(self):
        sd = _make_signal(duration_sec=10.0)
        with pytest.raises(ValueError, match="outside the recording"):
            crop_signal_to_range(sd, t_start=15.0, t_end=20.0)

    def test_full_range_is_identity(self):
        sd = _make_signal(duration_sec=5.0, sr=1000.0)
        cropped, offset = crop_signal_to_range(sd, t_start=0.0, t_end=5.0)
        assert offset == 0
        np.testing.assert_array_equal(cropped.signal, sd.signal)


# ---------------------------------------------------------------------------
# restore_event_timestamps
# ---------------------------------------------------------------------------

class TestRestoreEventTimestamps:
    def test_zero_offset_noop(self):
        sr = 1000.0
        ev = _make_event(start_idx=100, end_idx=200, sr=sr)
        events = restore_event_timestamps([ev], sample_offset=0, sampling_rate=sr)
        assert events[0].start_idx == 100
        assert events[0].start_time == pytest.approx(0.1)

    def test_adds_offset_to_indices(self):
        sr = 1000.0
        offset = 2000
        ev = _make_event(start_idx=500, end_idx=600, sr=sr)
        events = restore_event_timestamps([ev], sample_offset=offset, sampling_rate=sr)
        assert events[0].start_idx == 2500
        assert events[0].end_idx == 2600

    def test_adds_offset_to_times(self):
        sr = 1000.0
        offset = 2000
        ev = _make_event(start_idx=500, end_idx=600, sr=sr)
        events = restore_event_timestamps([ev], sample_offset=offset, sampling_rate=sr)
        assert events[0].start_time == pytest.approx(2.5)
        assert events[0].end_time == pytest.approx(2.6)

    def test_restores_sublevel_timestamps(self):
        sr = 1000.0
        offset = 3000
        ev = _make_event_with_sublevel(start_idx=100, end_idx=200, sr=sr)
        events = restore_event_timestamps([ev], sample_offset=offset, sampling_rate=sr)
        sl = events[0].sublevels[0]
        assert sl.start_idx == 3105  # 105 + 3000
        assert sl.end_idx == 3195   # 195 + 3000
        assert sl.start_time == pytest.approx(3.105)
        assert sl.end_time == pytest.approx(3.195)

    def test_duration_unchanged(self):
        sr = 1000.0
        ev = _make_event(start_idx=100, end_idx=200, sr=sr)
        original_duration = ev.duration
        events = restore_event_timestamps([ev], sample_offset=5000, sampling_rate=sr)
        assert events[0].duration == pytest.approx(original_duration)

    def test_multiple_events(self):
        sr = 1000.0
        offset = 1000
        events = [
            _make_event(start_idx=10, end_idx=20, sr=sr),
            _make_event(start_idx=50, end_idx=80, sr=sr),
        ]
        restored = restore_event_timestamps(events, sample_offset=offset, sampling_rate=sr)
        assert restored[0].start_idx == 1010
        assert restored[1].start_idx == 1050


# ---------------------------------------------------------------------------
# Integration: crop → run_pipeline → check timestamps
# ---------------------------------------------------------------------------

class TestAnalysisRangeIntegration:
    """Verify that events from a cropped pipeline run have original-file timestamps."""

    def test_timestamps_in_original_file_coordinates(self):
        """Events found after cropping should have start_time >= t_start."""
        import numpy as np
        from nano_ext.models import DetectionConfig, EventDirection
        from nano_ext.pipeline import run_pipeline

        rng = np.random.default_rng(0)
        sr = 10_000.0
        duration = 4.0
        n = int(duration * sr)
        # Flat baseline with a single clear downward event at t=2.5s
        signal = np.zeros(n, dtype=np.float32)
        ev_start = int(2.5 * sr)
        ev_end = int(2.52 * sr)
        signal[ev_start:ev_end] = -200.0  # large blockade
        signal += rng.normal(0, 0.5, n).astype(np.float32)

        sd = SignalData(signal=signal, sampling_rate=sr)
        config = DetectionConfig(
            apply_filter=False,
            event_direction=EventDirection.DOWN,
            min_event_duration_sec=0.005,
            gmm_max_samples=10_000,
        )

        # Crop to [2.0, 3.0) — the event is inside this range
        result = run_pipeline(
            sd, config=config, analyze_sublevel=False,
            analysis_range=(2.0, 3.0),
        )

        # All detected events must have timestamps inside the original-file range
        for ev in result.events:
            assert ev.start_time >= 2.0 - 1e-6, (
                f"Event start_time {ev.start_time:.4f} should be >= 2.0"
            )
            assert ev.end_time <= 3.0 + 1e-6, (
                f"Event end_time {ev.end_time:.4f} should be <= 3.0"
            )
            assert ev.start_idx >= int(2.0 * sr) - 1

    def test_no_range_backward_compatible(self):
        """analysis_range=None must produce identical results to not passing it."""
        from nano_ext.models import DetectionConfig
        from nano_ext.pipeline import run_pipeline

        rng = np.random.default_rng(42)
        sr = 5_000.0
        n = int(2.0 * sr)
        signal = rng.normal(0, 1, n).astype(np.float32)
        sd = SignalData(signal=signal, sampling_rate=sr)
        config = DetectionConfig(apply_filter=False, gmm_max_samples=5_000)

        r1 = run_pipeline(sd, config=config, analyze_sublevel=False, analysis_range=None)
        r2 = run_pipeline(sd, config=config, analyze_sublevel=False)

        assert len(r1.events) == len(r2.events)
        np.testing.assert_allclose(r1.baseline_result.noise_std, r2.baseline_result.noise_std)


# ---------------------------------------------------------------------------
# Stage 2: build_keep_ranges
# ---------------------------------------------------------------------------

class TestBuildKeepRanges:
    def test_no_exclusions_returns_full_range(self):
        keep = build_keep_ranges(10.0, [])
        assert keep == [(0.0, 10.0)]

    def test_exclude_start(self):
        keep = build_keep_ranges(10.0, [(0.0, 2.0)])
        assert keep == [(2.0, 10.0)]

    def test_exclude_end(self):
        keep = build_keep_ranges(10.0, [(8.0, 10.0)])
        assert keep == [(0.0, 8.0)]

    def test_exclude_middle(self):
        keep = build_keep_ranges(10.0, [(3.0, 5.0)])
        assert keep == [(0.0, 3.0), (5.0, 10.0)]

    def test_multiple_exclusions(self):
        keep = build_keep_ranges(10.0, [(1.0, 2.0), (5.0, 6.0)])
        assert keep == [(0.0, 1.0), (2.0, 5.0), (6.0, 10.0)]

    def test_overlapping_exclusions_are_merged(self):
        keep = build_keep_ranges(10.0, [(1.0, 4.0), (3.0, 6.0)])
        assert keep == [(0.0, 1.0), (6.0, 10.0)]

    def test_exclude_entire_recording(self):
        keep = build_keep_ranges(10.0, [(0.0, 10.0)])
        assert keep == []

    def test_clamped_to_recording_bounds(self):
        keep = build_keep_ranges(10.0, [(-1.0, 2.0)])
        assert keep == [(2.0, 10.0)]


# ---------------------------------------------------------------------------
# Stage 2: concatenate_signal_ranges
# ---------------------------------------------------------------------------

class TestConcatenateSignalRanges:
    def test_single_range_matches_crop(self):
        sd = _make_signal(duration_sec=10.0, sr=1000.0)
        concat, idx_map = concatenate_signal_ranges(sd, [(2.0, 5.0)])
        cropped, offset = crop_signal_to_range(sd, 2.0, 5.0)
        np.testing.assert_array_equal(concat.signal, cropped.signal)
        assert idx_map[0] == offset

    def test_two_ranges_concatenated_correctly(self):
        sd = _make_signal(duration_sec=10.0, sr=1000.0)
        # Keep [1.0, 3.0) and [7.0, 9.0)
        concat, idx_map = concatenate_signal_ranges(sd, [(1.0, 3.0), (7.0, 9.0)])
        assert len(concat.signal) == 4000  # 2000 + 2000
        # First segment: samples 1000-2999
        np.testing.assert_array_equal(concat.signal[:2000], sd.signal[1000:3000])
        # Second segment: samples 7000-8999
        np.testing.assert_array_equal(concat.signal[2000:], sd.signal[7000:9000])

    def test_idx_map_values(self):
        sd = _make_signal(duration_sec=10.0, sr=1000.0)
        _, idx_map = concatenate_signal_ranges(sd, [(1.0, 2.0), (5.0, 6.0)])
        assert idx_map[0] == 1000   # first sample of first range
        assert idx_map[999] == 1999  # last sample of first range
        assert idx_map[1000] == 5000  # first sample of second range

    def test_time_array_in_original_file_coords(self):
        sd = _make_signal(duration_sec=10.0, sr=1000.0)
        concat, idx_map = concatenate_signal_ranges(sd, [(2.0, 3.0), (7.0, 8.0)])
        assert concat.time[0] == pytest.approx(2.0, abs=1e-6)
        assert concat.time[1000] == pytest.approx(7.0, abs=1e-6)

    def test_boundaries_stored_in_metadata(self):
        sd = _make_signal(duration_sec=10.0, sr=1000.0)
        concat, _ = concatenate_signal_ranges(sd, [(1.0, 2.0), (5.0, 6.0), (8.0, 9.0)])
        bounds = concat.metadata["_range_boundaries"]
        assert bounds == [1000, 2000]  # after 1st and 2nd ranges

    def test_raises_on_empty_keep_ranges(self):
        sd = _make_signal()
        with pytest.raises(ValueError):
            concatenate_signal_ranges(sd, [])


# ---------------------------------------------------------------------------
# Stage 2: restore_event_timestamps_mapped
# ---------------------------------------------------------------------------

class TestRestoreEventTimestampsMapped:
    def test_single_range_matches_offset_restore(self):
        """For a single range, mapped restore should match simple offset restore."""
        sr = 1000.0
        sd = _make_signal(duration_sec=5.0, sr=sr)
        _, idx_map = concatenate_signal_ranges(sd, [(1.0, 3.0)])

        ev_mapped = _make_event(start_idx=500, end_idx=600, sr=sr)
        ev_offset = _make_event(start_idx=500, end_idx=600, sr=sr)

        restore_event_timestamps_mapped([ev_mapped], idx_map, sr)
        restore_event_timestamps([ev_offset], sample_offset=1000, sampling_rate=sr)

        assert ev_mapped.start_idx == ev_offset.start_idx
        assert ev_mapped.start_time == pytest.approx(ev_offset.start_time)

    def test_two_ranges_correct_mapping(self):
        sr = 1000.0
        sd = _make_signal(duration_sec=10.0, sr=sr)
        _, idx_map = concatenate_signal_ranges(sd, [(1.0, 3.0), (7.0, 9.0)])
        # First range: concat idx 0-1999 → original 1000-2999
        # Second range: concat idx 2000-3999 → original 7000-8999
        # Event at concatenated idx 2500 (500 into second range)
        ev = _make_event(start_idx=2500, end_idx=2600, sr=sr)
        restore_event_timestamps_mapped([ev], idx_map, sr)
        assert ev.start_idx == 7500   # 7000 + 500
        assert ev.start_time == pytest.approx(7.5)


# ---------------------------------------------------------------------------
# Stage 2: insert_nan_at_gaps
# ---------------------------------------------------------------------------

class TestInsertNanAtGaps:
    def test_no_boundaries_returns_unchanged(self):
        arr = np.arange(10, dtype=np.float64)
        result = insert_nan_at_gaps(arr, [])
        np.testing.assert_array_equal(result, arr)

    def test_single_boundary(self):
        arr = np.arange(6, dtype=np.float64)
        result = insert_nan_at_gaps(arr, [3])
        assert len(result) == 7
        assert np.isnan(result[3])
        np.testing.assert_array_equal(result[:3], arr[:3])
        np.testing.assert_array_equal(result[4:], arr[3:])

    def test_multiple_boundaries(self):
        arr = np.arange(9, dtype=np.float64)
        result = insert_nan_at_gaps(arr, [3, 6])
        assert len(result) == 11
        assert np.isnan(result[3])
        assert np.isnan(result[7])  # shifted by one due to first NaN


# ---------------------------------------------------------------------------
# Stage 2: exclude_ranges integration via pipeline
# ---------------------------------------------------------------------------

class TestExcludeRangesIntegration:
    def test_artifact_excluded_from_gmm(self):
        """Artifact samples must not influence the GMM threshold when excluded."""
        from nano_ext.models import DetectionConfig
        from nano_ext.pipeline import run_pipeline

        rng = np.random.default_rng(7)
        sr = 5_000.0
        duration = 6.0
        n = int(duration * sr)
        signal = np.zeros(n, dtype=np.float32)
        signal += rng.normal(0, 1, n).astype(np.float32)
        # Inject a real event at t=2.5s
        signal[int(2.5 * sr):int(2.52 * sr)] = -100.0
        # Inject a large artifact at t=1.0–1.5s (zapping simulation)
        signal[int(1.0 * sr):int(1.5 * sr)] = 5000.0

        sd = SignalData(signal=signal, sampling_rate=sr)
        config = DetectionConfig(
            apply_filter=False,
            min_event_duration_sec=0.005,
            gmm_max_samples=20_000,
        )

        result = run_pipeline(
            sd, config=config, analyze_sublevel=False,
            exclude_ranges=[(1.0, 1.5)],
        )
        # Events must not be inside the excluded artifact region
        for ev in result.events:
            assert not (ev.start_time >= 1.0 and ev.end_time <= 1.5), (
                f"Event at t={ev.start_time:.3f} is inside the excluded artifact region"
            )

    def test_cannot_combine_analysis_range_and_exclude_ranges(self):
        from nano_ext.models import DetectionConfig
        from nano_ext.pipeline import run_pipeline
        sd = _make_signal()
        with pytest.raises(ValueError, match="mutually exclusive"):
            run_pipeline(sd, analyze_sublevel=False,
                         analysis_range=(1.0, 5.0),
                         exclude_ranges=[(2.0, 3.0)])
