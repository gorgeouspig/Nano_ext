# Nano_ext: Nanopore Signal Event Extraction

A Python-based toolkit (with Rust extensions) for extracting ionic current blockade events from high-sampling-rate (>100 kHz) nanopore traces. Nano_ext uses objective, information-criterion-based methods throughout — no manual threshold tuning required.

## Screenshots

### Interactive Jupyter UI

![Jupyter UI](https://github.com/user-attachments/assets/49d7dd8b-0ee0-44c9-8c68-3b9974626eca)

### Waveform Analysis

![Waveform](https://github.com/user-attachments/assets/3930c3ae-69d2-402e-a63b-eb2265cb2874)

## Features

- **Objective Thresholding**: Gaussian Mixture Models (GMM) + Bayesian Information Criterion (BIC) automatically determine the number of current levels and the optimal detection threshold.
- **Objective Sub-level Analysis**: The same GMM + BIC approach is applied recursively within each event to identify sub-steps without any user-tuned sigma thresholds.
- **Drift Handling**: Iterative local baseline estimation with polynomial/spline global detrending handles gradual open-pore current shifts.
- **Multi-directional Detection**: Detect downward blockades, upward deflections, or both simultaneously. Each event is tagged with its direction.
- **Automated Parameter Tuning**: `suggest_config()` estimates the noise floor from the signal and automatically sets `min_event_duration`, `merge_gap`, and `baseline_window` — a good starting point before manual refinement.
- **Negative Control Integration**: Pass an analyte-free control recording to derive a clean noise estimate, stabilising threshold determination when the sample trace is event-dense.
- **High Performance**: Rust extensions (PyO3/maturin) accelerate the sliding-window baseline percentile and PELT change-point kernels by 5–34×.
- **Flexible Input**: Supports Axon Binary Format (ABF) and raw binary data.
- **Interactive Jupyter UI**: `notebooks/interactive_analysis.ipynb` provides a widget-based GUI for interactive parameter exploration and result inspection — no terminal required during analysis.
- **Comprehensive Output**: Per-event CSV summaries, per-sub-level CSV (joinable via `event_id`), and interactive plotly visualisations.

## Installation

### From Source (Development Mode)

Requires a Rust toolchain (`rustup`) because the package includes a compiled Rust extension.

```bash
# Clone the repository
git clone https://github.com/gorgeouspig/Nano_ext.git
cd Nano_ext

# Create and activate a conda environment (recommended)
conda create -n nano_ext python=3.10
conda activate nano_ext

# Install in development mode (compiles the Rust extension via maturin)
pip install -e ".[dev]"
```

### From Pre-built Wheels

Pre-built wheels for Linux, macOS, and Windows are provided in [GitHub Releases](https://github.com/gorgeouspig/Nano_ext/releases). No Rust toolchain needed.

```bash
pip install nano_ext-*.whl
```

### Interactive Jupyter UI (optional)

```bash
pip install "nano_ext[notebook]"

# Register the environment as a Jupyter kernel (once per environment)
python -m ipykernel install --user --name nano_ext --display-name "Python (nano_ext)"

jupyter notebook notebooks/interactive_analysis.ipynb
```

## Quick Start

### Interactive Jupyter Notebook

The easiest way to get started. Open the notebook and run the cells from top to bottom — no terminal knowledge required for the analysis itself.

```bash
conda activate nano_ext
jupyter notebook notebooks/interactive_analysis.ipynb
```

The UI guides you through four sections:

1. **File Loading** — select signal and optional control files via a file browser
2. **Analysis Settings** — choose event direction (Down / Up) and enable Auto-tune
3. **Additional Settings** *(collapsed by default)* — preprocessing filters and advanced detection parameters, each with explanations
4. **Run → Results** — interactive waveform with event shading, event table, and CSV download

### Command Line Interface

```bash
# Basic analysis — auto-tunes parameters from the signal noise
nano-ext analyze recording.abf -o ./results --auto-tune --plot

# Specify detection direction (default: down)
nano-ext analyze recording.abf -o ./results --event-direction up

# Use a negative control trace for a cleaner noise estimate
nano-ext analyze sample.abf -o ./results --control control.abf --auto-tune

# Pre-filtered data — skip the built-in filter
nano-ext analyze recording.abf -o ./results --no-filter --pre-filter-cutoff 10000

# Full manual control
nano-ext analyze recording.abf -o ./results \
    --event-direction down \
    --detrend-method polynomial --detrend-order 3 \
    --baseline-window-sec 5.0 \
    --min-event-duration-sec 0.0001 \
    --gmm-max-components 10 --bic-criterion bic \
    --plot
```

Output files written to `--output-dir`:
- `<stem>_events.csv` — one row per detected event
- `<stem>_analysis.png` — overview plot (with `--plot`)

```bash
nano-ext analyze --help   # full option reference
```

### Python API

```python
from nano_ext import run_pipeline, suggest_config
from nano_ext.io.abf_reader import read_abf

# Load signal
signal_data = read_abf("recording.abf")

# Option A: auto-tune parameters from noise characteristics
config = suggest_config(signal_data)

# Option B: manual configuration
from nano_ext.models import DetectionConfig, EventDirection
config = DetectionConfig(
    apply_filter=True,
    filter_type="bessel",
    filter_cutoff=10000.0,
    event_direction=EventDirection.DOWN,
    gmm_max_components=10,
)

# Run the pipeline
result = run_pipeline(signal_data, config=config, analyze_sublevel=True, verbose=True)

print(result.summary())
print(f"Detected {result.n_events} events ({result.n_multilevel} multi-level)")

# Access events
for ev in result.events:
    print(f"  t={ev.start_time:.4f}s  depth={ev.depth:.4f}  sublevels={len(ev.sublevels)}")
```

#### Using a negative control

```python
from nano_ext import run_pipeline
from nano_ext.io.abf_reader import read_abf

sample  = read_abf("sample.abf")
control = read_abf("control.abf")   # analyte-free, same conditions

result = run_pipeline(sample, control_signal=control, verbose=True)
```

#### Exporting results

```python
from nano_ext.outputs.csv_writer import write_events_to_csv, write_sublevels_to_csv

sr = result.signal_data.sampling_rate
write_events_to_csv(result.events, "events.csv", sr)
write_sublevels_to_csv(result.events, "sublevels.csv", sr)   # multi-level events only
```

## Algorithm Overview

### 1. Preprocessing
- Optional zero-phase low-pass filtering (Bessel or Butterworth, via `sosfiltfilt` with reflection padding)
- Global detrending (polynomial, linear, or spline) to remove slow drifts
- Iterative local baseline estimation using a Rust-accelerated sliding-window percentile with event-sample exclusion

### 2. Threshold Determination
- Fit GMM with k = 1 … K components to the baseline-corrected residual
- Select optimal k via BIC (or AIC)
- Identify the baseline component (closest to zero in residual space)
- Compute the threshold as the Gaussian crossing point between the baseline component and the nearest event component

### 3. Event Detection
- Binary mask on threshold crossings → run-length encoding → gap merging
- For `EventDirection.BOTH`: DOWN and UP passes run independently with symmetric thresholds; results are merged and sorted by time
- Each `Event` carries `direction`, timestamps, depth, area, and optionally a list of `SubLevel` objects

### 4. Sub-level Analysis (Optional)
- Filter transients at event edges are trimmed (≈ 2 / filter_cutoff seconds per side) to prevent the filter's finite rise time from being misidentified as a current step
- Rust PELT (Pruned Exact Linear Time) change-point detection segments each event; BIC selects the number of levels
- Per-sub-level depth and relative depth from baseline are stored and exportable via `write_sublevels_to_csv`

## Visualization

```python
from nano_ext.outputs.visualize import plot_pipeline_result

plot_pipeline_result(result, "analysis.png")   # save static matplotlib figure
```

The interactive plotly waveform (available in the Jupyter UI) shows:
- Filtered signal with pan/zoom/scroll
- Local baseline and threshold overlays
- Per-event shading with hover tooltips (event index, depth, duration)

## Testing

```bash
pip install -e ".[dev]"

pytest              # fast suite (~10 s), excludes GMM/Rust-heavy tests
pytest -m slow      # full suite including GMM fitting and large Rust baseline calls
pytest --cov=nano_ext   # with coverage report
```

## License

This project is licensed under the MIT License — see the LICENSE file for details.
