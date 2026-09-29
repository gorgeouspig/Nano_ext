"""Power Spectral Density analysis for nanopore signal diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np


@dataclass
class PSDResult:
    """Output of :func:`compute_psd`.

    Attributes
    ----------
    frequencies : np.ndarray
        Frequency bins in Hz.
    psd : np.ndarray
        Power spectral density in *units*²/Hz.
    units : str
        Unit label (e.g. ``"pA²/Hz"``).
    f_3db_estimate : float or None
        Estimated -3 dB cutoff frequency from :func:`estimate_filter_cutoff`,
        or None if the roll-off could not be identified.
    sampling_rate, segment_sec : float
        Sampling rate and Welch segment length (frequency resolution is
        ``1 / segment_sec``).
    n_segments : int
        Number of Welch segments averaged.
    duration_used_sec, duration_total_sec : float
        Signal time that entered the estimate, out of the time available
        (they differ when events are excluded or the data are subsampled).
    """

    frequencies: np.ndarray
    psd: np.ndarray
    units: str = "pA²/Hz"
    f_3db_estimate: Optional[float] = None
    sampling_rate: Optional[float] = None
    segment_sec: Optional[float] = None
    n_segments: int = 0
    duration_used_sec: float = 0.0
    duration_total_sec: float = 0.0


def compute_psd(
    signal_data,
    segment_sec: float = 1.0,
    overlap: float = 0.5,
    window: str = "hann",
    exclude: Optional[Sequence[tuple[int, int]]] = None,
    max_duration_sec: Optional[float] = None,
) -> PSDResult:
    """Compute the power spectral density using Welch's method.

    Parameters
    ----------
    signal_data : SignalData
    segment_sec : float
        Length of each Welch segment in seconds.  Automatically clamped to
        ``duration / 4`` for short recordings, and shortened when *exclude*
        leaves only short clean stretches.
    overlap : float
        Fractional overlap between consecutive segments (0–1).
    window : str
        Window function passed to ``scipy.signal.welch``.
    exclude : sequence of (start_idx, end_idx), optional
        Sample-index intervals to leave out (e.g. detected events), so the
        spectrum describes the open-pore noise.  Segments never straddle an
        excluded interval or a join between concatenated analysis ranges.
    max_duration_sec : float, optional
        Use at most this much signal (clean stretches spread evenly over the
        recording) to bound the run time on long recordings.

    Returns
    -------
    PSDResult
    """
    from scipy.signal import welch

    sr = float(signal_data.sampling_rate)
    x = signal_data.signal
    n = len(x)
    runs = _clean_runs(n, exclude, getattr(signal_data, "index_map", None))
    lengths = np.array([b - a for a, b in runs], dtype=np.int64)
    total = int(lengths.sum())

    nperseg = max(64, int(sr * min(segment_sec, max(total, 1) / sr / 4)))
    if exclude:
        # Shorten segments until at least half of the clean signal fits into them.
        while nperseg > 256 and (lengths // nperseg * nperseg).sum() < 0.5 * total:
            nperseg //= 2
    usable = [(a, b) for a, b in runs if b - a >= nperseg]
    if not usable:  # nothing long enough: fall back to the longest stretch
        a, b = runs[int(np.argmax(lengths))] if runs else (0, n)
        usable, nperseg = [(a, b)], max(8, b - a)
    if max_duration_sec is not None:
        budget = int(max_duration_sec * sr)
        used = sum(b - a for a, b in usable)
        if used > budget:
            chunk = max(4 * nperseg, budget // 64)  # split long stretches so the pick spreads
            usable = [(c, min(c + chunk, b)) for a, b in usable for c in range(a, b, chunk)
                      if min(c + chunk, b) - c >= nperseg]
            used = sum(b - a for a, b in usable)
            step = used / budget
            picked, acc, nxt = [], 0.0, 0.0
            for a, b in usable:  # every step-th sample's run, keeps the spread
                if acc >= nxt:
                    picked.append((a, b))
                    nxt += (b - a) * step
                acc += b - a
            usable = picked

    noverlap = int(nperseg * overlap)
    acc_psd, weight, n_seg, used = None, 0, 0, 0
    for a, b in usable:
        seg = np.asarray(x[a:b], dtype=np.float64)
        k = 1 + max(0, (len(seg) - nperseg) // (nperseg - noverlap))
        freqs, p = welch(seg, fs=sr, nperseg=nperseg, noverlap=noverlap, window=window)
        acc_psd = p * k if acc_psd is None else acc_psd + p * k
        weight += k
        n_seg += k
        used += len(seg)
    psd = acc_psd / weight
    units_label = f"{signal_data.units or 'pA'}²/Hz"
    result = PSDResult(frequencies=freqs, psd=psd, units=units_label, sampling_rate=sr,
                       segment_sec=nperseg / sr, n_segments=n_seg,
                       duration_used_sec=used / sr, duration_total_sec=n / sr)
    result.f_3db_estimate = estimate_filter_cutoff(result)
    return result


def _clean_runs(n: int, exclude, index_map) -> list[tuple[int, int]]:
    """Index intervals of ``[0, n)`` outside *exclude* and not across range joins."""
    blocked = sorted((min(n, max(0, int(a))), min(n, max(0, int(b)))) for a, b in (exclude or ()) if b > a)
    runs, pos = [], 0
    for a, b in blocked:
        if a > pos:
            runs.append((pos, a))
        pos = max(pos, b)
    if pos < n:
        runs.append((pos, n))
    if index_map is not None and len(index_map) == n and n > 1:
        joins = np.flatnonzero(np.diff(index_map) != 1) + 1
        split = []
        for a, b in runs:
            inner = joins[(joins > a) & (joins < b)]
            bounds = [a, *inner.tolist(), b]
            split += list(zip(bounds[:-1], bounds[1:]))
        runs = split
    return runs


def estimate_filter_cutoff(psd_result: PSDResult) -> Optional[float]:
    """Estimate the effective -3 dB cutoff by finding where power halves.

    The low-frequency median power (DC to 10 % of max frequency) is used as
    the reference baseline.  Returns None when the roll-off cannot be found.
    """
    freqs = psd_result.frequencies
    psd = psd_result.psd
    if len(freqs) < 8:
        return None

    low_end = max(2, int(len(freqs) * 0.1))
    baseline = np.median(psd[1:low_end])  # skip DC bin
    if baseline <= 0:
        return None

    threshold = baseline / 2.0
    # Search for the -3 dB point only beyond the low-frequency reference region
    below = np.where(psd[low_end:] < threshold)[0]
    if len(below) == 0:
        return None
    f = float(freqs[low_end + below[0]])
    if f >= 0.95 * freqs[-1]:  # only the anti-aliasing edge: no roll-off in band
        return None
    return f


def compare_psd(sample_psd: PSDResult, control_psd: PSDResult) -> np.ndarray:
    """Return element-wise power ratio sample / control.

    Both PSDs must have the same number of frequency bins.

    Returns
    -------
    np.ndarray
        Ratio array (NaN where control power is zero).
    """
    if len(sample_psd.frequencies) != len(control_psd.frequencies):
        raise ValueError(
            "sample and control PSDs must have the same frequency bins. "
            "Compute both with the same segment_sec and overlap."
        )
    denom = np.where(control_psd.psd > 0, control_psd.psd, np.nan)
    return sample_psd.psd / denom


_BANDS = ((1.0, 10.0), (10.0, 100.0), (100.0, 1e3), (1e3, 1e4), (1e4, 1e5), (1e5, 1e6))


def cumulative_rms(psd_result: PSDResult) -> np.ndarray:
    """RMS noise integrated from the lowest non-DC bin up to each frequency.

    ``cumulative_rms(p)[-1]`` is the total noise of the spectrum; the value
    at *f* is the noise a low-pass filter at *f* would leave (ideal brick wall).
    """
    f, p = psd_result.frequencies, psd_result.psd
    df = np.gradient(f) if len(f) > 1 else np.ones_like(f)
    power = np.where(f > 0, p, 0.0) * df
    return np.sqrt(np.cumsum(power))


def find_spectral_peaks(psd_result: PSDResult, min_ratio: float = 8.0, max_peaks: int = 5,
                        mains=(50.0, 60.0)) -> list[dict]:
    """Narrow lines standing out of the surrounding spectrum (mains hum etc.).

    Returns dicts ``{"frequency", "ratio", "label"}`` sorted by ratio, where
    ratio is the bin power over the median of its neighbourhood.
    """
    from scipy.ndimage import median_filter

    f, p = psd_result.frequencies, psd_result.psd
    n = len(f)
    if n < 16:
        return []
    ref = median_filter(p, size=25, mode="nearest")
    local_max = np.zeros(n, bool)
    local_max[2:-1] = (p[2:-1] >= p[1:-2]) & (p[2:-1] >= p[3:]) & (p[2:-1] > 0)
    ratio = np.where(ref > 0, p / np.where(ref > 0, ref, 1.0), 0.0)
    cand = np.flatnonzero(local_max & (ratio >= min_ratio))
    peaks = sorted(((float(ratio[i]), int(i)) for i in cand), reverse=True)
    df = f[1] - f[0]
    out = []
    for ratio, i in sorted(peaks, reverse=True):
        if any(abs(f[i] - o["frequency"]) <= 2 * df for o in out):
            continue
        label = ""
        for base in mains:
            k = round(f[i] / base)
            if 1 <= k <= 10 and abs(f[i] - k * base) <= max(df, 1.0):
                label = f"mains {base:.0f} Hz" + (f" × {k}" if k > 1 else "")
                break
        out.append({"frequency": float(f[i]), "ratio": float(ratio), "label": label})
        if len(out) >= max_peaks:
            break
    return out


def noise_summary(psd_result: PSDResult, cutoffs=(1e3, 1e4, 1e5)) -> dict:
    """Numbers that characterise a noise spectrum.

    Returns
    -------
    dict with
        ``rms_total`` – total RMS noise (units, e.g. pA);
        ``rms_below`` – ``[(f, rms)]``: RMS noise up to each frequency in *cutoffs*
        that lies within the spectrum;
        ``bands`` – ``[(f_lo, f_hi, median_psd, rms)]`` per decade;
        ``peaks`` – output of :func:`find_spectral_peaks`;
        ``resolution_hz`` – frequency resolution.
    """
    f, p = psd_result.frequencies, psd_result.psd
    cum = cumulative_rms(psd_result)
    fmax = f[-1] if len(f) else 0.0
    rms_below = [(c, float(np.interp(c, f, cum))) for c in cutoffs if c < fmax]
    bands = []
    for lo, hi in _BANDS:
        m = (f >= lo) & (f < hi) & (f > 0)
        if m.sum() >= 2:
            band_rms = float(np.sqrt(max(np.interp(min(hi, fmax), f, cum) ** 2 - np.interp(lo, f, cum) ** 2, 0.0)))
            bands.append((lo, min(hi, float(fmax)), float(np.median(p[m])), band_rms))
    return {
        "rms_total": float(cum[-1]) if len(cum) else float("nan"),
        "rms_below": rms_below,
        "bands": bands,
        "peaks": find_spectral_peaks(psd_result),
        "resolution_hz": float(f[1] - f[0]) if len(f) > 1 else float("nan"),
    }


def make_psd_figure(
    psd_result: PSDResult,
    control_psd: Optional[PSDResult] = None,
    filter_cutoff: Optional[float] = None,
    peaks: Optional[list] = None,
    show_cumulative: bool = True,
):
    """Build a log-log PSD figure using plotly.

    Parameters
    ----------
    psd_result : PSDResult
        Sample PSD to plot.
    control_psd : PSDResult, optional
        Control PSD overlaid in orange.
    filter_cutoff : float, optional
        Low-pass cutoff used by the analysis, drawn as a vertical line.
    peaks : list of dict, optional
        Output of :func:`find_spectral_peaks`, marked on the curve.
    show_cumulative : bool
        Add the integrated RMS noise (right axis).

    Returns
    -------
    plotly.graph_objects.Figure
    """
    try:
        import plotly.graph_objects as go
    except ImportError:
        raise ImportError(
            "plotly is required for PSD figures.\n"
            'Install it with:  pip install "nano_ext[notebook]"'
        )

    unit = psd_result.units.split("²")[0] or "pA"
    freqs = psd_result.frequencies
    psd = psd_result.psd
    mask = (freqs > 0) & (psd > 0)
    hover = "%{x:.4~s}Hz<br>%{y:.3e} " + psd_result.units + "<extra>%{fullData.name}</extra>"

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=freqs[mask], y=psd[mask],
        mode="lines", name="Sample",
        line=dict(color="#4C72B0", width=1.2),
        hovertemplate=hover,
    ))

    if control_psd is not None:
        cm = (control_psd.frequencies > 0) & (control_psd.psd > 0)
        fig.add_trace(go.Scatter(
            x=control_psd.frequencies[cm], y=control_psd.psd[cm],
            mode="lines", name="Control",
            line=dict(color="#DD8452", width=1.2, dash="dash"),
            hovertemplate=hover,
        ))

    if show_cumulative and mask.any():
        cum = cumulative_rms(psd_result)
        fig.add_trace(go.Scatter(
            x=freqs[mask], y=cum[mask], yaxis="y2",
            mode="lines", name=f"Integrated RMS ({unit})",
            line=dict(color="#55A868", width=1.5),
            hovertemplate="up to %{x:.4~s}Hz: %{y:.3g} " + unit + " RMS<extra></extra>",
        ))

    if peaks:
        pk = [q for q in peaks if q["frequency"] > 0]
        if pk:
            idx = [int(np.argmin(np.abs(freqs - q["frequency"]))) for q in pk]
            fig.add_trace(go.Scatter(
                x=[freqs[i] for i in idx], y=[psd[i] for i in idx],
                mode="markers+text", name="Peaks",
                marker=dict(symbol="triangle-down", size=9, color="#C44E52"),
                text=[q["label"] or f"{q['frequency']:.4g} Hz" for q in pk],
                textposition="top center", textfont=dict(size=10, color="#C44E52"),
                hovertemplate="%{x:.4~s}Hz · %{customdata:.0f}× local level<extra></extra>",
                customdata=[q["ratio"] for q in pk],
            ))

    def vline(x, text, color, dash):
        fig.add_shape(type="line", x0=x, x1=x, y0=0, y1=1, xref="x", yref="paper",
                      line=dict(color=color, dash=dash, width=1.5))
        # annotation positions on a log axis are given in log10 units
        fig.add_annotation(x=float(np.log10(x)), y=1, xref="x", yref="paper", text=text,
                           showarrow=False, xanchor="right", yanchor="top",
                           font=dict(size=10, color=color), bgcolor="rgba(255,255,255,0.8)")

    if filter_cutoff:
        vline(filter_cutoff, f"analysis low-pass {filter_cutoff / 1e3:.3g} kHz", "#8172B2", "dash")
    if psd_result.f_3db_estimate is not None:
        vline(psd_result.f_3db_estimate, f"−3 dB ≈ {psd_result.f_3db_estimate:.0f} Hz", "#C44E52", "dot")

    title = "Power spectral density"
    if psd_result.segment_sec:
        title += (f"  ·  {psd_result.n_segments} segments × {psd_result.segment_sec * 1e3:.4g} ms"
                  f"  (resolution {1 / psd_result.segment_sec:.3g} Hz)")
    fig.update_layout(
        xaxis=dict(type="log", title="Frequency (Hz)", exponentformat="SI"),
        yaxis=dict(type="log", title=f"PSD ({psd_result.units})", exponentformat="power"),
        yaxis2=dict(title=f"Integrated RMS noise ({unit})", overlaying="y", side="right",
                    rangemode="tozero", showgrid=False, color="#55A868"),
        template="plotly_white",
        height=420,
        margin=dict(l=80, r=80, t=60, b=50),
        title=dict(text=title, font=dict(size=14)),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig
