# Nano_ext: Nanopore Signal Event Extraction - Development Plan

This document summarizes the requirements and development roadmap for the `Nano_ext` project, as discussed in the session `ses_30fdafa0dffejYZcR4JGjd7V0s`.

## 0. How to Resume Development

1. **Activate environment**: `conda activate nano_ext`
2. **Confirm tests pass**: `pytest` → should finish in ~10s with all green
3. **Check next task**: Look for the first 🔲 item in Section 4 (Roadmap) — that is what to work on next
4. **Run slow tests before committing**: `pytest -m slow` (takes several minutes; covers GMM and Rust baseline)
5. **Commit**: Stage your changes and commit to `main`

**Current next task → Phase 5 完了 / Phase 6 未定義** (see Section 4)

> Performance fix committed 2026-05-02: O(n) baseline smoothing (Rust prefix-sum), numpy zero-copy arrays, `gmm_max_samples` cap → real 250 kHz / 10 s signal processes in 28 s end-to-end.

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

### Phase 4: Advanced Analysis & Validation (Completed)
1.  ✅ **Baseline Performance Cliff Fix**: Changed subsampling threshold from `n > 500k` to `n × window > 10M` in both Rust and Python paths; fixed O(n × sparse) interpolation to O(1). Result: ~260× speedup for short recordings with large windows.
2.  ✅ **Multi-directional Detection**: `EventDirection.BOTH` now runs DOWN and UP detection independently with symmetric thresholds, merges results sorted by time, and tags each `Event` with its `direction` field (`EventDirection.DOWN` or `EventDirection.UP`). CSV output includes the new `direction` column. 5 new unit tests added.
3.  ✅ **Automated Parameter Tuning**: `detection/autotune.py` — `estimate_noise_floor()` (MAD of first-differences) and `suggest_config()` (noise-aware `min_duration`, `merge_gap`, `baseline_window_sec`). CLI gains `--auto-tune` flag. `suggest_config`, `estimate_noise_floor` exported from top-level `nano_ext`. 12 unit tests added (`test_autotune.py`).
4.  ✅ **Negative Control Integration**: `preprocessing/control.py` — `ControlStats` dataclass and `compute_control_stats()`. `run_pipeline(control_signal=…)` and `process_file(control_path=…)` accept a negative control trace; its `noise_std` overrides the sample-derived estimate. CLI gains `--control PATH` flag. `ControlStats`, `compute_control_stats` exported from top-level `nano_ext`. 11 unit tests added (`test_control.py`). Also fixed circular import in `preprocessing/baseline.py` (now imports `local_baseline_percentile` directly from `nano_ext._nano_ext`).

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

### Phase 5: Jupyter Interactive Interface (Planned)

#### Goals
生物系研究者がターミナルなしで解析を行えるインタラクティブなJupyterノートブックを提供する。CLIは残したまま、Jupyterを追加の入り口として位置づける。

#### 技術選定

| 役割 | ライブラリ | 理由 |
|---|---|---|
| UIウィジェット | `ipywidgets` | Jupyter標準、軽量 |
| インタラクティブ可視化 | `plotly` | 波形スクロール・拡大が可能 |
| ノートブック | `notebooks/interactive_analysis.ipynb` | 解析の記録にもなる |

`plotly` と `ipywidgets` は `pyproject.toml` の `[notebook]` optional groupに追加する（`pip install "nano_ext[notebook]"`）。コア依存には含めない。

#### UIレイアウト（ノートブックのセル構成）

```
┌─────────────────────────────────────────┐
│ Section 1: ファイル読み込み              │
│   filepath テキスト入力                  │
│   channel セレクタ (0/1/2...)            │
│   format セレクタ (auto / abf / binary)  │
│   sampling_rate 入力 (binary用)          │
│   [Load] ボタン → 信号情報を表示        │
├─────────────────────────────────────────┤
│ Section 2: 前処理パラメータ             │
│   フィルタ ON/OFF トグル                │
│   filter_cutoff スライダー              │
│   auto-tune トグル (ONでSection3を無効化)│
├─────────────────────────────────────────┤
│ Section 3: 検出パラメータ               │
│   event_direction セレクタ (down/up/both)│
│   baseline_window_sec スライダー        │
│   min_event_duration_sec スライダー     │
│   gmm_max_components スライダー         │
│   [optional] control file パス入力      │
├─────────────────────────────────────────┤
│ Section 4: 実行                         │
│   [Run Analysis] ボタン                 │
│   プログレス表示 (出力ログ)             │
├─────────────────────────────────────────┤
│ Section 5: 結果                         │
│   サマリーテキスト                      │
│   plotly インタラクティブ波形           │
│     (生信号 / フィルタ後 / ベースライン  │
│      / 閾値 / イベントマーカー)          │
│   イベントテーブル (pandas DataFrame)   │
│   [Download CSV] ボタン                 │
└─────────────────────────────────────────┘
```

