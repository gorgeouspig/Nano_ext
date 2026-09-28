use pyo3::prelude::*;
use numpy::{IntoPyArray, PyArray1, PyReadonlyArray1};
use rayon::prelude::*;

/// A Python module implemented in Rust.
#[pymodule]
fn _nano_ext(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(pelt, m)?)?;
    m.add_function(wrap_pyfunction!(local_baseline_percentile, m)?)?;
    m.add_function(wrap_pyfunction!(local_baseline_percentile_f32, m)?)?;
    Ok(())
}

#[pyfunction]
fn pelt(signal: Vec<f64>, penalty_factor: f64, min_segment_samples: usize) -> Vec<usize> {
    let n = signal.len();
    if n < 2 * min_segment_samples {
        return vec![];
    }

    let beta = 2.0 * penalty_factor * (n as f64).ln();

    // Precompute cumulative sums
    let mut cum_sum = vec![0.0; n + 1];
    let mut cum_sum2 = vec![0.0; n + 1];
    for i in 0..n {
        cum_sum[i + 1] = cum_sum[i] + signal[i];
        cum_sum2[i + 1] = cum_sum2[i] + signal[i].powi(2);
    }

    // Function to compute cost for segment [start, end) (0-indexed, start inclusive, end exclusive)
    // Cost = n * log(variance) where variance = (sum2 - sum*sum/n) / n
    // So cost = n * log((sum2 - sum*sum/n) / n) = n * [log(sum2 - sum*sum/n) - log(n)]
    let cost = |start: usize, end: usize| -> f64 {
        let len = end - start;
        if len < min_segment_samples {
            return f64::INFINITY;
        }
        let sum = cum_sum[end] - cum_sum[start];
        let sum2 = cum_sum2[end] - cum_sum2[start];
        let term = sum2 - sum * sum / len as f64;
        // For numerical stability, ensure term is positive
        let term = if term <= 0.0 { 1e-20 } else { term };
        len as f64 * (term.ln() - (len as f64).ln())
    };

    // F[t] for t in 0..n (F[0]..F[n]) - minimal cost up to position t
    let mut F = vec![f64::INFINITY; n + 1];
    let mut last_change = vec![0; n + 1]; // to store the optimal s for each t
    F[0] = -beta; // base case: cost of empty segmentation

    for t in 1..=n {
        let mut best_cost = f64::INFINITY;
        let mut best_s = 0;
        // Simple O(n^2) approach: check all possible s < t
        for s in 0..t-1 {
            let c = cost(s, t); // segment [s, t)
            let candidate_cost = F[s] + c + beta;
            if candidate_cost < best_cost {
                best_cost = candidate_cost;
                best_s = s;
            }
        }
        F[t] = best_cost;
        last_change[t] = best_s;
    }

    // Now backtrack to find the change points
    let mut change_points = Vec::new();
    let mut t = n;
    while t > 0 {
        let s = last_change[t];
        if s != 0 { // we don't record the change point at 0
            change_points.push(s);
        }
        t = s;
    }
    change_points.reverse();
    change_points
}

/// Sample types the baseline kernel works on (float32 and float64 signals).
trait Sample: Copy + PartialOrd + Send + Sync + numpy::Element {
    fn to_f64(self) -> f64;
    fn from_f64(v: f64) -> Self;
}

impl Sample for f64 {
    fn to_f64(self) -> f64 { self }
    fn from_f64(v: f64) -> Self { v }
}

impl Sample for f32 {
    fn to_f64(self) -> f64 { self as f64 }
    fn from_f64(v: f64) -> Self { v as f32 }
}

fn cmp_samples<T: Sample>(a: &T, b: &T) -> std::cmp::Ordering {
    a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal)
}

/// Percentile of `buf` (index `round(p/100 * (len-1))` of the sorted values),
/// found by selection in O(len) instead of a full sort. Reorders `buf`.
fn select_percentile<T: Sample>(buf: &mut [T], percentile: f64) -> T {
    let idx = ((percentile / 100.0) * (buf.len() as f64 - 1.0)).round() as usize;
    let idx = idx.min(buf.len() - 1);
    *buf.select_nth_unstable_by(idx, cmp_samples).1
}

/// Collect the unmasked, non-NaN samples of `signal[start..end]` into `buf`.
fn collect_valid<T: Sample>(signal: &[T], mask: &[bool], start: usize, end: usize, buf: &mut Vec<T>) {
    buf.clear();
    for i in start..end {
        if mask[i] && !signal[i].to_f64().is_nan() {
            buf.push(signal[i]);
        }
    }
}

