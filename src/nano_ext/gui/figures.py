"""Plotly figures for the GUI (no Dash dependency)."""

from __future__ import annotations

from typing import Optional

import numpy as np
import plotly.graph_objects as go

# Palette (colour-blind safe, readable on white).
C_SIGNAL = "#3B6EA8"
C_RAW = "#9AA7B8"
C_BASELINE = "#E08A2E"
C_THRESHOLD = "#C0392B"
C_EVENT = "#C0392B"
C_EXCLUDE = "rgba(192, 57, 43, 0.12)"
C_KEEP = "rgba(46, 134, 87, 0.10)"
CLUSTER_COLORS = ["#3B6EA8", "#E08A2E", "#2E8657", "#8E5BB5", "#C0392B",
                  "#1B9AAA", "#B8860B", "#6C757D", "#D35FA6", "#4E6E3A"]

_LAYOUT = dict(
    template="plotly_white",
    margin=dict(l=60, r=20, t=30, b=45),
    legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0),
    font=dict(size=12),
)


def empty_figure(message: str = "") -> go.Figure:
    fig = go.Figure(layout=_LAYOUT)
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    if message:
        fig.add_annotation(text=message, showarrow=False, font=dict(size=14, color="#6C757D"),
                           xref="paper", yref="paper", x=0.5, y=0.5)
    return fig


def waveform_figure(
    view: dict,
    units: str = "pA",
    names: Optional[dict] = None,
    exclude_ranges=(),
    keep_range=None,
    event_markers: Optional[dict] = None,
    event_spans=(),
    x_range=None,
    uirevision: str = "wave",
) -> go.Figure:
    """Main trace figure.

    Parameters
    ----------
    view : dict
        Output of :meth:`TraceIndex.view`: ``t`` plus one array per trace.
        Keys ``signal``, ``raw``, ``baseline``, ``threshold`` and
        ``threshold_up`` are styled; others get default styling.
    event_markers : dict, optional
        ``{"t": ..., "y": ..., "text": ..., "id": ...}`` for event markers.
    event_spans : iterable of (t0, t1)
        Event spans to shade (only pass the visible ones; shapes are slow).
    """
    names = names or {}
    fig = go.Figure(layout=_LAYOUT)
    styles = {
        "raw": dict(color=C_RAW, width=1),
        "signal": dict(color=C_SIGNAL, width=1),
        "baseline": dict(color=C_BASELINE, width=1.5, dash="dash"),
        "threshold": dict(color=C_THRESHOLD, width=1, dash="dot"),
        "threshold_up": dict(color=C_THRESHOLD, width=1, dash="dot"),
    }
    for key, y in view.items():
        if key in ("t", "n_raw"):
            continue
        fig.add_trace(go.Scattergl(
            x=view["t"], y=y, mode="lines",
            line=styles.get(key, dict(width=1)),
            name=names.get(key, key.replace("_", " ").title()),
            hoverinfo="skip" if key != "signal" else None,
            hovertemplate=None if key != "signal" else "t=%{x:.5f} s<br>I=%{y:.2f} " + units + "<extra></extra>",
            connectgaps=False,
        ))
    if event_markers is not None and len(event_markers.get("t", [])):
        fig.add_trace(go.Scattergl(
            x=event_markers["t"], y=event_markers["y"], mode="markers",
            marker=dict(color=C_EVENT, size=7, symbol="triangle-down",
                        line=dict(width=0.5, color="white")),
            name="Events", text=event_markers.get("text"),
            customdata=event_markers.get("id"),
            hovertemplate="%{text}<extra></extra>",
        ))
    for a, b in exclude_ranges:
        fig.add_vrect(x0=a, x1=b, fillcolor=C_EXCLUDE, line_width=0, layer="below")
    if keep_range is not None:
        fig.add_vrect(x0=keep_range[0], x1=keep_range[1], fillcolor=C_KEEP,
                      line_width=0, layer="below")
    spans = list(event_spans)
    if spans:
        # One filled trace for all spans: far cheaper than one shape each.
        ys = [np.asarray(y, dtype=float) for k, y in view.items() if k not in ("t", "n_raw")]
        finite = np.concatenate([y[np.isfinite(y)] for y in ys]) if ys else np.empty(0)
        lo, hi = (float(finite.min()), float(finite.max())) if len(finite) else (0.0, 1.0)
        xs, yv = [], []
        for a, b in spans:
            xs += [a, a, b, b, a, None]
            yv += [lo, hi, hi, lo, lo, None]
        fig.add_trace(go.Scatter(x=xs, y=yv, mode="none", fill="toself",
                                 fillcolor="rgba(192,57,43,0.10)", hoverinfo="skip",
                                 name="Event spans", showlegend=False))
    fig.update_layout(
        uirevision=uirevision,
        dragmode="zoom",
        hovermode="closest",
        xaxis_title="Time (s)",
        yaxis_title=f"Current ({units})",
    )
    if x_range is not None:
        fig.update_xaxes(range=list(x_range))
    return fig


