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
bash benchmarks/external/setup.sh             # external tools (git + uv needed)
python benchmarks/run_phase2.py --workers 4   # external tools + equal-budget tuning, several hours
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
| `run_phase1.py` | runs the phase-1 grid (default settings), writes `results/phase1_*` |
| `run_phase2.py` | external tools and equal-budget tuning, writes `results/phase2_*` |
| `external/` | `setup.sh` (pinned installs of the external tools into `_external/`, untracked) and the worker scripts that run MOSAIC and Nano Trees in their own environments |
| `sublevel_count_repro.py` | reproduces the sub-level figure quoted in the main README |
| `results/` | phase 1: `phase1_runs.csv` (every run), `phase1_summary.csv` (mean and SD over seeds), `phase1_resources.csv`, `phase1.png`; phase 2: `phase2_tuning.csv`, `phase2_selected.csv`, `phase2_sensitivity.csv`, `phase2_test.csv`, `phase2_summary.csv`, `phase2.png` |

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

## Phase 1 results (default settings, mean over 5 seeds)

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

## Phase 2: external tools and equal-budget tuning

### Additional methods

| name | what runs | notes |
|---|---|---|
| `mosaic` | MOSAIC 2.4 (NIST, PyPI `mosaic-nist`): `eventSegment` event detection, each event fitted with `cusumPlus` (CUSUM+) for sub-levels. Only events MOSAIC marks `normal` are counted. | Python 3.10 environment. Its declared dependencies include test and packaging tools that can no longer be installed (codecov 2.1.12 is gone from PyPI), so the runtime pins are installed separately. Importing it fetches analytics settings from the web and its run functions post usage events; the worker disables both. The partitioner fails on an empty last data block, so the worker appends the trace's last 20 ms (open pore), mirrored. |
| `threshold+nanotrees` | Events from `threshold` (default k = 5); sub-levels fitted inside each event (± padding) by the NanoTrees event fitter of Poriscope (commit `9d2b9e8`, MIT). | Python ≥ 3.12.10 environment. The plugin is called as in Poriscope's own unit tests, without its GUI and event loader. Its default *Smallest Significant Sublevel* (600 pA) is far above the 36–60 pA blockades here, so by default it reports one level per event. |
| `autonanopore` | AutoNanopore (commit `a44cb80`), unmodified; `event_detection` is called on an ABF copy of the recording. | The repository has no licence file, so it is fetched, not copied. Its command-line entry point never calls the detection. It keeps the largest excursion of each 30 ms window and accepts windows whose amplitude is an outlier among all windows, so it assumes most windows hold no event. With ~1.5 events per window here it finds no outliers and fails; this is counted as zero detections. On a sparse check recording (2 events/s) it reaches F1 0.64 with defaults. |

CBED (cluster-based event detection, 2026 preprint) is not included: no public code was found.

### Tuning protocol

- **Same budget.** Every method gets 12 parameter settings (`GRIDS` in `run_phase2.py`), covering its main detection (and, where it has them, sub-level) parameters:

  | method | parameters (12 combinations) |
  |---|---|
  | `nano_ext[*]` | `min_event_duration_sec` ∈ {auto, 10, 30, 100 µs} × `filter_cutoff` ∈ {10 kHz, auto (25 kHz), 60 kHz} |
  | `threshold` | k ∈ {3, 4, 5, 6} × exit level ∈ {0.5, 1, 2} σ |
  | `pelt` | penalty factor ∈ {0.5, 1, 2, 4, 8, 16} × event level k ∈ {2, 3} σ |
  | `mosaic` | `eventThreshold` ∈ {3, 4, 5, 6} σ × CUSUM+ `StepSize` ∈ {2, 3, 5} |
  | `threshold+nanotrees` | k ∈ {4, 5} × *Smallest Significant Sublevel* ∈ {5, 10, 20, 40, 80, 600} pA |
  | `autonanopore` | window ∈ {2, 5, 10, 30} ms × θ ∈ {0.5, 1, 1.5} |

