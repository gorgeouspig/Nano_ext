"""Power Spectral Density analysis for nanopore signal diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

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
    """

    frequencies: np.ndarray
    psd: np.ndarray
    units: str = "pA²/Hz"
    f_3db_estimate: Optional[float] = None


def compute_psd(
    signal_data,
    segment_sec: float = 1.0,
    overlap: float = 0.5,
    window: str = "hann",
) -> PSDResult:
    """Compute the power spectral density using Welch's method.

    Parameters
    ----------
    signal_data : SignalData
    segment_sec : float
        Length of each Welch segment in seconds.  Automatically clamped to
        ``duration / 4`` for short recordings.
    overlap : float
        Fractional overlap between consecutive segments (0–1).
    window : str
        Window function passed to ``scipy.signal.welch``.

    Returns
    -------
    PSDResult
    """
    from scipy.signal import welch

    sr = signal_data.sampling_rate
    effective_seg = min(segment_sec, signal_data.duration_sec / 4)
    nperseg = max(64, int(sr * effective_seg))
    noverlap = int(nperseg * overlap)

    freqs, psd = welch(
        signal_data.signal.astype(float),
        fs=sr,
        nperseg=nperseg,
        noverlap=noverlap,
        window=window,
    )
    units_label = f"{signal_data.units or 'pA'}²/Hz"
    result = PSDResult(frequencies=freqs, psd=psd, units=units_label)
    result.f_3db_estimate = estimate_filter_cutoff(result)
    return result


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
    return float(freqs[low_end + below[0]])


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


def make_psd_figure(
    psd_result: PSDResult,
    control_psd: Optional[PSDResult] = None,
):
    """Build a log-log PSD figure using plotly.

    Parameters
    ----------
    psd_result : PSDResult
        Sample PSD to plot.
    control_psd : PSDResult, optional
        Control PSD overlaid in orange.

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

    freqs = psd_result.frequencies
    psd = psd_result.psd
    mask = (freqs > 0) & (psd > 0)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=freqs[mask], y=psd[mask],
        mode="lines", name="Sample",
        line=dict(color="#4C72B0", width=1.2),
        hovertemplate="f=%{x:.1f} Hz<br>PSD=%{y:.3e}<extra></extra>",
    ))

    if control_psd is not None:
        cm = (control_psd.frequencies > 0) & (control_psd.psd > 0)
        fig.add_trace(go.Scatter(
            x=control_psd.frequencies[cm], y=control_psd.psd[cm],
            mode="lines", name="Control",
            line=dict(color="#DD8452", width=1.2, dash="dash"),
            hovertemplate="f=%{x:.1f} Hz<br>PSD=%{y:.3e}<extra></extra>",
        ))

    if psd_result.f_3db_estimate is not None:
        fig.add_vline(
            x=psd_result.f_3db_estimate,
            line_color="#C44E52", line_dash="dot", line_width=1.5,
            annotation_text=f"-3 dB ≈ {psd_result.f_3db_estimate:.0f} Hz",
            annotation_font_size=10,
        )

    fig.update_layout(
        xaxis_type="log",
        yaxis_type="log",
        xaxis_title="Frequency (Hz)",
        yaxis_title=psd_result.units,
        template="plotly_white",
        height=380,
        margin=dict(l=70, r=20, t=50, b=50),
        title="Power Spectral Density",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig
