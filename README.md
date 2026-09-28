# Nano_ext: Nanopore Signal Event Extraction

A Python-based toolkit (with Rust extensions) for extracting ionic current blockade events from high-sampling-rate (>100 kHz) nanopore traces. Nano_ext uses objective, information-criterion-based methods throughout — no manual threshold tuning required.

## Screenshots

### Interactive Jupyter UI

![Jupyter UI](https://github.com/user-attachments/assets/49d7dd8b-0ee0-44c9-8c68-3b9974626eca)

### Waveform Analysis

![Waveform](https://github.com/user-attachments/assets/a8aa7c5a-8ea2-4404-bd63-fb56475ebd95)

## Features

- **Objective Thresholding**: Gaussian Mixture Models (GMM) + Bayesian Information Criterion (BIC) automatically determine the number of current levels and the optimal detection threshold.
- **Objective Sub-level Analysis**: The same GMM + BIC approach is applied recursively within each event to identify sub-steps without any user-tuned sigma thresholds.
- **Drift Handling**: Iterative local baseline estimation with polynomial/spline global detrending handles gradual open-pore current shifts.
- **Multi-directional Detection**: Detect downward blockades, upward deflections, or both simultaneously. Each event is tagged with its direction.
- **Automated Parameter Tuning**: `suggest_config()` estimates the noise floor from the signal and automatically sets `min_event_duration`, `merge_gap`, and `baseline_window` — a good starting point before manual refinement.
- **Negative Control Integration**: Pass an analyte-free control recording to derive a clean noise estimate, stabilising threshold determination when the sample trace is event-dense.
- **Artifact Exclusion**: Exclude one or more artifact regions (e.g. from zapping operations) from all estimation steps via `--analysis-range START END` (single keep-window) or `--exclude-range START END` (one or more exclusion windows). Event timestamps in the output remain in original-file coordinates.
- **High Performance**: Rust extensions (PyO3/maturin) accelerate the sliding-window baseline percentile (multi-threaded, selection instead of sorting) and PELT change-point kernels. Per-event sub-level analysis runs in parallel worker processes (`--jobs`, default: all CPUs).
- **Long Recordings on a Laptop**: ABF data are memory-mapped and kept as float32 (exact for 16-bit ADC data); time arrays are implied rather than stored. A 10-minute, 250 kHz recording (150 M samples) runs end-to-end in ~1.5 min with a ~3.3 GB peak (see [Performance](#performance)).
- **Flexible Input**: Supports Axon Binary Format (ABF) and raw binary data.
- **Interactive Jupyter UI**: `notebooks/interactive_analysis.ipynb` provides a widget-based GUI for interactive parameter exploration and result inspection — no terminal required during analysis.
- **Per-Event Waveform Viewer**: Browse individual events with zoomed two-panel figures (signal + residual + sublevel boundaries). Export any event's waveform as `.npz` or `.csv` for downstream ML workflows.
- **HMM Multi-state Analysis**: Gaussian HMM fitting per event with BIC-based state-count selection. Reports state means, transition matrix, and dwell-time distributions. Enable with `--hmm` (requires `pip install "nano_ext[hmm]"`), or use `--hmm-method sticky_hdp` for a sticky HDP-HMM that infers the number of states (no extra dependency).
- **Bayesian Nonparametric Options**: Dirichlet-process GMMs (`--threshold-method dpgmm`, `--sublevel-method dpgmm`) infer the number of current levels in a single fit instead of scanning k by BIC; Bayesian online change-point detection (`--sublevel-method bocpd`) segments events with a hazard-rate prior instead of a penalty.
- **Event Population Clustering**: `--cluster` groups events into populations (relative depth × log dwell time) with a Dirichlet-process GMM — the number of populations is inferred, and each event gets a membership probability.
- **Bayesian Event Statistics**: `--bayes-stats` reports the capture rate with a credible interval and fits a mixture of exponentials to dwell times, giving the posterior number of time constants and their credible intervals (per population with `--cluster`).
- **Power Spectral Density**: Welch-method PSD for noise diagnostics and effective filter-cutoff estimation. Compare sample vs. control with `nano-ext spectrum` or the Diagnostics panel in the Jupyter UI.
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

The UI guides you through five sections:

1. **File Loading** — select signal and optional control files via a file browser
2. **Analysis Range** *(collapsed by default)* — restrict analysis to a time window; a preview button shows the full signal with the selected range highlighted
3. **Analysis Settings** — choose event direction (Down / Up) and enable Auto-tune
4. **Additional Settings** *(collapsed by default)* — preprocessing filters and advanced detection parameters, each with explanations
5. **Run → Results** — interactive waveform with event shading, event table, and CSV download

### Command Line Interface

```bash
# Basic analysis — auto-tunes parameters from the signal noise
nano-ext analyze recording.abf -o ./results --auto-tune --plot

# Specify detection direction (default: down)
nano-ext analyze recording.abf -o ./results --event-direction up

# Use a negative control trace for a cleaner noise estimate
nano-ext analyze sample.abf -o ./results --control control.abf --auto-tune

# Exclude an artifact region (zapping at t=2–3 s); rest of recording is analysed
nano-ext analyze recording.abf -o ./results --exclude-range 2.0 3.0 --auto-tune

# Multiple artifact regions
nano-ext analyze recording.abf -o ./results \
    --exclude-range 2.0 3.0 --exclude-range 7.5 8.0 --auto-tune

# Alternatively, restrict to a specific clean window
nano-ext analyze recording.abf -o ./results --analysis-range 3.0 10.0 --auto-tune

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

# Bayesian / nonparametric variants
nano-ext analyze recording.abf -o ./results --auto-tune \
    --threshold-method dpgmm --sublevel-method bocpd \
    --hmm --hmm-method sticky_hdp \
    --cluster --bayes-stats
```

Output files written to `--output-dir`:
- `<stem>_events.csv` — one row per detected event (plus `cluster_id`, `cluster_prob` with `--cluster`)
- `<stem>_clusters.csv` — one row per event population (with `--cluster`)
- `<stem>_bayes_stats.json` — capture-rate and dwell-time posteriors (with `--bayes-stats`)
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

#### Event populations and Bayesian statistics

```python
from nano_ext.analysis import cluster_events, summarize_event_statistics, dwell_time_mixture

clusters = cluster_events(result.events)          # sets ev.cluster_id / ev.cluster_prob
print(clusters.n_clusters)
print(clusters.summary_table())

stats = summarize_event_statistics(result.events, observation_time=result.signal_data.duration_sec)
print(stats["all"]["capture_rate"])               # mean, lower, upper (events / s)

dw = dwell_time_mixture([ev.duration for ev in result.events])
print(dw.n_components, dw.tau_mean, dw.n_components_probs)
```

## Algorithm Overview

### 1. Preprocessing
- Optional zero-phase low-pass filtering (Bessel or Butterworth, via `sosfiltfilt` with reflection padding)
- Global detrending (polynomial, linear, or spline) to remove slow drifts
- Two-phase local baseline estimation: early iterations use a high-percentile sliding window (Rust-accelerated) to robustly establish an event mask; a final pass computes the median of the masked open-pore samples, yielding an unbiased estimate of the true open-pore current level

### 2. Threshold Determination
- Fit GMM with k = 1 … K components to the baseline-corrected residual
- Select optimal k via BIC (or AIC)
- Identify the baseline component (closest to zero in residual space)
- Compute the threshold as the Gaussian crossing point between the baseline component and the nearest event component
- `threshold_method="dpgmm"`: a single truncated Dirichlet-process GMM fit (variational) replaces the k-scan; unused components are pruned, and adjacent components whose two-component mixture is unimodal are merged, so the baseline is one component even when the noise is slightly non-Gaussian

### 3. Event Detection
- Binary mask on threshold crossings → run-length encoding → gap merging
- For `EventDirection.BOTH`: DOWN and UP passes run independently with symmetric thresholds; results are merged and sorted by time
- Each `Event` carries `direction`, timestamps, depth, area, and optionally a list of `SubLevel` objects

### 4. Sub-level Analysis (Optional)
- Filter transients at event edges are trimmed (≈ 2 / filter_cutoff seconds per side) to prevent the filter's finite rise time from being misidentified as a current step
- `sublevel_method="gmm"` (default): GMM + BIC selects the number of levels; samples are assigned to levels and run-length encoded into segments
- `sublevel_method="dpgmm"`: Dirichlet-process GMM fitted on samples thinned to about one per filter correlation time (`sampling_rate / (2 · cutoff)`), so autocorrelated samples are not counted as independent evidence
- `sublevel_method="bocpd"`: Bayesian online change-point detection (Adams & MacKay, 2007) with a Normal-Inverse-Gamma level model on the thinned samples; segments whose means differ by less than 4 standard errors are grouped into the same level
- Segments shorter than `min_segment_samples` are absorbed into their nearest neighbour
- Per-sub-level depth and relative depth from baseline are stored and exportable via `write_sublevels_to_csv`

### 5. HMM Analysis (Optional)
- `hmm_method="bic"`: hmmlearn Gaussian HMMs for k = 1 … K states, selected by BIC
- `hmm_method="sticky_hdp"`: sticky HDP-HMM (Fox et al., 2011) with a weak-limit Gibbs sampler; the self-transition bias suppresses spurious fast switching, and the posterior over the number of occupied states is reported in `HMMResult.n_states_posterior`

### 6. Event Populations and Statistics (Optional)
- `cluster_events`: Dirichlet-process GMM on z-scored per-event features (default: relative depth, log10 dwell time)
- `capture_rate_posterior`: Gamma posterior of the Poisson capture rate (Jeffreys prior)
- `dwell_time_mixture`: Gibbs-sampled mixture of exponentials with a sparse Dirichlet prior on the weights (superfluous components empty out); dwell times are shifted by the shortest detectable duration

## Visualization

```python
from nano_ext.outputs.visualize import plot_pipeline_result
from nano_ext.outputs.event_viewer import make_event_figure, make_event_grid, dump_event_waveform
from nano_ext.analysis.spectrum import compute_psd, make_psd_figure

plot_pipeline_result(result, "analysis.png")   # static matplotlib overview

# Per-event figures (plotly, Jupyter-ready)
make_event_figure(result, event_index=0).show()   # zoom into event #1
make_event_grid(result, n_cols=3, max_events=9).show()   # side-by-side grid

# Export event waveform for downstream ML
dump_event_waveform(result, event_index=0, path="event_0.npz")

# Power spectral density
psd = compute_psd(result.signal_data)
make_psd_figure(psd).show()
```

The interactive plotly waveform (available in the Jupyter UI) shows:
- Filtered signal with pan/zoom/scroll
- Local baseline and threshold overlays
- Per-event shading with hover tooltips (event index, depth, duration)
- Event Browser: per-event zoom with IntSlider and grid-view toggle
- Diagnostics: PSD plot with optional control overlay

## Performance

Measured on a synthetic 10-minute, 250 kHz, int16 ABF (150 M samples, 1 180 events), 4 CPU cores, default settings with a 1 s baseline window:

| | Before | After |
|---|---|---|
| Peak memory | ~16 GB (extrapolated from a 60 s run: 1.8 GB) | 3.3 GB (+ ~190 MB per worker process) |
| Total time | ~10 min (extrapolated) | 88 s with `--jobs -1` (184 s with `n_jobs=1`) |
| Baseline estimation | ~8 min (extrapolated) | 34 s |

What changed:

- **Readers**: ABF samples are memory-mapped from the file (the header is still parsed by pyabf) and only the requested channel is scaled, to float32. Raw binary files are memory-mapped as well.
- **Pipeline arrays** (filtered signal, baseline, residual) are float32 for float32 input; reductions (medians, fits, sums) are exact or run in float64. On the same data the detected events are identical to a float64 run.
- **`SignalData.time`** is computed on access unless given explicitly; cropped / concatenated signals store an offset or index map instead of a time array. Use `SignalData.time_at(indices)` for a subset.
- **Rust baseline kernel** returns a numpy array (previously a Python list of floats), uses O(n) selection instead of sorting each window, runs windows on all cores and releases the GIL.
- **Filtering** runs in overlapping chunks (identical output) to bound float64 working memory.
- **Reproducibility**: the random subsample used for the threshold model is seeded (`DetectionConfig.random_seed`, default 0); previously repeated runs on the same file could give thresholds a few pA apart.
- **Parallel sub-levels**: `DetectionConfig.n_jobs` (Python API default 1) / `--jobs` (CLI default -1). Results are identical to a sequential run. When calling the API with `n_jobs != 1` from a script, keep the usual `if __name__ == "__main__":` guard (workers are started with `spawn`).

## Testing

```bash
pip install -e ".[dev]"

pytest              # fast suite (~10 s), excludes GMM/Rust-heavy tests
pytest -m slow      # full suite including GMM fitting and large Rust baseline calls
pytest --cov=nano_ext   # with coverage report
```

## License

This project is licensed under the MIT License — see the LICENSE file for details.