fn baseline_kernel<T: Sample>(
    signal: &[T],
    mask: &[bool],
    window_samples: usize,
    percentile: f64,
) -> Vec<T> {
    let n_samples = signal.len();
    if n_samples == 0 {
        return Vec::new();
    }
    let half_win = window_samples / 2;

    // Fallback for windows without valid samples: median of all valid samples.
    // Computed only if some window actually needs it (it costs a full pass).
    let fallback_cell: std::sync::OnceLock<T> = std::sync::OnceLock::new();
    let fallback = || -> T {
        *fallback_cell.get_or_init(|| {
            let mut vals: Vec<T> = Vec::new();
            collect_valid(signal, mask, 0, n_samples, &mut vals);
            if vals.is_empty() {
                T::from_f64(0.0)
            } else {
                let mid = vals.len() / 2;
                *vals.select_nth_unstable_by(mid, cmp_samples).1
            }
        })
    };

    let mut baseline: Vec<T> = vec![T::from_f64(0.0); n_samples];

    // Switch to subsampled path when n_samples * window_samples exceeds a product
    // threshold. This avoids O(n * window) blow-up for short signals with large
    // windows (e.g. a 1-second recording with a 5-second baseline window).
    let use_subsample = (n_samples as u64) * (window_samples as u64) > 10_000_000;

    if use_subsample {
        // Subsample: compute baseline at every `step`-th point, then interpolate.
        // step is chosen so that ~4 sparse points fit inside one window.
        let step = std::cmp::max(1, window_samples / 4);
        let n_sparse = (n_samples + step - 1) / step; // ceil division
        // Windows are independent: evaluate them in parallel, each worker
        // reusing its own buffer.
        let baseline_sparse: Vec<f64> = (0..n_sparse)
            .into_par_iter()
            .map_init(
                || Vec::with_capacity(window_samples.min(n_samples)),
                |buf: &mut Vec<T>, si| {
                    let center = si * step;
                    let start = center.saturating_sub(half_win);
                    let end = std::cmp::min(n_samples, center + half_win);
                    if start >= end {
                        return fallback().to_f64();
                    }
                    collect_valid(signal, mask, start, end, buf);
                    if buf.is_empty() {
                        fallback().to_f64()
                    } else {
                        select_percentile(buf, percentile).to_f64()
                    }
                },
            )
            .collect();

        // Interpolate to full resolution.
        // Since sparse indices are evenly spaced (step apart), the bracket for
        // position i is O(1): left = i/step, right = left+1.
        baseline.par_iter_mut().enumerate().for_each(|(i, out)| {
            let left_si = (i / step).min(n_sparse.saturating_sub(2));
            let right_si = (left_si + 1).min(n_sparse - 1);
            let v = if left_si == right_si {
                baseline_sparse[left_si]
            } else {
                let left_pos = (left_si * step) as f64;
                let right_pos = (right_si * step) as f64;
                let t = (i as f64 - left_pos) / (right_pos - left_pos);
                baseline_sparse[left_si] + t * (baseline_sparse[right_si] - baseline_sparse[left_si])
            };
            *out = T::from_f64(v);
        });
    } else {
        // Direct computation for smaller signals (parallel over centres)
        baseline
            .par_iter_mut()
            .enumerate()
            .for_each_init(
                || Vec::with_capacity(window_samples.min(n_samples)),
                |buf: &mut Vec<T>, (center, out)| {
                    let start = center.saturating_sub(half_win);
                    let end = std::cmp::min(n_samples, center + half_win);
                    if start >= end {
                        *out = fallback();
                        return;
                    }
                    collect_valid(signal, mask, start, end, buf);
                    *out = if buf.is_empty() {
                        fallback()
                    } else {
                        select_percentile(buf, percentile)
                    };
                },
            );
    }

    // Smooth the baseline to remove discontinuities
    let mut smooth_window = std::cmp::max(3, window_samples / 10);
    if smooth_window % 2 == 0 {
        smooth_window += 1;
    }

    // Only smooth if we have enough points
    if n_samples >= smooth_window {
        let half_smooth = smooth_window / 2;

        // O(n) sliding mean with an f64 running sum over [start, end),
        // computed in parallel chunks (each chunk primes its own sum).
        let mut smoothed: Vec<T> = vec![T::from_f64(0.0); n_samples];
        const CHUNK: usize = 1 << 20;
        let src = &baseline;
        smoothed.par_chunks_mut(CHUNK).enumerate().for_each(|(c, out)| {
            let first = c * CHUNK;
            let mut sum = 0.0_f64;
            let mut lo = first.saturating_sub(half_smooth);
            let mut hi = lo;
            for (k, o) in out.iter_mut().enumerate() {
                let i = first + k;
                let start = i.saturating_sub(half_smooth);
                let end = std::cmp::min(n_samples, i + half_smooth + 1);
                while hi < end {
                    sum += src[hi].to_f64();
                    hi += 1;
                }
                while lo < start {
                    sum -= src[lo].to_f64();
                    lo += 1;
                }
                *o = T::from_f64(sum / (end - start) as f64);
            }
        });
        baseline = smoothed;
    }

    baseline
}

/// Sliding-window percentile baseline over unmasked samples (float64).
#[pyfunction]
fn local_baseline_percentile<'py>(
    py: Python<'py>,
    signal: PyReadonlyArray1<'py, f64>,
    mask: PyReadonlyArray1<'py, bool>,
    window_samples: usize,
    percentile: f64,
) -> Bound<'py, PyArray1<f64>> {
    let signal = signal.as_slice().expect("signal must be contiguous");
    let mask = mask.as_slice().expect("mask must be contiguous");
    let out = py.detach(|| baseline_kernel(signal, mask, window_samples, percentile));
    out.into_pyarray(py)
}

/// Sliding-window percentile baseline over unmasked samples (float32).
#[pyfunction]
fn local_baseline_percentile_f32<'py>(
    py: Python<'py>,
    signal: PyReadonlyArray1<'py, f32>,
    mask: PyReadonlyArray1<'py, bool>,
    window_samples: usize,
    percentile: f64,
) -> Bound<'py, PyArray1<f32>> {
    let signal = signal.as_slice().expect("signal must be contiguous");
    let mask = mask.as_slice().expect("mask must be contiguous");
    let out = py.detach(|| baseline_kernel(signal, mask, window_samples, percentile));
    out.into_pyarray(py)
}
