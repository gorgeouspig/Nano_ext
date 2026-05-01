"""Benchmark: Rust extension vs. pure-Python fallback implementations.

Measures wall-clock time for the two Rust-accelerated kernels:

  1. local_baseline_percentile — sliding-window percentile baseline
  2. pelt                      — change-point detection (O(n²) PELT)

Strategy
--------
- Speedup ratio is measured at "comparison sizes" where the pure-Python
  implementation completes in a few seconds.
- Rust throughput is measured independently at "scale sizes" to show
  how the implementation handles production-scale signals.

Usage
-----
    conda activate nano_ext
    python scripts/benchmark_rust_vs_python.py
"""

from __future__ import annotations

import math
import time
from typing import Optional

import numpy as np
from scipy.ndimage import uniform_filter1d

# ---------------------------------------------------------------------------
# Pure-Python reference implementations (mirror Rust kernels)
# ---------------------------------------------------------------------------

def _local_baseline_percentile_python(
    signal: list[float],
    mask: list[bool],
    window_samples: int,
    percentile: float,
) -> list[float]:
    n = len(signal)
    half_win = window_samples // 2
    masked = [v if m else float("nan") for v, m in zip(signal, mask)]
    valid_all = [v for v in masked if not math.isnan(v)]
    fallback = sorted(valid_all)[len(valid_all) // 2] if valid_all else 0.0
    baseline = [0.0] * n
    for center in range(n):
        start = max(0, center - half_win)
        end = min(n, center + half_win)
        window = [v for v in masked[start:end] if not math.isnan(v)]
        if window:
            window.sort()
            idx = round((percentile / 100.0) * (len(window) - 1))
            baseline[center] = window[idx]
        else:
            baseline[center] = fallback
    smooth_win = max(3, window_samples // 10)
    if smooth_win % 2 == 0:
        smooth_win += 1
    return uniform_filter1d(np.array(baseline), size=smooth_win).tolist()


def _pelt_python(
    signal: list[float],
    penalty_factor: float,
    min_segment_samples: int,
) -> list[int]:
    n = len(signal)
    if n < 2 * min_segment_samples:
        return []
    beta = 2.0 * penalty_factor * math.log(n)
    cum = [0.0] * (n + 1)
    cum2 = [0.0] * (n + 1)
    for i, v in enumerate(signal):
        cum[i + 1] = cum[i] + v
        cum2[i + 1] = cum2[i] + v * v

    def cost(s: int, e: int) -> float:
        length = e - s
        if length < min_segment_samples:
            return math.inf
        s_ = cum[e] - cum[s]
        s2 = cum2[e] - cum2[s]
        term = max(s2 - s_ * s_ / length, 1e-20)
        return length * (math.log(term) - math.log(length))

    F = [math.inf] * (n + 1)
    last = [0] * (n + 1)
    F[0] = -beta
    for t in range(1, n + 1):
        best_cost = math.inf
        best_s = 0
        for s in range(t - 1):
            c = F[s] + cost(s, t) + beta
            if c < best_cost:
                best_cost = c
                best_s = s
        F[t] = best_cost
        last[t] = best_s

    cps: list[int] = []
    t = n
    while t > 0:
        s = last[t]
        if s != 0:
            cps.append(s)
        t = s
    cps.reverse()
    return cps


# ---------------------------------------------------------------------------
# Timing helper
# ---------------------------------------------------------------------------

def _timeit(fn, n_repeats: int = 5) -> tuple[float, float]:
    """Return (mean_seconds, std_seconds) across n_repeats calls."""
    times = []
    for _ in range(n_repeats):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    arr = np.array(times)
    return float(arr.mean()), float(arr.std())


def _fmt(seconds: float) -> str:
    if seconds < 1e-3:
        return f"{seconds * 1e6:7.1f} µs"
    if seconds < 1.0:
        return f"{seconds * 1e3:7.1f} ms"
    return f"{seconds:7.3f}  s"


# ---------------------------------------------------------------------------
# Benchmark 1: local_baseline_percentile
# ---------------------------------------------------------------------------

def bench_baseline() -> None:
    from nano_ext import local_baseline_percentile  # Rust

    percentile = 90.0
    rng = np.random.default_rng(42)

    # --- Part A: Rust vs Python at small sizes (window=51) ---
    window_small = 51
    comparison_sizes = [500, 1_000, 2_000, 5_000]

    print("\n" + "=" * 68)
    print("Benchmark 1a: local_baseline_percentile — Rust vs. Python")
    print(f"  window={window_small} samples, percentile={percentile}")
    print("-" * 68)
    print(f"{'Samples':>10}  {'Rust':>12}  {'Python':>12}  {'Speedup':>8}")
    print("-" * 68)

    for n in comparison_sizes:
        signal_np = rng.normal(200.0, 5.0, n)
        mask_np = np.ones(n, dtype=bool)
        for s in range(0, n, max(1, n // 20)):
            mask_np[s : min(n, s + max(1, n // 200))] = False
        sig = signal_np.tolist()
        msk = mask_np.tolist()

        rust_t, _ = _timeit(
            lambda: local_baseline_percentile(sig, msk, window_small, percentile)
        )
        py_t, _ = _timeit(
            lambda: _local_baseline_percentile_python(sig, msk, window_small, percentile)
        )
        print(
            f"{n:>10,}  {_fmt(rust_t):>12}  {_fmt(py_t):>12}  {py_t/rust_t:>7.1f}x"
        )
    print("-" * 68)

    # --- Part B: Rust scaling at production sizes (window=5001) ---
    # The product-based threshold (n*window > 10M) ensures the fast subsampled
    # path is used for all sizes here: 10k*5001 = 50M > 10M.
    window_prod = 5_001
    scale_sizes = [10_000, 50_000, 100_000, 500_000, 1_000_000]

    print(f"\nBenchmark 1b: local_baseline_percentile — Rust scaling")
    print(f"  window={window_prod} samples (50 ms at 100 kHz / 5 ms at 1 MHz)")
    print(f"  (product-based subsampling threshold kicks in for all sizes here)")
    print("-" * 40)
    print(f"{'Samples':>12}  {'Rust (mean)':>14}")
    print("-" * 40)

    for n in scale_sizes:
        signal_np = rng.normal(200.0, 5.0, n)
        mask_np = np.ones(n, dtype=bool)
        for s in range(0, n, max(1, n // 20)):
            mask_np[s : min(n, s + max(1, n // 200))] = False
        sig = signal_np.tolist()
        msk = mask_np.tolist()

        rust_t, _ = _timeit(
            lambda: local_baseline_percentile(sig, msk, window_prod, percentile),
            n_repeats=3,
        )
        print(f"{n:>12,}  {_fmt(rust_t):>14}")
    print("-" * 40)


# ---------------------------------------------------------------------------
# Benchmark 2: pelt
# ---------------------------------------------------------------------------

def bench_pelt() -> None:
    from nano_ext import pelt  # Rust

    penalty_factor = 1.5
    min_seg = 50
    rng = np.random.default_rng(42)

    # --- Part A: Rust vs Python at small sizes (typical event segments) ---
    comparison_sizes = [300, 500, 800, 1_000, 1_500]

    print("\n" + "=" * 68)
    print("Benchmark 2a: pelt — Rust vs. Python")
    print(f"  penalty_factor={penalty_factor}, min_segment_samples={min_seg}")
    print("-" * 68)
    print(f"{'Samples':>10}  {'Rust':>12}  {'Python':>12}  {'Speedup':>8}")
    print("-" * 68)

    for n in comparison_sizes:
        segment = max(1, n // 4)
        levels = [0.0, -50.0, -80.0, -30.0]
        sig = np.concatenate(
            [rng.normal(levels[i % 4], 5.0, segment) for i in range(4)]
        )[:n].tolist()

        rust_t, _ = _timeit(lambda: pelt(sig, penalty_factor, min_seg))
        py_t, _ = _timeit(lambda: _pelt_python(sig, penalty_factor, min_seg))
        print(
            f"{n:>10,}  {_fmt(rust_t):>12}  {_fmt(py_t):>12}  {py_t/rust_t:>7.1f}x"
        )
    print("-" * 68)

    # --- Part B: Rust scaling (larger segments) ---
    scale_sizes = [2_000, 5_000, 10_000, 20_000, 50_000]

    print(f"\nBenchmark 2b: pelt — Rust scaling")
    print("-" * 38)
    print(f"{'Samples':>10}  {'Rust (mean)':>14}")
    print("-" * 38)

    for n in scale_sizes:
        segment = max(1, n // 4)
        sig = np.concatenate(
            [rng.normal(levels[i % 4], 5.0, segment) for i in range(4)]
        )[:n].tolist()

        rust_t, _ = _timeit(
            lambda: pelt(sig, penalty_factor, min_seg), n_repeats=3
        )
        print(f"{n:>10,}  {_fmt(rust_t):>14}")
    print("-" * 38)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Nano_ext — Rust vs. Pure-Python Benchmark")
    print("(times are mean over 5 repeats unless noted; lower is better)")

    bench_baseline()
    bench_pelt()
    print()
