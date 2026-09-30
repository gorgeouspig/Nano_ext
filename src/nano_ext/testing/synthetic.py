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
        Ground-truth event specifications (times before the low-pass filter).
    unfiltered_clean_signal : np.ndarray, optional
        The noise-free signal before the simulated low-pass filter (only set
        when a filter is applied; ``clean_signal`` is then the filtered one).
    filter_delay_sec : float
        Low-frequency group delay of the simulated filter: events appear this
        much later in ``signal_data`` than their ``start_time``.
    """

    signal_data: SignalData
    clean_signal: np.ndarray
    baseline_trace: np.ndarray
    events: list[SyntheticEventSpec]
    unfiltered_clean_signal: Optional[np.ndarray] = None
    filter_delay_sec: float = 0.0


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
    filter_cutoff: Optional[float] = None,
    filter_order: int = 4,
    filter_noise: bool = False,
    oversample: int = 4,
    hum_frequency: float = 50.0,
    hum_amplitude: float = 0.0,
    hum_harmonics: int = 1,
    hf_noise_std: float = 0.0,
    hf_noise_exponent: float = 1.0,
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
    filter_cutoff : float, optional
        −3 dB cutoff (Hz) of a simulated analog Bessel low-pass filter, as in
        the amplifier. The noise-free signal is built on a grid ``oversample``
        times finer than ``sampling_rate``, filtered causally and then
        sampled, so steps get the finite rise time (and group delay) of real
        recordings. ``None`` (default) keeps ideal steps.
    filter_order : int
        Number of poles of the Bessel filter (4 and 8 are common).
    filter_noise : bool
        Also pass the white, pink and high-frequency noise through the filter
        (their ``*_std`` then refer to the noise before filtering). By default
        the noise is added after the filter, so the ``*_std`` values are the
        noise levels in the output.
    oversample : int
        Oversampling factor used to simulate the analog filter.
    hum_frequency, hum_amplitude, hum_harmonics : float, float, int
        Mains pickup: ``hum_harmonics`` sinusoids at k × ``hum_frequency``
        with amplitude ``hum_amplitude / k`` (pA) and random phases. Off by
        default.
    hf_noise_std : float
        RMS (pA) of an extra noise component whose PSD rises as
        f^``hf_noise_exponent`` (1: dielectric, 2: capacitive / input
        voltage noise). Off by default.
    hf_noise_exponent : float
        Spectral slope of that component.

    New options draw their random numbers after the existing ones, so a
    given ``seed`` reproduces earlier signals exactly when they are off.

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

    # --- Simulated analog low-pass filter (amplifier) ---
    unfiltered = None
    delay = 0.0
    sos = None
    if filter_cutoff is not None:
        unfiltered = clean_signal
        clean_signal, sos, delay = _analog_bessel_clean(
            events, drift, baseline_current, n_samples, sampling_rate,
            filter_cutoff, filter_order, max(1, int(oversample)),
        )

    # --- Add noise ---
    white_noise = white_noise_std * rng.standard_normal(n_samples)
    pink_noise = pink_noise_std * _generate_pink_noise(n_samples, rng)
    noise = white_noise + pink_noise
    if hf_noise_std > 0:
        noise = noise + hf_noise_std * _generate_power_law_noise(n_samples, hf_noise_exponent, rng)
    if filter_noise and filter_cutoff is not None:
        noise = _filter_at_rate(noise, sampling_rate, filter_cutoff, filter_order)
    if hum_amplitude > 0:
        t = np.arange(n_samples) / sampling_rate
        for k in range(1, max(1, int(hum_harmonics)) + 1):
            phase = rng.uniform(0, 2 * np.pi)
            noise = noise + (hum_amplitude / k) * np.sin(2 * np.pi * k * hum_frequency * t + phase)
    noisy_signal = clean_signal + noise

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
            **({"filter_cutoff": filter_cutoff, "filter_order": filter_order,
                "filter_delay_sec": delay} if filter_cutoff is not None else {}),
            **({"hum_frequency": hum_frequency, "hum_amplitude": hum_amplitude,
                "hum_harmonics": hum_harmonics} if hum_amplitude > 0 else {}),
            **({"hf_noise_std": hf_noise_std, "hf_noise_exponent": hf_noise_exponent}
               if hf_noise_std > 0 else {}),
        },
    )

    return SyntheticSignalResult(
        signal_data=signal_data,
        clean_signal=clean_signal,
        baseline_trace=baseline_trace,
        events=events,
        unfiltered_clean_signal=unfiltered,
        filter_delay_sec=delay,
    )


def _bessel_sos(cutoff: float, order: int, fs: float):
    from scipy.signal import bessel

    if not 0 < cutoff < fs / 2:
        raise ValueError(f"filter_cutoff must be between 0 and {fs / 2:g} Hz (Nyquist of the simulation grid)")
    return bessel(order, cutoff, btype="low", norm="mag", fs=fs, output="sos")


def _dc_group_delay(sos, fs: float) -> float:
    from scipy.signal import group_delay, sos2tf

    b, a = sos2tf(sos)
    _, gd = group_delay((b, a), w=[1e-6], fs=fs)
    return float(gd[0]) / fs


def _analog_bessel_clean(events, drift, baseline_current, n_samples, sampling_rate,
                         cutoff, order, oversample):
    """Noise-free trace through a Bessel low-pass, simulated on a finer grid."""
    from scipy.signal import sosfilt, sosfilt_zi

    fs_hi = sampling_rate * oversample
    n_hi = n_samples * oversample
    # drift is smooth: linear interpolation onto the fine grid is exact enough
    t_lo = np.arange(n_samples)
    t_hi = np.arange(n_hi) / oversample
    hi = baseline_current + np.interp(t_hi, t_lo, drift)
    drift_hi = hi - baseline_current
    for event in events:
        current_time = event.start_time
        for level_duration, level_current in event.levels:
            a = max(0, min(int(round(current_time * fs_hi)), n_hi))
            b = max(0, min(int(round((current_time + level_duration) * fs_hi)), n_hi))
            hi[a:b] = level_current + drift_hi[a:b]
            current_time += level_duration
    sos = _bessel_sos(cutoff, order, fs_hi)
    y, _ = sosfilt(sos, hi, zi=sosfilt_zi(sos) * hi[0])
    return y[::oversample].copy(), sos, _dc_group_delay(sos, fs_hi)


def _filter_at_rate(x: np.ndarray, fs: float, cutoff: float, order: int) -> np.ndarray:
    from scipy.signal import sosfilt, sosfilt_zi

    sos = _bessel_sos(cutoff, order, fs)
    y, _ = sosfilt(sos, x, zi=sosfilt_zi(sos) * x[0])
    return y


def _generate_power_law_noise(n_samples: int, exponent: float, rng: np.random.Generator) -> np.ndarray:
    """Unit-variance Gaussian noise with PSD ∝ f^exponent (DC removed)."""
    white = rng.standard_normal(n_samples)
    spec = np.fft.rfft(white)
    f = np.fft.rfftfreq(n_samples)
    shape = np.zeros_like(f)
    shape[1:] = f[1:] ** (exponent / 2.0)
    x = np.fft.irfft(spec * shape, n=n_samples)
    std = x.std()
    return x / std if std > 0 else x


def truth_table(result: SyntheticSignalResult) -> list[dict]:
    """Ground truth per event, in the time frame of ``result.signal_data``.

    Returns one dict per event with ``start``/``end`` (s, shifted by the
    filter group delay), ``dwell`` (s), ``n_levels``, ``depth`` (duration-
    weighted mean blockade relative to the local baseline, pA),
    ``level_bounds`` (s, boundaries between sub-levels) and
    ``level_currents`` (pA).
    """
    sd = result.signal_data
    delay = result.filter_delay_sec
    rows = []
    for ev in result.events:
        t, bounds, currents, weighted = ev.start_time, [], [], 0.0
        i = min(max(int(ev.start_time * sd.sampling_rate), 0), len(result.baseline_trace) - 1)
        base = float(result.baseline_trace[i])
        for k, (dur, cur) in enumerate(ev.levels):
            if k:
                bounds.append(t + delay)
            currents.append(float(cur))
            weighted += dur * (base - cur)
            t += dur
        rows.append({
            "start": ev.start_time + delay,
            "end": ev.end_time + delay,
            "dwell": ev.duration,
            "n_levels": ev.n_levels,
            "depth": weighted / ev.duration if ev.duration > 0 else 0.0,
            "baseline": base,
            "level_bounds": bounds,
            "level_currents": currents,
        })
    return rows


def poisson_event_train(
    duration_sec: float,
    rate_hz: float,
    make_levels,
    rng: np.random.Generator,
    min_gap_sec: float = 0.0,
    margin_sec: float = 0.02,
) -> list[SyntheticEventSpec]:
    """Events arriving as a Poisson process.

    Parameters
    ----------
    duration_sec : float
        Length of the recording.
    rate_hz : float
        Mean arrival rate (events per second of open pore).
    make_levels : callable
        ``make_levels(rng) -> list[(duration_sec, current)]`` for one event,
        e.g. from :func:`level_sampler`.
    rng : numpy.random.Generator
    min_gap_sec : float
        Minimum open-pore time after each event (events never overlap).
    margin_sec : float
        Keep this much open pore at both ends of the recording.
    """
    events, t = [], margin_sec
    while True:
        t += rng.exponential(1.0 / rate_hz)
        levels = make_levels(rng)
        dur = sum(d for d, _ in levels)
        if t + dur > duration_sec - margin_sec:
            return events
        events.append(SyntheticEventSpec(t, levels))
        t += dur + min_gap_sec


def level_sampler(
    baseline_current: float,
    depth,
    dwell_mean_sec: float,
    dwell: str = "exponential",
    dwell_sigma: float = 0.5,
    dwell_min_sec: float = 0.0,
    n_levels=1,
    level_depths=None,
):
    """Build a ``make_levels`` callable for :func:`poisson_event_train`.

    Parameters
    ----------
    baseline_current : float
        Open-pore current (pA).
    depth : float or (low, high)
        Blockade depth (pA) of single-level events, fixed or uniform in a range.
    dwell_mean_sec : float
        Mean total event duration.
    dwell : {"exponential", "lognormal", "fixed"}
        Dwell-time distribution (lognormal uses ``dwell_sigma`` in log space
        with the given mean).
    dwell_min_sec : float
        Added to every dwell time (shortest possible event).
    n_levels : int or sequence of int
        Number of sub-levels, fixed or drawn uniformly from the sequence.
    level_depths : sequence of float, optional
        Depths (pA) of the sub-levels in order; the i-th level of an event
        uses ``level_depths[i % len]``. Defaults to ``depth`` for level 0 and
        alternately ±30 % of it for the following ones.
    """

    def draw_depth(rng):
        if isinstance(depth, (tuple, list)):
            return float(rng.uniform(depth[0], depth[1]))
        return float(depth)

    def draw_dwell(rng):
        if dwell == "exponential":
            d = rng.exponential(dwell_mean_sec)
        elif dwell == "lognormal":
            mu = np.log(dwell_mean_sec) - dwell_sigma ** 2 / 2
            d = rng.lognormal(mu, dwell_sigma)
        elif dwell == "fixed":
            d = dwell_mean_sec
        else:
            raise ValueError(f"unknown dwell distribution {dwell!r}")
        return float(d) + dwell_min_sec

    def make(rng):
        k = int(rng.choice(n_levels)) if isinstance(n_levels, (list, tuple)) else int(n_levels)
        total = draw_dwell(rng)
        d0 = draw_depth(rng)
        if level_depths is not None:
            depths = [level_depths[i % len(level_depths)] for i in range(k)]
        else:
            depths = [d0 * (1.0 if i == 0 else (0.7 if i % 2 else 1.3)) for i in range(k)]
        parts = rng.dirichlet(np.full(k, 5.0)) * total if k > 1 else np.array([total])
        return [(float(p), baseline_current - float(dd)) for p, dd in zip(parts, depths)]

    return make


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
