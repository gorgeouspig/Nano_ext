# Nano_ext: Nanopore Signal Event Extraction - Development Plan

This document summarizes the requirements and development roadmap for the `Nano_ext` project, as discussed in the session `ses_30fdafa0dffejYZcR4JGjd7V0s`.

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

The project has a functional Python skeleton with several components completed:

- [x] **Data IO**: `abf_reader.py` and `binary_reader.py` for loading signals.
- [x] **Preprocessing**: `filters.py` (Bessel/Butterworth) and `baseline.py` (iterative drift correction).
- [x] **Detection Core**: `threshold.py` (GMM+BIC) and `events.py` (threshold-based detection).
- [x] **Sub-level Analysis**: `sublevel.py` and `changepoint.py` (recursive segmentation).
- [x] **Pipeline**: `pipeline.py` for end-to-end orchestration.
- [x] **CLI**: Implemented in `nano_ext/cli.py` with full configuration options.
- [x] **Visualization**: Implemented in `nano_ext.outputs/visualize.py` for comprehensive result plotting.
- [x] **Export**: Implemented in `nano_ext.outputs/csv_writer.py` for CSV/TSV output.
- [ ] **Rust Extension**: `Cargo.toml` and Rust source code are not yet created.
- [ ] **Testing**: Synthetic data generation exists (`testing/synthetic.py`), but formal unit tests are missing.

## 4. Development Roadmap

### Phase 1: Python MVP & CLI (Completed)
✅ **CLI Implementation**: Created `nano_ext/cli.py` to allow users to run the pipeline on ABF/binary files from the command line.
✅ **Visualization Module**: Created `nano_ext.outputs/visualize.py` using `matplotlib` to plot raw/filtered signals, baseline, thresholds, and detected events.
✅ **Export Module**: Created `nano_ext.outputs/csv_writer.py` to save event statistics to CSV/TSV.
✅ **Baseline Drift Refinement**: Enhanced the iterative baseline estimator in `nano_ext/preprocessing/baseline.py` to robustly handle upward-drifting baselines as requested.

### Phase 2: Python Testing & Documentation (Short-term)
1.  **Unit Testing**: Establish a test suite in `/tests` using `pytest` to validate core components (IO, preprocessing, detection).
2.  **Synthetic Data Validation**: Expand `testing/synthetic.py` to generate test data with known event characteristics for validation.
3.  **Documentation**: Improve docstrings and create user-facing documentation (README, API docs).
4.  **Example Scripts**: Create example usage scripts in `/examples` demonstrating common workflows.

### Phase 3: Performance Optimization (Mid-term)
1.  **Rust Project Setup**: Initialize `maturin` and create `Cargo.toml`.
2.  **Rust Kernel Implementation**: 
    - Port sliding window percentile/mean calculations to Rust.
    - Implement the **PELT** (Pruned Exact Linear Time) algorithm in Rust for fast change-point detection.
3.  **Integration**: Replace Python bottleneck functions with Rust calls in `nano_ext/preprocessing/baseline.py` and `nano_ext/detection/changepoint.py`.

### Phase 4: Advanced Analysis & Validation (Long-term)
1.  **Multi-directional Detection**: Support detection of both blockades and current spikes in the same trace.
2.  **Automated Parameter Tuning**: Use noise characteristics to automatically set `min_duration` and `merge_gap`.
3.  **Performance Benchmarking**: Benchmark Rust-accelerated components against pure Python implementations.

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
