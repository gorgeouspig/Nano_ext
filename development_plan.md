# Nano_ext: Nanopore Signal Event Extraction - Development Plan

This document summarizes the requirements and development roadmap for the `Nano_ext` project, as discussed in the session `ses_30fdafa0dffejYZcR4JGjd7V0s`.

## 0. How to Resume Development

1. **Activate environment**: `conda activate nano_ext`
2. **Confirm tests pass**: `pytest` → should finish in ~10s with all green
3. **Check next task**: Look for the first 🔲 item in Section 4 (Roadmap) — that is what to work on next
4. **Run slow tests before committing**: `pytest -m slow` (takes several minutes; covers GMM and Rust baseline)
5. **Commit**: Stage your changes and commit to `main`

**Current next task → Phase 4 item 3: Automated Parameter Tuning** (see Section 4)

## 1. Project Overview

`Nano_ext` is a Python-based toolkit (with Rust extensions) designed to extract events (blockades) from high-sampling rate (>100 kHz) nanopore current traces. It aims to provide an objective, information-criterion-based approach to thresholding and sub-level (multi-step) event analysis.

### Core Goals
- **Objective Thresholding**: Use Gaussian Mixture Models (GMM) and Bayesian Information Criterion (BIC) to automatically determine the number of current levels and optimal detection thresholds.
- **Drift Handling**: Robustly handle baseline drift (gradual shifts in open-channel current) using iterative local estimation.
- **Sub-level Analysis**: Recursively decompose complex events into multiple sub-steps using change-point detection (Binary Segmentation/PELT) and BIC.
- **Performance**: Use Rust (PyO3/maturin) to accelerate bottleneck computations for large datasets.

## 2. Requirements Summary

| Feature | Specification |
|:---|:---|
| **Language** | Python 3.10+ & Rust (PyO3) |
| **Input** | Axon Binary Format (ABF), Raw Binary Data |
| **Event Direction** | Configurable (Default: Downward/Blockade) |
| **Sampling Rate** | High-speed (100 kHz+) |
| **Output** | CSV/TSV summaries, Visualization plots |
| **Core Algorithm** | GMM + BIC (Thresholding), Binary Segmentation + BIC (Sub-levels) |

## 3. Current Implementation Status

The project is functional end-to-end with both Python and Rust components in place:

- [x] **Data IO**: `io/abf_reader.py` and `io/binary_reader.py` for loading signals.
- [x] **Preprocessing**: `preprocessing/filters.py` (Bessel/Butterworth) and `preprocessing/baseline.py` (iterative drift correction, Rust-accelerated local percentile).
- [x] **Detection Core**: `detection/threshold.py` (GMM+BIC) and `detection/events.py` (threshold-based detection with run-length encoding and gap merging).
- [x] **Sub-level Analysis**: `detection/sublevel.py` and `detection/changepoint.py` (recursive PELT-based segmentation).
- [x] **Pipeline**: `pipeline.py` for end-to-end orchestration (`run_pipeline`, `process_file`).
- [x] **CLI**: Implemented in `nano_ext/cli.py` (Click) with full configuration options.
- [x] **Visualization**: `outputs/visualize.py` for comprehensive result plotting.
- [x] **Export**: `outputs/csv_writer.py` for CSV/TSV output.
- [x] **Rust Extension**: `rust/src/lib.rs` exposes `pelt()` and `local_baseline_percentile()` to Python as `nano_ext._nano_ext` via PyO3 + maturin. Cargo.toml configured.
- [x] **Module Layout Cleanup**: `output` → `outputs` rename and `nano_ext_core` → `nano_ext._nano_ext` migration completed (resolves build conflicts and circular imports).
- [x] **Synthetic Data**: `testing/synthetic.py` for generating ground-truth test data.
- [x] **Unit Tests (initial)**: `tests/unit/test_pelt.py`, `tests/unit/test_sublevel.py` cover the Rust PELT bindings and GMM+BIC sub-level analysis.
- [x] **CI**: `.github/workflows/build_wheels.yml` builds wheels for Linux / macOS / Windows on push to `main` and version tags (`v*`).
- [ ] **Test Coverage Expansion**: IO, preprocessing (filters/baseline), threshold, events, and end-to-end pipeline tests are still missing.
- [ ] **Documentation**: README is updated, but API-level docs and example notebooks remain to be written.
- [ ] **Negative Control Integration**: See Section 6.

