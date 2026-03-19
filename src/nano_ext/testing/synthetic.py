"""Synthetic nanopore signal generator for testing and development.

Generates realistic synthetic current traces with known event positions,
depths, and sub-level structures for algorithm validation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from nano_ext.models import SignalData


@dataclass
class SyntheticEventSpec:
    """Specification for a single synthetic event.

    Attributes
    ----------
    start_time : float
        Event start time in seconds.
    levels : list[tuple[float, float]]
        List of (duration_sec, current_level) pairs defining sub-levels.
        For a single-level event, this is a single-element list.
        current_level is the absolute current during that sub-level.
    """

    start_time: float
    levels: list[tuple[float, float]]

    @property
    def end_time(self) -> float:
        return self.start_time + sum(dur for dur, _ in self.levels)

    @property
    def duration(self) -> float:
        return sum(dur for dur, _ in self.levels)

    @property
    def n_levels(self) -> int:
        return len(self.levels)


@dataclass
class SyntheticSignalResult:
    """Result of synthetic signal generation, including ground truth.

    Attributes
    ----------
    signal_data : SignalData
        The generated signal with noise.
    clean_signal : np.ndarray
        The noise-free signal (for visualization/debugging).
    baseline_trace : np.ndarray
        The true baseline (including drift) without events.
    events : list[SyntheticEventSpec]
        Ground-truth event specifications.
    """

    signal_data: SignalData
    clean_signal: np.ndarray
    baseline_trace: np.ndarray
    events: list[SyntheticEventSpec]


def _generate_pink_noise(n_samples: int, rng: np.random.Generator) -> np.ndarray:
    """Generate 1/f (pink) noise using FFT method.

    Parameters
    ----------
    n_samples : int
        Number of samples to generate.
    rng : np.random.Generator
        Random number generator.

    Returns
    -------
    np.ndarray
        Pink noise signal with unit variance.
    """
    white = rng.standard_normal(n_samples)
    fft = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n_samples)

    # Avoid division by zero at DC
    freqs[0] = 1.0
    # 1/f spectrum: amplitude ~ 1/sqrt(f)
    fft *= 1.0 / np.sqrt(freqs)
    # Zero DC component
    fft[0] = 0.0

    pink = np.fft.irfft(fft, n=n_samples)
    # Normalize to unit variance
    std = pink.std()
    if std > 0:
        pink /= std
    return pink


def _generate_drift(
    n_samples: int,
    sampling_rate: float,
    drift_rate: float = 0.0,
    drift_type: str = "linear",
    drift_coefficients: Optional[list[float]] = None,
) -> np.ndarray:
    """Generate a baseline drift signal.

    Parameters
    ----------
    n_samples : int
        Number of samples.
    sampling_rate : float
        Sampling rate in Hz.
    drift_rate : float
        For linear drift: slope in current_units/second.
    drift_type : str
        "linear", "polynomial", or "custom".
    drift_coefficients : list[float], optional
        Polynomial coefficients for "polynomial" or "custom" type.
        Coefficients are in descending order: [a_n, ..., a_1, a_0].

    Returns
    -------
    np.ndarray
        Drift signal.
    """
    t = np.arange(n_samples) / sampling_rate

    if drift_type == "linear":
        return drift_rate * t

    elif drift_type == "polynomial":
        if drift_coefficients is None:
            raise ValueError("drift_coefficients required for polynomial drift")
        return np.polyval(drift_coefficients, t)

    elif drift_type == "custom":
        if drift_coefficients is None:
            raise ValueError("drift_coefficients required for custom drift")
        return np.polyval(drift_coefficients, t)

    else:
        raise ValueError(f"Unknown drift_type: {drift_type}")


def generate_synthetic_signal(
    duration_sec: float = 10.0,
    sampling_rate: float = 100_000.0,
    baseline_current: float = 200.0,
    white_noise_std: float = 5.0,
    pink_noise_std: float = 2.0,
    events: Optional[list[SyntheticEventSpec]] = None,
    drift_rate: float = 0.0,
    drift_type: str = "linear",
    drift_coefficients: Optional[list[float]] = None,
    seed: Optional[int] = None,
) -> SyntheticSignalResult:
    """Generate a synthetic nanopore current trace with known events.

    Parameters
    ----------
    duration_sec : float
        Total duration of the signal in seconds.
    sampling_rate : float
        Sampling rate in Hz.
    baseline_current : float
        Open-channel (baseline) current in pA.
    white_noise_std : float
        Standard deviation of white Gaussian noise in pA.
    pink_noise_std : float
        Standard deviation of 1/f (pink) noise in pA.
    events : list[SyntheticEventSpec], optional
        List of event specifications. If None, a default set is generated.
    drift_rate : float
        Baseline drift rate in pA/second (for linear drift).
    drift_type : str
        Type of drift: "linear" or "polynomial".
    drift_coefficients : list[float], optional
        Polynomial coefficients for drift (used when drift_type="polynomial").
    seed : int, optional
        Random seed for reproducibility.

    Returns
    -------
    SyntheticSignalResult
        Generated signal with ground truth information.
    """
    rng = np.random.default_rng(seed)
    n_samples = int(duration_sec * sampling_rate)

    # --- Baseline + drift ---
    drift = _generate_drift(
        n_samples, sampling_rate, drift_rate, drift_type, drift_coefficients
    )
    baseline_trace = baseline_current + drift

    # --- Clean signal (baseline without noise) ---
    clean_signal = baseline_trace.copy()

    # --- Generate default events if not provided ---
    if events is None:
        events = _generate_default_events(
            duration_sec, baseline_current, rng
        )

    # --- Inject events into clean signal ---
    for event in events:
        sample_offset = 0
        for level_duration, level_current in event.levels:
            start_idx = int(
                (event.start_time + sample_offset / sampling_rate * sampling_rate)
                * 1
            )
            # Recompute properly
            level_start_time = event.start_time + sample_offset / sampling_rate
            # Actually, let's track properly with sample indices
            pass

        # Proper approach: iterate over sub-levels with sample-level precision
        current_time = event.start_time
        for level_duration, level_current in event.levels:
            start_idx = int(current_time * sampling_rate)
            end_idx = int((current_time + level_duration) * sampling_rate)
            start_idx = max(0, min(start_idx, n_samples))
            end_idx = max(0, min(end_idx, n_samples))
            clean_signal[start_idx:end_idx] = level_current + drift[start_idx:end_idx]
            current_time += level_duration

    # --- Add noise ---
    white_noise = white_noise_std * rng.standard_normal(n_samples)
    pink_noise = pink_noise_std * _generate_pink_noise(n_samples, rng)
    noisy_signal = clean_signal + white_noise + pink_noise

    signal_data = SignalData(
        signal=noisy_signal,
        sampling_rate=sampling_rate,
        units="pA",
        metadata={
            "type": "synthetic",
            "baseline_current": baseline_current,
            "white_noise_std": white_noise_std,
            "pink_noise_std": pink_noise_std,
            "n_events": len(events),
        },
    )

    return SyntheticSignalResult(
        signal_data=signal_data,
        clean_signal=clean_signal,
        baseline_trace=baseline_trace,
        events=events,
    )


def _generate_default_events(
    duration_sec: float,
    baseline_current: float,
    rng: np.random.Generator,
) -> list[SyntheticEventSpec]:
    """Generate a default set of diverse synthetic events.

    Creates a mix of:
    - Single-level blockades (various depths and durations)
    - Multi-level (stepwise) events
    - Short spike-like events

    Parameters
    ----------
    duration_sec : float
        Total signal duration in seconds.
    baseline_current : float
        Baseline (open-channel) current.
    rng : np.random.Generator
        Random number generator.

    Returns
    -------
    list[SyntheticEventSpec]
        Generated event specifications.
    """
    events: list[SyntheticEventSpec] = []

    # Reserve margins at start and end
    margin = 0.5  # seconds
    available_start = margin
    available_end = duration_sec - margin

    # --- Single-level blockades ---
    # Shallow blockade
    events.append(
        SyntheticEventSpec(
            start_time=1.0,
            levels=[(0.005, baseline_current * 0.7)],  # 5ms, 30% blockade
        )
    )
    # Medium blockade
    events.append(
        SyntheticEventSpec(
            start_time=2.0,
            levels=[(0.010, baseline_current * 0.5)],  # 10ms, 50% blockade
        )
    )
    # Deep blockade
    events.append(
        SyntheticEventSpec(
            start_time=3.0,
            levels=[(0.008, baseline_current * 0.2)],  # 8ms, 80% blockade
        )
    )
    # Long blockade
    events.append(
        SyntheticEventSpec(
            start_time=4.0,
            levels=[(0.050, baseline_current * 0.6)],  # 50ms, 40% blockade
        )
    )

    # --- Multi-level (stepwise) events ---
    # 2-level event (AB type)
    events.append(
        SyntheticEventSpec(
            start_time=5.5,
            levels=[
                (0.008, baseline_current * 0.6),  # Level 1: 40% blockade
                (0.012, baseline_current * 0.3),  # Level 2: 70% blockade
            ],
        )
    )
    # 3-level event (ABC type)
    events.append(
        SyntheticEventSpec(
            start_time=7.0,
            levels=[
                (0.005, baseline_current * 0.7),  # Level 1: 30% blockade
                (0.010, baseline_current * 0.4),  # Level 2: 60% blockade
                (0.005, baseline_current * 0.7),  # Back to Level 1
            ],
        )
    )
    # Complex multi-level
    events.append(
        SyntheticEventSpec(
            start_time=8.5,
            levels=[
                (0.004, baseline_current * 0.6),
                (0.006, baseline_current * 0.3),
                (0.003, baseline_current * 0.5),
                (0.007, baseline_current * 0.2),
            ],
        )
    )

    # --- Short spike ---
    events.append(
        SyntheticEventSpec(
            start_time=9.2,
            levels=[(0.0005, baseline_current * 0.1)],  # 0.5ms spike
        )
    )

    return events


def generate_scenario(
    scenario: str,
    sampling_rate: float = 100_000.0,
    seed: Optional[int] = 42,
) -> SyntheticSignalResult:
    """Generate a predefined test scenario.

    Parameters
    ----------
    scenario : str
        Scenario name. One of:
        - "basic": Simple single-level blockades
        - "variable_depth": Events with different depths
        - "multilevel": Multi-level / stepwise events
        - "drift": Signal with baseline drift
        - "high_noise": Low S/N ratio
        - "dense": Closely spaced events
        - "short_events": Very short duration events
        - "all": Comprehensive scenario with all features
    sampling_rate : float
        Sampling rate in Hz.
    seed : int, optional
        Random seed.

    Returns
    -------
    SyntheticSignalResult
        Generated signal with ground truth.
    """
    baseline = 200.0  # pA

    if scenario == "basic":
        events = [
            SyntheticEventSpec(1.0, [(0.010, baseline * 0.5)]),
            SyntheticEventSpec(3.0, [(0.010, baseline * 0.5)]),
            SyntheticEventSpec(5.0, [(0.010, baseline * 0.5)]),
            SyntheticEventSpec(7.0, [(0.010, baseline * 0.5)]),
        ]
        return generate_synthetic_signal(
            duration_sec=10.0,
            sampling_rate=sampling_rate,
            baseline_current=baseline,
            events=events,
            seed=seed,
        )

    elif scenario == "variable_depth":
        events = [
            SyntheticEventSpec(1.0, [(0.010, baseline * 0.9)]),  # 10%
            SyntheticEventSpec(2.5, [(0.010, baseline * 0.7)]),  # 30%
            SyntheticEventSpec(4.0, [(0.010, baseline * 0.5)]),  # 50%
            SyntheticEventSpec(5.5, [(0.010, baseline * 0.3)]),  # 70%
            SyntheticEventSpec(7.0, [(0.010, baseline * 0.1)]),  # 90%
        ]
        return generate_synthetic_signal(
            duration_sec=10.0,
            sampling_rate=sampling_rate,
            baseline_current=baseline,
            events=events,
            seed=seed,
        )

    elif scenario == "multilevel":
        events = [
            # 2-level
            SyntheticEventSpec(
                1.0,
                [(0.008, baseline * 0.6), (0.012, baseline * 0.3)],
            ),
            # 3-level symmetric
            SyntheticEventSpec(
                3.0,
                [
                    (0.005, baseline * 0.7),
                    (0.010, baseline * 0.4),
                    (0.005, baseline * 0.7),
                ],
            ),
            # 4-level
            SyntheticEventSpec(
                5.5,
                [
                    (0.004, baseline * 0.6),
                    (0.006, baseline * 0.3),
                    (0.003, baseline * 0.5),
                    (0.007, baseline * 0.2),
                ],
            ),
        ]
        return generate_synthetic_signal(
            duration_sec=8.0,
            sampling_rate=sampling_rate,
            baseline_current=baseline,
            events=events,
            seed=seed,
        )

    elif scenario == "drift":
        events = [
            SyntheticEventSpec(1.0, [(0.010, baseline * 0.5)]),
            SyntheticEventSpec(3.0, [(0.010, baseline * 0.5)]),
            SyntheticEventSpec(5.0, [(0.010, baseline * 0.5)]),
            SyntheticEventSpec(8.0, [(0.010, baseline * 0.5)]),
            SyntheticEventSpec(12.0, [(0.010, baseline * 0.5)]),
            SyntheticEventSpec(18.0, [(0.010, baseline * 0.5)]),
        ]
        return generate_synthetic_signal(
            duration_sec=20.0,
            sampling_rate=sampling_rate,
            baseline_current=baseline,
            events=events,
            drift_rate=2.0,  # 2 pA/sec upward drift
            seed=seed,
        )

    elif scenario == "high_noise":
        events = [
            SyntheticEventSpec(1.0, [(0.010, baseline * 0.5)]),
            SyntheticEventSpec(3.0, [(0.010, baseline * 0.3)]),
            SyntheticEventSpec(5.0, [(0.010, baseline * 0.5)]),
        ]
        return generate_synthetic_signal(
            duration_sec=8.0,
            sampling_rate=sampling_rate,
            baseline_current=baseline,
            white_noise_std=15.0,  # 3x normal
            pink_noise_std=8.0,  # 4x normal
            events=events,
            seed=seed,
        )

    elif scenario == "dense":
        # Events every 200ms with short gaps
        events = [
            SyntheticEventSpec(
                0.5 + i * 0.2,
                [(0.010, baseline * 0.5)],
            )
            for i in range(30)
        ]
        return generate_synthetic_signal(
            duration_sec=8.0,
            sampling_rate=sampling_rate,
            baseline_current=baseline,
            events=events,
            seed=seed,
        )

    elif scenario == "short_events":
        events = [
            SyntheticEventSpec(1.0, [(0.0002, baseline * 0.3)]),  # 0.2ms
            SyntheticEventSpec(2.0, [(0.0005, baseline * 0.3)]),  # 0.5ms
            SyntheticEventSpec(3.0, [(0.001, baseline * 0.3)]),  # 1ms
            SyntheticEventSpec(4.0, [(0.002, baseline * 0.3)]),  # 2ms
            SyntheticEventSpec(5.0, [(0.005, baseline * 0.3)]),  # 5ms
        ]
        return generate_synthetic_signal(
            duration_sec=8.0,
            sampling_rate=sampling_rate,
            baseline_current=baseline,
            events=events,
            seed=seed,
        )

    elif scenario == "all":
        return generate_synthetic_signal(
            duration_sec=10.0,
            sampling_rate=sampling_rate,
            baseline_current=baseline,
            drift_rate=1.0,
            seed=seed,
        )

    else:
        raise ValueError(
            f"Unknown scenario: {scenario}. "
            "Available: basic, variable_depth, multilevel, drift, "
            "high_noise, dense, short_events, all"
        )
