# Benchmarks

Accuracy of event detection and sub-level analysis on synthetic recordings
with known ground truth, for Nano_ext and simple reference methods. Nothing
here is part of the installed package, and generated signals are not stored
(every run regenerates them from fixed seeds).

```bash
pip install -e .                              # Nano_ext itself
pip install -r benchmarks/requirements.txt    # reference methods (ruptures)
python benchmarks/sublevel_count_repro.py     # README sub-level figure, ~1 min
python benchmarks/run_phase1.py --workers 4   # SNR / dwell-time grid, ~40 min on 4 cores
python benchmarks/run_phase1.py --quick       # smaller grid, 2 seeds
```

`run_phase1.py --resume` keeps finished recordings in
`results/phase1_runs.csv` and runs only the missing ones;
`--resources-only` re-measures run time and memory.

## Files

| file | content |
|---|---|
| `scenarios.py` | synthetic recordings (seeded) and their ground truth |
| `adapters.py` | methods under test, all with a common output format |
| `common.py` | event matching, metrics, shared baseline estimate of the reference methods |
| `run_phase1.py` | runs the grid, writes `results/` |
| `sublevel_count_repro.py` | reproduces the sub-level figure quoted in the main README |
| `results/` | `phase1_runs.csv` (every run), `phase1_summary.csv` (mean and SD over seeds), `phase1_resources.csv`, `phase1.png` |

## Synthetic recordings

Generated with `nano_ext.testing.synthetic`:

- 250 kHz sampling, 200 pA open pore.
- A 4-pole Bessel low-pass at 30 kHz is simulated as an analog filter (4× oversampled, causal). Steps therefore have a 10–90 % rise time of ≈ 11 µs, and events appear 11 µs after their nominal start. The ground truth accounts for this delay.
- The noise goes through the same filter, as in an amplifier. It is band-limited white noise plus 1/f noise, with the 1/f part making up 30 % of the RMS.
- Events arrive as a Poisson process at ≤ 50 /s, and cover about 20 % of the time or less.
- Dwell times are log-normal (σ_log = 0.5).
- Half of the events have one level (60 pA blockade) and half have two. The second level blocks 36 pA, and the split between the two levels is drawn from Dirichlet(5, 5).
- **SNR** = first-level blockade ÷ total noise RMS of the recording.

Phase 1 varies one parameter at a time around a centre point (SNR 8, mean dwell 1 ms), with 5 seeds per point:

| axis | values |
|---|---|
| SNR | 2, 3, 5, 8, 15 |
| mean dwell time | 10 µs, 30 µs, 100 µs, 300 µs, 1 ms, 10 ms, 100 ms (≈ 1–10 000 filter rise times) |

Recordings last 4–30 s, so each has about 100–190 events (43 at 100 ms).

The generator can also add mains hum with harmonics, power-law (f¹–f²) high-frequency noise and baseline drift. These are not yet varied (next phase).

## Methods (default settings, no tuning)

| name | description |
|---|---|
| `nano_ext[dpgmm]` | `run_pipeline` with `DetectionConfig()` (current defaults: Dirichlet-process mixture threshold and sub-levels), single process |
| `nano_ext[gmm]` | same with `threshold_method="gmm", sublevel_method="gmm"` (defaults up to v1.1) |
| `threshold` | fixed threshold: an event starts where the signal drops below baseline − 5σ, and its boundaries extend outward to where it returns above baseline − 1σ (hysteresis). No sub-levels. |
| `pelt` | PELT change points on the baseline-subtracted trace (`ruptures.KernelCPD(kernel="linear")`, i.e. L2 cost, penalty 2σ² ln n). Segments whose mean lies more than 3σ below the baseline are event levels; consecutive ones form one event, one sub-level per segment. |

**Shared baseline and noise for the reference methods.** The baseline is the running 80th percentile, over 2 s, of 256-sample block medians. σ is the median absolute deviation (MAD) of the baseline-subtracted signal. Nano_ext uses its own baseline.

## Metrics

- **Matching.** Detected and true events are paired one-to-one, greedily by decreasing interval IoU (intersection over union). A pair counts as a true positive at **IoU ≥ 0.5**.
  - **Recall, precision and F1** follow from these pairs.
  - **`f1_any`** accepts any overlap instead, i.e. detection regardless of boundary accuracy.
- **`dwell_err`, `depth_err`.** Median absolute relative error over matched events.
  - Depth is the duration-weighted mean blockade relative to the baseline.
- **`level_acc`.** Fraction of matched events whose number of sub-levels is correct.
  - It is only computed for methods that report sub-levels, and only over matched events. At low F1 it rests on few events.
- **`bound_err_us`.** Median absolute error of sub-level boundaries (µs), over matched multi-level events with the right level count.
- **Run time.** Wall-clock time per recording (`phase1_runs.csv`, which is noisier because 4 recordings run in parallel).
  - `phase1_resources.csv` measures run time and peak RSS serially, each in a fresh process, on the centre scenario (1 M samples).

## Results (phase 1, default settings, mean over 5 seeds)