## 4. Development Roadmap

### Phase 1: Python MVP & CLI (Completed)
✅ **CLI Implementation**: Created `nano_ext/cli.py` to allow users to run the pipeline on ABF/binary files from the command line.
✅ **Visualization Module**: Created `nano_ext.outputs/visualize.py` using `matplotlib` to plot raw/filtered signals, baseline, thresholds, and detected events.
✅ **Export Module**: Created `nano_ext.outputs/csv_writer.py` to save event statistics to CSV/TSV.
✅ **Baseline Drift Refinement**: Enhanced the iterative baseline estimator in `nano_ext/preprocessing/baseline.py` to robustly handle upward-drifting baselines as requested.

### Phase 2: Python Testing & Documentation (Completed)
1.  ✅ **Initial Unit Tests**: `test_pelt.py` and `test_sublevel.py` validate Rust bindings and sub-level analysis.
2.  ✅ **Synthetic Data Generation**: `testing/synthetic.py` produces traces with known event characteristics.
3.  ✅ **Example Script**: `examples/example_basic_usage.py` demonstrates the full pipeline.
4.  ✅ **Test Coverage Expansion**: `test_filters.py`, `test_baseline.py`, `test_threshold.py`, `test_events.py`, `test_pipeline.py` added. GMM/Rust-heavy tests marked `@pytest.mark.slow`; `pytest` (default) runs in ~10s, `pytest -m slow` for full suite.
5.  ✅ **API Documentation**: Improved docstrings across all public-API modules — `DetectionConfig` fields, `csv_writer`, `changepoint`, `sublevel`, `pipeline`, and `__init__`. Sphinx/mkdocs site deferred to Phase 4.

### Phase 3: Performance Optimization (Completed)
1.  ✅ **Rust Project Setup**: `Cargo.toml`, `rust/src/lib.rs`, and maturin/pyproject configuration in place.
2.  ✅ **Rust Kernel Implementation**: 
    - `local_baseline_percentile()` — sliding-window percentile for iterative baseline estimation.
    - `pelt()` — Pruned Exact Linear Time change-point detection for sub-level analysis.
3.  ✅ **Integration**: Python `preprocessing/baseline.py` and `detection/sublevel.py` call into the Rust extension via `nano_ext._nano_ext`.
4.  ✅ **Build & Distribution**: GitHub Actions builds platform-specific wheels (Linux, macOS, Windows) on push to `main` and version tags.
5.  ✅ **Benchmarking**: Quantitative comparison completed (`scripts/benchmark_rust_vs_python.py`).

    | Kernel | Sizes tested | Rust vs Python |
    |---|---|---|
    | `local_baseline_percentile` | n=500–5,000, window=51 | **~5.5×** |
    | `pelt` | n=300–1,500 | **~34×** |

    **Key findings:**
    - `pelt` Rust scaling: n=1k→24ms, n=5k→320ms, n=10k→1.2s (O(n²) dominates for large events).
    - `local_baseline_percentile` has a **performance cliff at n=500,000**: the direct path (n≤500k) is O(n×window), so a 500k-sample signal with window=5001 takes ~94s; the subsampled path (n>500k) takes only ~1.9s (50× faster). This means short recordings (<5s at 100kHz) with large windows are slow.
    - **✅ Fixed (Phase 4)**: changed both Rust and Python paths to a product-based threshold (`n × window > 10M` instead of `n > 500k`). Also fixed an O(n × sparse) linear-search interpolation loop to O(1) using evenly-spaced index arithmetic. New timings: n=100k/window=5001 → **67 ms** (was 19 s); n=500k/window=5001 → **360 ms** (was 94 s).