def histogram_figure(result, sample: np.ndarray, units: str = "pA") -> go.Figure:
    """Residual histogram with the fitted mixture components and thresholds."""
    from nano_ext.gui.service import effective_thresholds

    tr = result.threshold_result
    fig = go.Figure(layout=_LAYOUT)
    lo, hi = np.percentile(sample, [0.05, 99.95])
    span = hi - lo
    lo, hi = lo - 0.05 * span, hi + 0.05 * span
    counts, edges = np.histogram(sample, bins=200, range=(lo, hi), density=True)
    centers = 0.5 * (edges[1:] + edges[:-1])
    fig.add_trace(go.Bar(x=centers, y=counts, marker_color="#B8C4D6",
                         marker_line_width=0, name="Residual", hoverinfo="skip"))
    x = np.linspace(lo, hi, 600)
    total = np.zeros_like(x)
    for k, (m, s, w) in enumerate(zip(tr.component_means, tr.component_stds, tr.component_weights)):
        pdf = w * np.exp(-0.5 * ((x - m) / s) ** 2) / (s * np.sqrt(2 * np.pi))
        total += pdf
        fig.add_trace(go.Scatter(
            x=x, y=pdf, mode="lines",
            line=dict(width=1.2, color=CLUSTER_COLORS[k % len(CLUSTER_COLORS)]),
            name=f"Component {k}" + (" (open pore)" if k == tr.baseline_component_idx else ""),
        ))
    fig.add_trace(go.Scatter(x=x, y=total, mode="lines", line=dict(color="#222", width=1.5),
                             name="Mixture"))
    down, up = effective_thresholds(result)
    direction = result.config.event_direction.value
    if direction in ("down", "both"):
        fig.add_vline(x=down, line=dict(color=C_THRESHOLD, dash="dot"),
                      annotation_text=f"threshold {down:.2f}", annotation_position="top left")
    if direction in ("up", "both"):
        fig.add_vline(x=up, line=dict(color=C_THRESHOLD, dash="dot"),
                      annotation_text=f"threshold {up:.2f}", annotation_position="top right")
    fig.update_layout(bargap=0, xaxis_title=f"Residual current ({units})",
                      yaxis_title="Density", yaxis_type="log")
    fig.update_yaxes(range=[np.log10(max(counts[counts > 0].min(), 1e-6)) - 0.3,
                            np.log10(counts.max()) + 0.3] if (counts > 0).any() else None)
    return fig


def scatter_figure(result, units: str = "pA") -> go.Figure:
    """Dwell time vs. relative depth, coloured by population if clustered."""
    evs = result.events
    fig = go.Figure(layout=_LAYOUT)
    if not evs:
        return empty_figure("No events")
    dur = np.array([e.duration * 1e3 for e in evs])
    rel = np.array([e.relative_depth for e in evs])
    ids = np.arange(len(evs))
    cl = np.array([e.cluster_id if e.cluster_id is not None else -1 for e in evs])
    groups = sorted(set(cl.tolist()))
    for g in groups:
        m = cl == g
        name = "Events" if g < 0 else f"Population {g}"
        color = C_SIGNAL if g < 0 else CLUSTER_COLORS[g % len(CLUSTER_COLORS)]
        fig.add_trace(go.Scattergl(
            x=dur[m], y=rel[m], mode="markers", name=name,
            marker=dict(size=6, color=color, opacity=0.75, line=dict(width=0)),
            customdata=ids[m],
            hovertemplate="event %{customdata}<br>%{x:.3f} ms<br>ΔI/I₀ = %{y:.3f}<extra></extra>",
        ))
    fig.update_layout(xaxis_title="Dwell time (ms)", yaxis_title="Relative depth ΔI/I₀",
                      xaxis_type="log", clickmode="event")
    return fig


def dwell_figure(result, stats: Optional[dict] = None) -> go.Figure:
    """Dwell-time histogram (log bins) with the fitted exponential mixture."""
    evs = result.events
    if len(evs) < 2:
        return empty_figure("Not enough events")
    d = np.array([e.duration for e in evs]) * 1e3
    fig = go.Figure(layout=_LAYOUT)
    bins = np.logspace(np.log10(d.min()), np.log10(d.max() * 1.0001), 40)
    counts, edges = np.histogram(d, bins=bins)
    widths = np.diff(edges)
    centers = np.sqrt(edges[1:] * edges[:-1])
    fig.add_trace(go.Bar(x=centers, y=counts / (widths * len(d)), width=widths,
                         marker_color="#B8C4D6", marker_line_width=0, name="Events"))
    dw = (stats or {}).get("all", {}).get("dwell_time")
    if dw:
        t_min = dw["t_min"] * 1e3
        x = np.logspace(np.log10(max(d.min(), 1e-6)), np.log10(d.max()), 400)
        pdf = np.zeros_like(x)
        for tau, w in zip(dw["tau_mean"], dw["weight_mean"]):
            tau_ms = tau * 1e3
            pdf += w * np.where(x >= t_min, np.exp(-(x - t_min) / tau_ms) / tau_ms, 0.0)
        fig.add_trace(go.Scatter(x=x, y=pdf, mode="lines", line=dict(color=C_THRESHOLD, width=2),
                                 name=f"{dw['n_components']}-exponential fit"))
    fig.update_layout(xaxis_type="log", yaxis_type="log", bargap=0,
                      xaxis_title="Dwell time (ms)", yaxis_title="Probability density (1/ms)")
    return fig


def event_figure(result, index: int, padding_ms: float = 0.5) -> go.Figure:
    from nano_ext.outputs.event_viewer import make_event_figure
    fig = make_event_figure(result, index, padding_ms=padding_ms)
    fig.update_layout(template="plotly_white", height=None, autosize=True,
                      margin=dict(l=60, r=20, t=80, b=45),
                      title=dict(x=0, xanchor="left", y=0.98, yanchor="top"),
                      legend=dict(orientation="h", x=0, xanchor="left", y=1.02, yanchor="bottom"))
    return fig