![phase 1](results/phase1.png)

F1 (IoU ≥ 0.5):

| SNR | nano_ext[dpgmm] | nano_ext[gmm] | threshold | pelt |
|---|---|---|---|---|
| 2 | 0.00 | 0.04 | 0.01 | 0.00 |
| 3 | 0.03 | 0.36 | 0.41 | 0.50 |
| 5 | 0.84 | 0.83 | 0.90 | 0.86 |
| 8 | 1.00 | 1.00 | 1.00 | 0.99 |
| 15 | 1.00 | 0.96 | 1.00 | 0.99 |

| mean dwell | nano_ext[dpgmm] | nano_ext[gmm] | threshold | pelt |
|---|---|---|---|---|
| 10 µs | 0.00 | 0.00 | 0.26 | 0.53 |
| 30 µs | 0.00 | 0.00 | 0.81 | 0.94 |
| 100 µs | 0.10 | 0.10 | 0.99 | 0.97 |
| 300 µs | 0.85 | 0.85 | 1.00 | 0.98 |
| 1 ms | 1.00 | 1.00 | 1.00 | 0.99 |
| 10 ms | 1.00 | 1.00 | 0.98 | 1.00 |
| 100 ms | 0.98 | 0.99 | 0.81 | 0.99 |

Sub-level count accuracy (matched events):

| SNR | nano_ext[dpgmm] | nano_ext[gmm] | pelt |
|---|---|---|---|
| 3 | 0.80 | 0.73 | 0.69 |
| 5 | 0.61 | 0.61 | 0.77 |
| 8 | 0.79 | 0.76 | 0.83 |
| 15 | 0.82 | 0.77 | 0.05 |

| mean dwell | nano_ext[dpgmm] | nano_ext[gmm] | pelt |
|---|---|---|---|
| 100 µs | 0.57 | 0.56 | 0.80 |
| 300 µs | 0.53 | 0.54 | 0.90 |
| 1 ms | 0.79 | 0.76 | 0.83 |
| 10 ms | 1.00 | 0.89 | 0.62 |
| 100 ms | 0.99 | 0.99 | 0.04 |

Run time and memory, centre scenario (1 M samples, single thread; peak RSS increase over the loaded data):

| method | run time | peak RSS increase |
|---|---|---|
| nano_ext[dpgmm] | 5.7 s | 25 MB |
| nano_ext[gmm] | 14.5 s | 67 MB |
| threshold | 0.09 s | 46 MB |
| pelt | 10.8 s | 64 MB |

### Observations

- **Short events.** With default settings, Nano_ext discards events shorter than 5 / f_c, where f_c is its own low-pass cutoff (sampling rate / 10 = 25 kHz here), so the limit is 200 µs. Events with a mean dwell ≤ 100 µs are therefore largely missing. The simple threshold and PELT detect them down to ~30 µs (≈ 3 rise times). Setting `min_event_duration_sec` removes this limit; tuned settings are not evaluated yet.
- **Low SNR.** At SNR 3 every method struggles (F1 ≤ 0.5), and the Dirichlet-process threshold is worst (0.03). For the other three methods `f1_any` is 0.73–0.77, so most of their detections overlap a true event but with wrong boundaries or merged neighbours. For the Dirichlet-process threshold it is 0.28, i.e. it also misses events. At SNR ≥ 8 all methods detect essentially every event.
- **Sub-levels, long events.** For events of 10–100 ms the Dirichlet-process sub-level analysis finds the true count almost always (0.99–1.00). PELT with a BIC-type penalty over-segments long levels, because it reads the 1/f wander as level changes (0.04 at 100 ms), and it does the same at high SNR (0.05 at SNR 15).
- **Sub-levels, short events.** For 100 µs–1 ms events PELT is more accurate (0.80–0.90 vs 0.53–0.79). The levels there last only tens to hundreds of µs, i.e. a few filter rise times.
- **Feature accuracy.** Dwell-time errors are similar across methods (< 2 % from 1 ms up). PELT gives the smallest dwell error at most dwell times and the smallest sub-level boundary error (≈ 3 µs vs ≈ 4 µs for Nano_ext).

### Reproducing the main README figure

`sublevel_count_repro.py` reproduces the sub-level level-count accuracy quoted in the top-level README. It gives **dpgmm 1.00, gmm 0.66, bocpd 0.25** on 217 events, with the v1.1 threshold default (`--threshold-method gmm`). With the current default threshold (`dpgmm`) it gives 1.00 / 0.65 / 0.27.

That recording is favourable: every level lasts ≥ 1 ms, the blockades are deep (SNR ≈ 17–22) and there is no simulated filter. The grid above shows that accuracy drops for shorter levels.

## Limitations and next steps

- Only default settings so far. A tuned comparison, where each method gets the same search budget over its main parameters, and parameter-sensitivity curves are planned.
- Scenario axes not yet varied: filter cutoff, drift, hum, event density, number of sub-levels (1–4), upward events.
- Further methods (e.g. CUSUM-type and tree-based level fitting) and agreement on public real recordings are planned.