### Phase 4: Advanced Analysis & Validation (In Progress)
1.  ✅ **Baseline Performance Cliff Fix**: Changed subsampling threshold from `n > 500k` to `n × window > 10M` in both Rust and Python paths; fixed O(n × sparse) interpolation to O(1). Result: ~260× speedup for short recordings with large windows.
2.  ✅ **Multi-directional Detection**: `EventDirection.BOTH` now runs DOWN and UP detection independently with symmetric thresholds, merges results sorted by time, and tags each `Event` with its `direction` field (`EventDirection.DOWN` or `EventDirection.UP`). CSV output includes the new `direction` column. 5 new unit tests added.
3.  🔲 **Automated Parameter Tuning**: Use noise characteristics to automatically set `min_duration` and `merge_gap`.
4.  🔲 **Negative Control Integration**: Use solvent-only control traces to characterize baseline noise (see Section 6).

## 6. Design Note: Negative Control Data for Noise Characterization

### Motivation
In nanopore experiments, a negative control measurement (solvent / buffer only, no analyte) is routinely acquired. This trace contains the open-pore current with all instrumental and electrochemical noise but **no translocation events**. Currently, `Nano_ext` estimates noise statistics from the same trace it analyzes, which has two weaknesses:

1.  **Event contamination**: When events are frequent or long, the noise/baseline statistics are biased by event samples themselves, even with iterative outlier rejection.
2.  **Threshold sensitivity**: GMM+BIC thresholding implicitly assumes the baseline component is well-separated from event components. With heavy event load or low SNR, the baseline Gaussian is poorly estimated.

A control trace eliminates both issues by providing a direct, event-free reference distribution.

### Proposed Use Cases

| Stage | How control data helps |
|:---|:---|
| **Baseline drift model** | Fit drift parameters (polynomial/spline order, smoothing window) on control to avoid over/under-fitting on event-laden traces. |
| **Noise floor (σ)** | Direct estimate of noise std without robust trimming. Feeds into threshold setting (e.g. `baseline − k·σ`) as a fallback when GMM is ambiguous. |
| **GMM prior** | Use the control's single-component fit (μ, σ) as an informative prior for the baseline component in the sample's GMM, stabilizing component assignment. |
| **Threshold validation** | Run the same threshold on the control: any "events" detected are false positives. Tune detection parameters until control FPR is below a target (e.g. < 1 event/min). |
| **Sub-level BIC penalty** | Use control noise statistics to calibrate the BIC penalty, since the optimal penalty depends on noise color/bandwidth. |

### Proposed API Sketch
- Extend `DetectionConfig` with optional `control_signal: SignalData | None`.
- Add `preprocessing/control.py` to compute control-derived statistics (`ControlStats` dataclass: `noise_std`, `baseline_mean`, `drift_coeffs`, ...).
- Pipeline accepts an optional control trace; when present, threshold/baseline stages consume `ControlStats` instead of (or in addition to) self-estimation.
- CLI: `--control PATH` flag pointing to a separate ABF/binary file.

### Open Questions / Caveats
- **Matching conditions**: Control and sample must share sampling rate, filter cutoff, applied voltage, and ideally the same pore (or same chip). Mismatched conditions can mislead more than help. The pipeline should validate metadata and warn on discrepancies.
- **Temporal stationarity**: Pore noise can drift across hours. A control taken far from the sample run may not represent the sample's noise. Consider supporting *bracketed* controls (before + after).
- **Backwards compatibility**: Control input must remain optional — most users will run without it, and the existing self-estimation path must continue to work unchanged.
- **Synthetic validation**: Test the control-augmented pipeline on synthetic data where ground-truth noise is known, to confirm the integration improves (rather than degrades) detection metrics.

### Recommendation
Adopt this as a **Phase 4 feature** (not blocking the current MVP). Start with the simplest integration — using control noise std as a sanity check / fallback threshold — before layering on GMM priors and FPR-tuned threshold calibration.

## 5. Technical Details (Algorithms)

### BIC-Based Thresholding
1. Fit GMM with $k=1 \dots K_{max}$.
2. Select $k$ that minimizes $BIC = k \ln(n) - 2 \ln(\hat{L})$.
3. Identify the baseline component (closest to 0 in residual space).
4. Compute the crossing point of the baseline Gaussian and the nearest event Gaussian as the threshold.

### Sub-level Analysis (Recursive BIC)
1. For each detected event, attempt to split the segment at point $t$ that maximizes the likelihood gain.
2. Use BIC to compare the single-level model vs. the two-level model.
3. Recursively repeat until BIC no longer improves or minimum segment length is reached.
