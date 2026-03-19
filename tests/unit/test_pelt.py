import numpy as np
import pytest
from nano_ext import pelt


def test_pelt_no_change():
    """Test PELT on a signal with no change point."""
    # Constant signal
    signal = np.ones(1000) * 5.0
    change_points = pelt(signal.tolist(), penalty_factor=4.0, min_segment_samples=10)
    # Should detect no change points (or possibly at boundaries, but we don't record 0 or n)
    assert change_points == []


def test_pelt_single_change():
    """Test PELT on a signal with one change point."""
    # Two segments: first half zeros, second half ones
    signal = np.concatenate([np.zeros(500), np.ones(500)])
    change_points = pelt(signal.tolist(), penalty_factor=4.0, min_segment_samples=10)
    # Should detect a change point near 500
    assert len(change_points) == 1
    # Allow some tolerance due to the algorithm
    assert 400 <= change_points[0] <= 600


def test_pelt_multiple_changes():
    """Test PELT on a signal with multiple change points."""
    # Three segments: zeros, ones, zeros
    signal = np.concatenate([np.zeros(300), np.ones(400), np.zeros(300)])
    change_points = pelt(signal.tolist(), penalty_factor=4.0, min_segment_samples=10)
    # Should detect two change points
    assert len(change_points) == 2
    # First change point near 300
    assert 200 <= change_points[0] <= 400
    # Second change point near 700 (300+400)
    assert 600 <= change_points[1] <= 800


def test_pelt_min_segment_samples():
    """Test PELT respects min_segment_samples."""
    # Test 1: Signal too short to allow any valid segmentation
    # Signal length = 15, min_segment_samples = 10
    # Any change point would create at least one segment with < 10 samples
    signal = np.concatenate([np.zeros(5), np.ones(10)])  # length 15
    change_points = pelt(signal.tolist(), penalty_factor=4.0, min_segment_samples=10)
    # Should detect no change points because any segmentation would violate min_segment_samples
    assert change_points == []

    # Test 2: Now make signal long enough that we can detect a change point
    # Signal length = 25, min_segment_samples = 10
    # We can now have a change point at index 10, giving segments of size 10 and 15
    signal = np.concatenate([np.zeros(10), np.ones(15)])  # length 25
    change_points = pelt(signal.tolist(), penalty_factor=4.0, min_segment_samples=10)
    # Should detect a change point near 10
    assert len(change_points) == 1
    assert 5 <= change_points[0] <= 15  # Allow some tolerance


def test_pelt_penalty_factor():
    """Test that penalty factor affects the number of change points detected."""
    # Signal with multiple small fluctuations
    signal = np.sin(np.linspace(0, 20*np.pi, 1000)) + 0.1 * np.random.randn(1000)
    # With low penalty, we expect more change points
    change_points_low = pelt(signal.tolist(), penalty_factor=1.0, min_segment_samples=10)
    # With high penalty, we expect fewer change points
    change_points_high = pelt(signal.tolist(), penalty_factor=10.0, min_segment_samples=10)
    # The high penalty should result in fewer or equal change points
    assert len(change_points_high) <= len(change_points_low)