#### 実装タスク

1.  ✅ **依存関係追加**: `pyproject.toml` に `[notebook]` optional group を追加 (`ipywidgets>=8.0`, `plotly>=5.0`)。conda環境にインストール済み。
2.  ✅ **ウィジェットヘルパーモジュール**: `src/nano_ext/outputs/notebook_widgets.py` を作成。`NanoExtUI` クラスに5セクションのUI全体を実装。lazy importで`[notebook]`なし環境でもコアライブラリはインポート可能。
3.  ✅ **ノートブック作成**: `notebooks/interactive_analysis.ipynb` を作成。UI起動は2セル（確認セル + `NanoExtUI().display()`）。後続セルでDataFrame取得・ヒストグラム描画のカスタム処理例を提供。
4.  ✅ **plotly可視化**: `make_plotly_figure(result)` を実装。2段構成（上: 波形+ベースライン+閾値+イベントシェーディング、下: イベント深さのバーチャート）。ホバーでイベント番号・深さ・持続時間を表示。BOTH方向時は上下両閾値を表示。
5.  ✅ **CSVダウンロード**: `Download CSV` ボタンをBase64エンコードのdata URIとして実装。ブラウザから直接ダウンロード可能。
6.  ✅ **動作確認**: 合成データで `make_plotly_figure` と `NanoExtUI._run_pipeline` の動作を確認。全86テスト通過。

#### 設計上の注意点
- **再実行しない設計**: ウィジェット変更のたびにパイプラインを再実行しない。「Run Analysis」ボタンを1回押したときだけ `run_pipeline` を呼ぶ。結果表示の更新のみはボタンなしで即応してよい（例: イベントテーブルのフィルタリング）。
- **エラーハンドリング**: ファイルが見つからない、sampling_rateが未指定などのエラーはウィジェット上に赤字で表示する（例外をノートブック外に出さない）。
- **ノートブックの自己完結性**: ノートブックを開いてセルを上から実行するだけで動くこと。環境構築手順を冒頭セルのMarkdownに記載する。

### Phase 5 Post-Work: Performance Fixes & Real-Data Validation (Completed 2026-05-02)

These items were discovered and fixed during real-data analysis after Phase 5 shipped.

1. ✅ **O(n×window) Rust smoothing bottleneck**: The sliding-mean at the end of `local_baseline_percentile` was a nested loop. Replaced with an O(n) prefix-sum → baseline step: 331 s → 8.25 s (40×) on a 2.5 M-sample 10 s recording.
2. ✅ **Python `.tolist()` overhead**: `baseline.py` was calling `.tolist()` before passing to Rust, causing element-by-element PyO3 conversion. Added `numpy = "0.27"` crate (`PyReadonlyArray1`) for zero-copy array access. Further reduced to 5.54 s.
3. ✅ **GMM hanging on large signals**: No sample cap meant GMM fitting on 500k–2.5M samples took 100–300 s. Added `gmm_max_samples = 100_000` to `DetectionConfig` (exposed as `--gmm-max-samples` in CLI). GMM step now caps at 20 s.
4. ✅ **Circular import**: `preprocessing/baseline.py` was importing `local_baseline_percentile` from `nano_ext` (top-level `__init__`). Changed to `from nano_ext._nano_ext import local_baseline_percentile`.

**Real data validation results** (`examples/signal/2024_11_29_0016_f32.bin`, 250 kHz, 10 s, EventDirection.UP):
- Control noise floor: 13.06 pA (from `examples/cont/2024_11_29_0013.abf`); SNR ≈ 50
- GMM: k=1, threshold ≈ +84 pA (3σ above baseline)
- Final analysis (min_dur = 0.04 ms): **2 events** detected
  - Event 1: t = 5.1575 s, dur = 0.160 ms, depth = 676.7 pA
  - Event 2: t = 8.4663 s, dur = 0.164 ms, depth = 650.7 pA
  - No sub-levels found
- min_duration sweep summary:

  | min_dur | events | notes |
  |---------|--------|-------|
  | 0.120 ms | 2 | clean large events only |
  | 0.050 ms | 3 | one intermediate event added |
  | 0.020 ms | 6 | small spikes start appearing |
  | 0.010 ms | 364 | noise-dominated |

- Total pipeline time: 28.1 s for 10 s signal (2.8× real-time)

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