- **Separate data.** Settings are chosen on two tuning recordings per scenario (seeds 100 and 101, at most 10 s long). They are evaluated on the phase-1 test recordings (seeds 0–4).
- **Selection.** For each method the setting with the best mean tuning score is chosen:
  - either once for all scenarios (**global**), which is what a user with one setting would get;
  - or separately for every scenario (**per scenario**), an optimistic upper bound.
- **Objectives.** Two objectives are used:
  - event detection, **F1**;
  - sub-levels, **F1 × sub-level count accuracy**, only for methods that report levels.
- **Sensitivity.** The spread of tuning F1 over the 12 settings is kept as a measure of how much results depend on the parameters (`phase2_sensitivity.csv`, bottom row of the figure).
- **Skipped setting.** One selected setting was not tested: PELT's sub-level choice for 100 ms events. Tuning predicts ≈ 75 minutes per recording for it. PELT slows down sharply on long events at high penalties.

![phase 2](results/phase2.png)

### Results (test recordings, mean over 5 seeds)

F1 (IoU ≥ 0.5), mean over the 12 scenarios:

| setting | nano_ext[dpgmm] | nano_ext[gmm] | threshold | pelt | mosaic | threshold+nanotrees | autonanopore |
|---|---|---|---|---|---|---|---|
| default | 0.57 | 0.59 | 0.76 | 0.81 | 0.23 | 0.76 | 0.01 |
| one tuned setting (global) | 0.78 | 0.68 | 0.78 | **0.90** | 0.49 | 0.76 | 0.52 |
| tuned per scenario | 0.84 | 0.82 | 0.85 | **0.92** | 0.49 | 0.76 | 0.54 |

F1 with one tuned setting for all scenarios:

| scenario | nano_ext[dpgmm] | nano_ext[gmm] | threshold | pelt | mosaic | threshold+nanotrees | autonanopore |
|---|---|---|---|---|---|---|---|
| SNR 2 | 0.05 | 0.37 | 0.06 | 0.62 | 0.00 | 0.01 | 0.03 |
| SNR 3 | 0.62 | 0.73 | 0.66 | 0.87 | 0.00 | 0.41 | 0.40 |
| SNR 5 | 0.99 | 0.87 | 0.97 | 1.00 | 0.24 | 0.90 | 0.71 |
| SNR 8 | 1.00 | 0.86 | 1.00 | 1.00 | 0.83 | 1.00 | 0.77 |
| SNR 15 | 1.00 | 0.72 | 1.00 | 1.00 | 0.99 | 1.00 | 0.80 |
| 10 µs | 0.05 | 0.00 | 0.14 | 0.40 | 0.00 | 0.26 | 0.23 |
| 30 µs | 0.64 | 0.02 | 0.64 | 0.93 | 0.29 | 0.81 | 0.75 |
| 100 µs | 0.98 | 0.79 | 0.96 | 1.00 | 0.91 | 0.99 | 0.91 |
| 300 µs | 1.00 | 0.98 | 1.00 | 1.00 | 0.92 | 1.00 | 0.90 |
| 1 ms | 1.00 | 0.86 | 1.00 | 1.00 | 0.83 | 1.00 | 0.77 |
| 10 ms | 1.00 | 1.00 | 1.00 | 1.00 | 0.76 | 0.98 | 0.00 |
| 100 ms | 1.00 | 1.00 | 0.99 | 1.00 | 0.09 | 0.81 | 0.00 |

Sub-levels, one setting tuned for F1 × sub-level accuracy (value: F1 × accuracy):

