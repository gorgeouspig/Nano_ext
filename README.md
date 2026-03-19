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

### From Source (Development Mode)
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

### Installing via Pre-built Wheels
We provide pre-built wheels for supported platforms in our GitHub Releases. It is recommended to install these within a virtual environment.

```bash
# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install the wheel file downloaded from the latest release
pip install nano_ext-0.1.0-cp310-cp310-linux_x86_64.whl
```

## Usage

### Command Line Interface

The `nano-ext` CLI is designed for batch processing of nanopore data files.

#### Basic Usage
The primary positional argument is the input file path. Use the `-o` or `--output-dir` option to specify the directory where results will be saved.

```bash
# Process input file (abf or binary) and save output files to ./analysis_results
nano-ext path/to/input.abf -o ./analysis_results
```

The tool will generate:
1. `[input_filename]_events.csv`: A summary of detected events.
2. `[input_filename]_analysis.png`: (If `--plot` is used) A visualization plot.

#### Handling Pre-Filtered Data
If your data is already low-pass filtered, you can disable the built-in filtering to avoid phase distortion and provide the known cutoff frequency to ensure accurate event detection parameters.

```bash
# Disable built-in filtering and specify the pre-applied cutoff frequency (e.g., 10 kHz)
nano-ext path/to/input.abf -o ./analysis_results --no-filter --pre-filter-cutoff 10000
```

#### Visualizing Results
To visualize the analysis, add the `--plot` flag. This generates an `[input_filename]_analysis.png` file in your output directory, which includes:

- **Raw Signal** (gray)
- **Filtered Signal** (blue)
- **Local Baseline** (red)
- **Detected Events** (orange highlights)

```bash
nano-ext path/to/input.abf -o ./analysis_results --plot
```

#### Handling Pre-Filtered Data
If your data is already low-pass filtered, you can disable the built-in filtering to avoid phase distortion and inform the pipeline of the previous filter's cutoff frequency for accurate event parameter calculation.

```bash
# Process pre-filtered data (e.g., filtered at 10 kHz)
nano-ext input.abf -o ./analysis_results --no-filter --pre-filter-cutoff 10000
```

#### Advanced Analysis Options
```bash
nano-ext input.abf -o ./analysis_results \
    --detrend-method polynomial --detrend-order 3 \
    --baseline-window-sec 5.0 --baseline-iterations 3 \
    --event-direction down \
    --min-event-duration-sec 0.0001 \
    --gmm-max-components 10 --bic-criterion bic \
    --plot
```

*For a full list of available options, run:*
```bash
nano-ext --help
```

### Python API

```python
from nano_ext import SignalData, run_pipeline
from nano_ext.models import DetectionConfig

# Load your signal data
signal_data = SignalData(
    signal=your_current_signal_array,
    sampling_rate=100000.0,  # Hz
    units="pA"
)

# Configure detection parameters
config = DetectionConfig(
    apply_filter=True,
    filter_type="bessel",
    detrend_method="polynomial",
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

## Visualization

The `nano_ext.outputs.visualize` module provides functionality to generate comprehensive plots of the analysis results. When using the CLI with the `--plot` flag, an `[input_filename]_analysis.png` file is automatically saved in the output directory.

The generated plot visualizes:
- **Raw Signal**: The original input data (in gray).
- **Filtered Signal**: The signal after low-pass filtering (in blue).
- **Local Baseline**: The estimated baseline trend (in red).
- **Detected Events**: Highlighted regions (in orange) showing the detected events, allowing for easy verification of the pipeline's detection accuracy.

## Testing

Run the test suite:
```bash
pip install -e .[dev]
pytest
```

## License

This project is licensed under the MIT License - see the LICENSE file for details.
