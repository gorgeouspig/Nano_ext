use pyo3::prelude::*;

/// A Python module implemented in Rust.
#[pymodule]
fn _nano_ext(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(pelt, m)?)?;
    m.add_function(wrap_pyfunction!(local_baseline_percentile, m)?)?;
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

#[pyfunction]
fn local_baseline_percentile(signal: Vec<f64>, mask: Vec<bool>, window_samples: usize, percentile: f64) -> Vec<f64> {
    let n_samples = signal.len();
    let half_win = window_samples / 2;
    let mut baseline = vec![0.0; n_samples];
    
    // Handle edge case where signal is too small
    if n_samples == 0 {
        return baseline;
    }
    
    // Replace event samples with NaN for percentile calculation
    let mut masked_signal = signal.clone();
    for i in 0..n_samples {
        if !mask[i] {
            masked_signal[i] = f64::NAN;
        }
    }
    
    // For very large signals, we subsample and interpolate
    if n_samples > 500_000 {
        // Subsample: compute baseline at every Kth point, then interpolate
        let step = std::cmp::max(1, window_samples / 4);
        let indices: Vec<usize> = (0..n_samples).step_by(step).collect();
        let mut baseline_sparse = vec![0.0; indices.len()];
        
        for (i, &center) in indices.iter().enumerate() {
            let start = std::cmp::max(0, center as isize - half_win as isize) as usize;
            let end = std::cmp::min(n_samples, center + half_win);
            if start >= end {
                if i > 0 {
                    baseline_sparse[i] = baseline_sparse[i - 1];
                }
                continue;
            }
            
            let window = &masked_signal[start..end];
            let valid: Vec<f64> = window.iter().filter(|x| !x.is_nan()).cloned().collect();
            if valid.len() > 0 {
                // Calculate percentile
                let mut sorted = valid.clone();
                sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
                let index = ((percentile / 100.0) * (sorted.len() as f64 - 1.0)).round() as usize;
                baseline_sparse[i] = sorted[index];
            } else if i > 0 {
                baseline_sparse[i] = baseline_sparse[i - 1];
            }
        }
        
        // Interpolate to full resolution
        for i in 0..n_samples {
            // Find the two closest indices in our sparse array
            let mut left_idx = 0;
            let mut right_idx = indices.len() - 1;
            
            if indices.len() < 2 {
                baseline[i] = if indices.len() > 0 { baseline_sparse[0] } else { 0.0 };
                continue;
            }
            
            // Binary search for the right interval
            for j in 0..indices.len() {
                if indices[j] > i as usize {
                    right_idx = j;
                    left_idx = j.saturating_sub(1);
                    break;
                }
            }
            
            if left_idx == right_idx {
                baseline[i] = baseline_sparse[left_idx];
            } else {
                // Linear interpolation
                let left_val = baseline_sparse[left_idx];
                let right_val = baseline_sparse[right_idx];
                let left_pos = indices[left_idx] as f64;
                let right_pos = indices[right_idx] as f64;
                let pos = i as f64;
                
                if right_pos == left_pos {
                    baseline[i] = left_val;
                } else {
                    baseline[i] = left_val + (right_val - left_val) * (pos - left_pos) / (right_pos - left_pos);
                }
            }
        }
    } else {
        // Direct computation for smaller signals
        for center in 0..n_samples {
            let start = std::cmp::max(0, center as isize - half_win as isize) as usize;
            let end = std::cmp::min(n_samples, center + half_win);
            if start >= end {
                if center > 0 {
                    baseline[center] = baseline[center - 1];
                }
                continue;
            }
            
            let window = &masked_signal[start..end];
            let valid: Vec<f64> = window.iter().filter(|x| !x.is_nan()).cloned().collect();
            if valid.len() > 0 {
                // Calculate percentile
                let mut sorted = valid.clone();
                sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
                let index = ((percentile / 100.0) * (sorted.len() as f64 - 1.0)).round() as usize;
                baseline[center] = sorted[index];
            } else if center > 0 {
                baseline[center] = baseline[center - 1];
            }
        }
    }
    
    // Smooth the baseline to remove discontinuities
    let mut smooth_window = std::cmp::max(3, window_samples / 10);
    if smooth_window % 2 == 0 {
        smooth_window += 1;
    }
    
    // Only smooth if we have enough points
    if n_samples >= smooth_window {
        // Create a smoothed version
        let mut smoothed = baseline.clone();
        let half_smooth = smooth_window / 2;
        
        for i in 0..n_samples {
            let start = std::cmp::max(0, i as isize - half_smooth as isize) as usize;
            let end = std::cmp::min(n_samples, i + half_smooth + 1);
            let window = &baseline[start..end];
            let sum: f64 = window.iter().sum();
            let count = window.len() as f64;
            if count > 0.0 {
                smoothed[i] = sum / count;
            }
        }
        baseline = smoothed;
    }
    
    baseline
}