| scenario | nano_ext[dpgmm] | nano_ext[gmm] | pelt | mosaic | threshold+nanotrees |
|---|---|---|---|---|---|
| SNR 3 | 0.01 | 0.17 | 0.60 | 0.00 | 0.20 |
| SNR 5 | 0.39 | 0.40 | 0.97 | 0.22 | 0.65 |
| SNR 8 | 0.79 | 0.72 | 1.00 | 0.71 | 0.95 |
| SNR 15 | 0.82 | 0.63 | 0.94 | 0.99 | 0.99 |
| 30 µs | 0.49 | 0.12 | 0.51 | 0.17 | 0.18 |
| 100 µs | 0.52 | 0.52 | 0.67 | 0.52 | 0.62 |
| 300 µs | 0.53 | 0.54 | 0.94 | 0.66 | 0.81 |
| 1 ms | 0.79 | 0.72 | 1.00 | 0.71 | 0.95 |
| 10 ms | 1.00 | 0.88 | 0.94 | 0.56 | 0.95 |
| 100 ms | 0.97 | 0.94 | 0.21 | 0.04 | 0.60 |
| **mean (12 scenarios)** | 0.54 | 0.47 | **0.71** | 0.38 | 0.58 |

Median run time per test recording with the global setting: threshold 0.07 s, AutoNanopore 1.1 s, MOSAIC 3.3 s, Nano_ext dpgmm 5.5 s, threshold + Nano Trees 6.6 s, Nano_ext gmm 16 s, PELT 30 s.

### Observations

- **Tuning matters for every method, most for the defaults that are furthest from this data.**
  - Nano_ext dpgmm goes from 0.57 to 0.78 with one setting: a 10 µs minimum event duration and a 10 kHz low-pass. The shorter minimum removes the 200 µs limit, and the lower cutoff helps at low SNR.
  - MOSAIC improves from 0.23 to 0.49 and AutoNanopore from 0.01 to 0.52; both defaults are made for deeper or sparser events.
  - The simple threshold barely changes (0.76 → 0.78): its defaults are already near its best here.
- **PELT with an L2 cost is the most accurate detector on these recordings**, with or without tuning (0.81 default, 0.90 tuned).
  - It is the only method with F1 > 0.6 at SNR 2 and ≥ 0.87 at SNR 3 with one setting.
  - It is also the slowest: 30 s per recording with the chosen setting, and far longer for long events at high penalties.
- **Nano_ext** matches the threshold and PELT from SNR 5 and from 100 µs up once tuned (F1 ≥ 0.96).
  - It stays behind at SNR ≤ 3 (dpgmm 0.05 / 0.62) and for 10–30 µs events.
  - The `gmm` variant is less stable: with one setting it drops to 0.72–0.87 at SNR 5–15. Recall stays 1.00, but precision falls to 0.66–0.83 (over-detection).
- **Sub-levels.** No method is best everywhere.
  - PELT is best from 300 µs to 1 ms and at SNR 5–8.
  - Nano_ext's Dirichlet-process sub-levels are best for long events: 1.00 and 0.97 at 10 ms and 100 ms, where PELT drops to 0.21.
  - Nano Trees (with a 20 pA smallest sub-level) is second overall and close to the best at SNR ≥ 8.
  - Everything is poor below 100 µs, where levels last only a few filter rise times.
- **Sensitivity.** The bottom row of the figure shows the range of tuning F1 over the 12 settings. The mean over scenarios of best − worst F1 is:
  - 0.69 threshold, 0.64 PELT, 0.62 Nano_ext dpgmm, 0.58 Nano_ext gmm, 0.54 AutoNanopore, 0.28 MOSAIC and 0.15 threshold + Nano Trees.
  - The last is low only because its detection varies just k = 4–5.
  - No method is insensitive to its parameters on this data, and the ranges also reflect how wide each grid is. Nano_ext's are widest for 10–30 µs events and at SNR 3–5, where the minimum event duration and the filter cutoff decide what is detected.

## Limitations and next steps

- **Scenario axes not yet varied:** filter cutoff, drift, hum, event density (AutoNanopore in particular is designed for sparse events), number of sub-levels (1–4) and upward events.
- **Shared baseline:** the reference detectors (threshold, PELT) share one simple baseline estimate. The external tools and Nano_ext use their own.
- **Real recordings:** agreement between methods on public recordings, where there is no ground truth, is planned.

