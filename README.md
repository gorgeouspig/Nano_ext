# Nano_ext: Nanopore Signal Event Extraction

A Python-based toolkit (with Rust extensions) designed to extract events (blockades) from high-sampling rate (>100 kHz) nanopore current traces. It provides an objective, information-criterion-based approach to thresholding and sub-level (multi-step) event analysis.

## Features

- **Objective Thresholding**: Uses Gaussian Mixture Models (GMM) and Bayesian Information Criterion (BIC) to automatically determine the number of current levels and optimal detection thresholds.
- **Drift Handling**: Robustly handles baseline drift (gradual shifts in open-channel current) using iterative local estimation.
- **Sub-level Analysis**: Recursively decomposes complex events into multiple sub-steps using change-point detection (PELT algorithm) and BIC.
- **High Performance**: Uses Rust (PyO3/maturin) to accelerate bottleneck computations for large datasets.
- **Flexible Input**: Supports Axon Binary Format (ABF) and raw binary data.
- **Comprehensive Output**: Provides CSV/TSV summaries and visualization plots.

## Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/nano_ext.git
cd nano_ext

# Create a virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate

# Install the package in development mode
pip install -e .
```

## Usage

### Command Line Interface

```bash
# Basic usage
nano-ext input.abf -o output_directory

# With advanced options
nano-ext input.abf -o output_directory \
    --detrend-method polynomial \
    --baseline-window-sec 5.0 \
    --gmm-max-components 10 \
    --plot
```

### Python API

```python
from nano_ext import SignalData, run_pipeline
from nano_ext.models import DetectionConfig

# Load your signal data (example with synthetic data)
signal_data = SignalData(
    signal=your_current_signal_array,
    sampling_rate=100000.0,  # Hz
    units="pA"
)

# Configure detection parameters
config = DetectionConfig(
    detrend_method="polynomial",
    baseline_window_sec=5.0,
    gmm_max_components=10,
)

# Run the pipeline
result = run_pipeline(
    signal_data=signal_data,
    config=config,
    analyze_sublevel=True,
    verbose=True
)

# Access results
print(f"Detected {result.n_events} events")
print(result.summary())

# Save results
from nano_ext.output.csv_writer import write_events_to_csv
from nano_ext.output.visualize import plot_pipeline_result

write_events_to_csv(result.events, "events.csv", result.signal_data.sampling_rate)
plot_pipeline_result(result, "analysis.png")
```

## Algorithm Overview

### 1. Preprocessing
- Optional low-pass filtering (Bessel or Butterworth)
- Global detrending (polynomial, linear, or spline) to remove slow drifts
- Iterative local baseline estimation using percentile-based approach

### 2. Threshold Determination
- Fit Gaussian Mixture Models (GMM) with k=1..K components to baseline-corrected signal
- Select optimal k using Bayesian Information Criterion (BIC)
- Identify baseline component (closest to zero in residual space)
- Compute threshold as crossing point between baseline and nearest event Gaussians

### 3. Event Detection
- Threshold crossing analysis to detect event start/end points
- Optional merging of closely spaced events
- Baseline current and event statistics calculation

### 4. Sub-level Analysis (Optional)
- For each detected event, apply Pruned Exact Linear Time (PELT) algorithm
- Uses Gaussian negative log-likelihood as cost function
- BIC-based decision to accept/reject change points
- Recursive decomposition until no further improvement

## Output

The pipeline produces:
- CSV file with event statistics (start/end times, duration, depth, area, etc.)
- Optional visualization plot showing:
  - Raw and filtered signals
  - Estimated baseline
  - Baseline-corrected signal with threshold
  - Detected events colored by depth

## Performance Optimization

Computationally intensive components have been accelerated with Rust:
- PELT change-point detection algorithm
- Local baseline estimation using sliding window percentile filtering

## Testing

Run the test suite:
```bash
pip install -e .[dev]
pytest
```

## References

The implementation follows principles from:
- Chung et al. (2011) for nanopore signal analysis
- Killick et al. (2012) for the PELT algorithm
- Schwarz (1978) for Bayesian Information Criterion

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

Developed as part of nanopore sensing research aimed at providing objective, automated analysis tools for single-molecule biophysics.