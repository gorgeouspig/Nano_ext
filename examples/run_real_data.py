"""Analysis script for real ABF data."""
import time
import os
import sys
import numpy as np
import matplotlib
matplotlib.use('Agg')

from nano_ext.io.abf_reader import read_abf
from nano_ext import run_pipeline, suggest_config
from nano_ext.models import EventDirection
from nano_ext.outputs.csv_writer import write_events_to_csv, write_sublevels_to_csv
from nano_ext.outputs.visualize import plot_pipeline_result

os.makedirs('examples/output', exist_ok=True)

print('Loading files...')
sys.stdout.flush()
from nano_ext.io.binary_reader import read_binary
sig  = read_binary('examples/signal/2024_11_29_0016_f32.bin',
                   sampling_rate=250000.0, dtype='float32')
ctrl = read_abf('examples/cont/2024_11_29_0013.abf', channel=0)
print(f'  Signal : {sig.n_samples:,} samples  ({sig.duration_sec:.1f} s)')
print(f'  Control: {ctrl.n_samples:,} samples ({ctrl.duration_sec:.1f} s)')
sys.stdout.flush()

print('\nBuilding config (auto-tune)...')
config = suggest_config(sig, event_direction=EventDirection.UP)
print(f'  filter_cutoff         : {config.filter_cutoff:.0f} Hz')
print(f'  min_event_duration_sec: {config.min_event_duration_sec*1000:.4f} ms')
print(f'  merge_gap_sec         : {config.merge_gap_sec*1000:.4f} ms')
print(f'  baseline_window_sec   : {config.baseline_window_sec:.1f} s')
sys.stdout.flush()

print('\nRunning pipeline...')
sys.stdout.flush()
t0 = time.time()
result = run_pipeline(sig, config=config, control_signal=ctrl,
                      analyze_sublevel=True, verbose=True)
elapsed = time.time() - t0
print(f'\nDone in {elapsed:.1f} s')
sys.stdout.flush()

print('\n' + '='*55)
print(result.summary())
print('='*55)

write_events_to_csv(result.events, 'examples/output/events.csv', sig.sampling_rate)
write_sublevels_to_csv(result.events, 'examples/output/sublevels.csv', sig.sampling_rate)
n_sub = sum(len(e.sublevels) for e in result.events)
print(f'\nevents.csv    : {len(result.events)} rows')
print(f'sublevels.csv : {n_sub} rows')

if result.events:
    durations = np.array([e.duration * 1000 for e in result.events])
    depths    = np.array([e.depth for e in result.events])
    n_multi   = sum(1 for e in result.events if e.is_multilevel)

    print(f'\n--- Duration (ms) ---')
    for label, val in [
        ('median', np.median(durations)), ('mean', np.mean(durations)),
        ('std',    np.std(durations)),    ('min',  np.min(durations)),
        ('max',    np.max(durations)),    ('p25',  np.percentile(durations, 25)),
        ('p75',    np.percentile(durations, 75)), ('p95', np.percentile(durations, 95)),
    ]:
        print(f'  {label:6s}: {val:.3f} ms')

    print(f'\n--- Depth (pA) ---')
    for label, val in [
        ('median', np.median(depths)), ('mean', np.mean(depths)),
        ('std',    np.std(depths)),    ('min',  np.min(depths)),
        ('max',    np.max(depths)),    ('p25',  np.percentile(depths, 25)),
        ('p75',    np.percentile(depths, 75)), ('p95', np.percentile(depths, 95)),
    ]:
        print(f'  {label:6s}: {val:.2f} pA')

    print(f'\n--- Sub-level ---')
    print(f'  multi-level events: {n_multi} / {len(result.events)} ({100*n_multi/len(result.events):.1f}%)')
    if n_sub > 0:
        sl_counts = [len(e.sublevels) for e in result.events if e.is_multilevel]
        print(f'  sublevels/event (multi only): median={np.median(sl_counts):.1f}  max={max(sl_counts)}')

    # Event rate
    event_rate = len(result.events) / sig.duration_sec
    print(f'\n--- Event rate ---')
    print(f'  {event_rate:.2f} events/s  ({event_rate*60:.1f} events/min)')

plot_pipeline_result(result, 'examples/output/analysis_full.png')
print('\nanalysis_full.png saved')
sys.stdout.flush()
