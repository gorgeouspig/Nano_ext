"""Benchmark each pipeline step to find the bottleneck."""
import time, logging, sys
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')

from nano_ext.io.binary_reader import read_binary
from nano_ext.io.abf_reader import read_abf
from nano_ext.detection.autotune import suggest_config
from nano_ext.models import EventDirection

sig  = read_binary('examples/signal/2024_11_29_0016_f32.bin', sampling_rate=250000.0, dtype='float32')
ctrl = read_abf('examples/cont/2024_11_29_0013.abf', channel=0)
config = suggest_config(sig, event_direction=EventDirection.UP)

sr     = sig.sampling_rate
signal = sig.signal
print(f'Signal: {len(signal):,} samples  sr={sr:.0f}', flush=True)
print(f'Config: cutoff={config.filter_cutoff:.0f} Hz  '
      f'min_dur={config.min_event_duration_sec*1000:.3f} ms  '
      f'baseline_window={config.baseline_window_sec:.1f} s  '
      f'gmm_max={config.gmm_max_components}', flush=True)

# Step 1: Filter
t0 = time.time()
from nano_ext.preprocessing.filters import lowpass_filter
filtered = lowpass_filter(signal, sr, config.filter_cutoff,
                          filter_type=config.filter_type, order=config.filter_order)
print(f'[{time.time()-t0:5.2f}s] Step 1: filtering done', flush=True)

# Step 2: Baseline (sample)
t0 = time.time()
from nano_ext.preprocessing.baseline import estimate_baseline
bl = estimate_baseline(filtered, sr,
    detrend_method=config.detrend_method, detrend_order=config.detrend_order,
    window_sec=config.baseline_window_sec, n_iterations=config.baseline_iterations,
    percentile=config.baseline_percentile, n_sigma=config.baseline_n_sigma,
    noise_estimation=config.noise_estimation)
print(f'[{time.time()-t0:5.2f}s] Step 2: baseline done  noise_std={bl.noise_std:.4f}', flush=True)

# Step 3: Control noise
t0 = time.time()
from nano_ext.preprocessing.control import compute_control_stats
cs = compute_control_stats(ctrl, config)
print(f'[{time.time()-t0:5.2f}s] Step 3: control noise={cs.noise_std:.4f}', flush=True)

# Step 4: GMM — benchmark multiple sample sizes
from nano_ext.detection.threshold import determine_threshold
for n_fit in [10_000, 30_000, 100_000]:
    t0 = time.time()
    th = determine_threshold(bl.residual, max_components=config.gmm_max_components,
                             criterion=config.bic_criterion, max_samples_for_fit=n_fit)
    print(f'[{time.time()-t0:5.2f}s] Step 4 GMM (n_fit={n_fit:,}): '
          f'k={th.n_components}  threshold={th.threshold:.2f} pA', flush=True)
