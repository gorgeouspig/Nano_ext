"""Per-event waveform visualisation.

Provides zoomed figures for individual events and a grid-view for
side-by-side comparison of multiple events.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def _require_plotly():
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
        return go, make_subplots
    except ImportError:
        raise ImportError(
            "plotly is required for event figures.\n"
            'Install it with:  pip install "nano_ext[notebook]"'
        )


def _event_slice(result, event_index: int, padding_ms: float):
    """Return array slice indices for an event with padding."""
    ev = result.events[event_index]
    t_arr = result.signal_data.time  # absolute file times (s)
    padding_s = padding_ms / 1000.0
    i_start = int(np.searchsorted(t_arr, ev.start_time - padding_s))
    i_end = int(np.searchsorted(t_arr, ev.end_time + padding_s))
    i_start = max(0, i_start)
    i_end = min(len(t_arr), i_end)
    return ev, i_start, i_end, t_arr


def _eff_thresholds(result):
    """Compute effective up/down thresholds matching detect_events logic."""
    tr = result.threshold_result
    raw = tr.threshold
    if tr.baseline_component_idx is not None and tr.component_stds is not None:
        op_mean = tr.component_means[tr.baseline_component_idx]
        op_std = tr.component_stds[tr.baseline_component_idx]
        return min(raw, op_mean - 5.0 * op_std), max(abs(raw), op_mean + 5.0 * op_std)
    return raw, abs(raw)


def make_event_figure(
    result,
    event_index: int,
    padding_ms: float = 0.2,
) -> "plotly.graph_objects.Figure":
    """Build a two-panel plotly figure zoomed into a single event.

    Top panel: filtered signal, local baseline, and threshold line(s).
    Bottom panel: baseline-corrected residual with sub-level boundaries.

    Parameters
    ----------
    result : PipelineResult
        Output of :func:`~nano_ext.pipeline.run_pipeline`.
    event_index : int
        Zero-based index into ``result.events``.
    padding_ms : float
        Padding added before and after the event in milliseconds.

    Returns
    -------
    plotly.graph_objects.Figure
    """
    go, make_subplots = _require_plotly()
    from nano_ext.models import EventDirection

    if not result.events:
        return go.Figure()
    if event_index >= len(result.events):
        raise IndexError(f"event_index {event_index} out of range ({len(result.events)} events)")

    ev, i_start, i_end, t_arr = _event_slice(result, event_index, padding_ms)
    units = result.signal_data.units or "pA"

    # Time axis: ms relative to event start
    t_rel = (t_arr[i_start:i_end] - ev.start_time) * 1000
    sig = result.filtered_signal[i_start:i_end].astype(float)
    bl = result.baseline_result.local_baseline[i_start:i_end]
    res = result.baseline_result.residual[i_start:i_end]

    eff_down, eff_up = _eff_thresholds(result)
    direction = ev.direction

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.6, 0.4],
    )

    # --- Top: signal + baseline + threshold ---
    fig.add_trace(go.Scatter(
        x=t_rel, y=sig,
        mode="lines", line=dict(color="#4C72B0", width=1.0),
        name="Filtered signal",
        hovertemplate="t=%{x:.3f} ms<br>I=%{y:.4f} " + units + "<extra></extra>",
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=t_rel, y=bl,
        mode="lines", line=dict(color="#DD8452", width=1.5, dash="dash"),
        name="Baseline", hoverinfo="skip",
    ), row=1, col=1)

    if direction in (EventDirection.DOWN, EventDirection.BOTH):
        fig.add_trace(go.Scatter(
            x=t_rel, y=bl + eff_down,
            mode="lines", line=dict(color="#C44E52", width=1.0, dash="dot"),
            name="Threshold (↓)", hoverinfo="skip",
        ), row=1, col=1)
    if direction in (EventDirection.UP, EventDirection.BOTH):
        fig.add_trace(go.Scatter(
            x=t_rel, y=bl + eff_up,
            mode="lines", line=dict(color="#8172B2", width=1.0, dash="dot"),
            name="Threshold (↑)", hoverinfo="skip",
        ), row=1, col=1)

    # Event region shading (top panel)
    ev_color = "rgba(196,78,82,0.10)" if direction.value == "down" else "rgba(129,114,178,0.10)"
    ev_end_rel = ev.duration * 1000
    fig.add_vrect(x0=0, x1=ev_end_rel, fillcolor=ev_color, opacity=1.0,
                  layer="below", line_width=0, row=1, col=1)

    # --- Bottom: residual + sublevel boundaries ---
    fig.add_trace(go.Scatter(
        x=t_rel, y=res,
        mode="lines", line=dict(color="#4C72B0", width=1.0),
        name="Residual", showlegend=False,
        hovertemplate="t=%{x:.3f} ms<br>res=%{y:.4f} " + units + "<extra></extra>",
    ), row=2, col=1)

    fig.add_hline(y=0, line_color="#aaa", line_dash="dot", line_width=0.8, row=2, col=1)

    # Horizontal threshold lines on residual panel
    if direction in (EventDirection.DOWN, EventDirection.BOTH):
        fig.add_hline(y=eff_down, line_color="#C44E52", line_dash="dot",
                      line_width=0.8, row=2, col=1)
    if direction in (EventDirection.UP, EventDirection.BOTH):
        fig.add_hline(y=eff_up, line_color="#8172B2", line_dash="dot",
                      line_width=0.8, row=2, col=1)

    # Sublevel boundaries (both panels)
    for sl in ev.sublevels:
        x_sl = (sl.start_time - ev.start_time) * 1000
        for r in (1, 2):
            fig.add_vline(x=x_sl, line_color="#555", line_dash="dash",
                          line_width=1.0, row=r, col=1)

    title = (
        f"Event #{event_index + 1}  |  "
        f"t = {ev.start_time * 1000:.3f} ms  |  "
        f"depth = {ev.depth:.4f} {units}  |  "
        f"dur = {ev.duration * 1000:.3f} ms"
        + (f"  |  {ev.n_levels} levels" if ev.is_multilevel else "")
    )
    fig.update_layout(
        height=420,
        title=dict(text=title, font_size=13),
        margin=dict(l=60, r=20, t=55, b=50),
        hovermode="x unified",
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    xanchor="right", x=1),
    )
    fig.update_xaxes(title_text="Time from event start (ms)", row=2, col=1)
    fig.update_yaxes(title_text=f"Current ({units})", row=1, col=1)
    fig.update_yaxes(title_text=f"Residual ({units})", row=2, col=1)

    return fig


def make_event_grid(
    result,
    n_cols: int = 3,
    max_events: int = 12,
    padding_ms: float = 0.1,
) -> "plotly.graph_objects.Figure":
    """Grid of per-event residual traces for side-by-side comparison.

    Parameters
    ----------
    result : PipelineResult
    n_cols : int
        Number of columns in the grid.
    max_events : int
        Maximum number of events to show (first *max_events* by time).
    padding_ms : float
        Padding around each event.

    Returns
    -------
    plotly.graph_objects.Figure
    """
    go, make_subplots = _require_plotly()

    events = result.events[:max_events]
    n = len(events)
    if n == 0:
        return go.Figure()

    n_rows = max(1, (n + n_cols - 1) // n_cols)
    units = result.signal_data.units or "pA"

    titles = []
    for i, ev in enumerate(events):
        titles.append(
            f"#{i + 1}  {ev.duration * 1000:.2f} ms  {ev.depth:.2f} {units}"
        )
    # Pad to full grid
    titles += [""] * (n_rows * n_cols - n)

    fig = make_subplots(
        rows=n_rows, cols=n_cols,
        subplot_titles=titles,
        shared_yaxes=False,
        horizontal_spacing=0.08,
        vertical_spacing=0.15,
    )

    for i, ev in enumerate(events):
        row = i // n_cols + 1
        col = i % n_cols + 1
        _, i_start, i_end, t_arr = _event_slice(result, i, padding_ms)
        t_rel = (t_arr[i_start:i_end] - ev.start_time) * 1000
        res = result.baseline_result.residual[i_start:i_end]

        color = "#C44E52" if ev.direction.value == "down" else "#8172B2"
        fig.add_trace(go.Scatter(
            x=t_rel, y=res,
            mode="lines", line=dict(color=color, width=0.9),
            showlegend=False,
            hovertemplate="t=%{x:.3f} ms<br>res=%{y:.3f} " + units + "<extra></extra>",
        ), row=row, col=col)

        # Zero baseline
        fig.add_hline(y=0, line_color="#ccc", line_dash="dot",
                      line_width=0.5, row=row, col=col)

        # Sublevel boundaries
        for sl in ev.sublevels:
            x_sl = (sl.start_time - ev.start_time) * 1000
            fig.add_vline(x=x_sl, line_color="#888", line_dash="dash",
                          line_width=0.8, row=row, col=col)

    fig.update_layout(
        height=220 * n_rows,
        template="plotly_white",
        margin=dict(l=40, r=20, t=40, b=40),
        showlegend=False,
    )
    return fig


def dump_event_waveform(
    result,
    event_index: int,
    path,
    fmt: str = "npz",
    padding_ms: float = 0.2,
) -> None:
    """Export the waveform of a single event to a file.

    Parameters
    ----------
    result : PipelineResult
    event_index : int
    path : str or Path
        Output file path (extension is appended if not present).
    fmt : str
        ``"npz"`` (numpy compressed) or ``"csv"``.
    padding_ms : float
        Padding before and after the event.
    """
    ev, i_start, i_end, t_arr = _event_slice(result, event_index, padding_ms)
    time = t_arr[i_start:i_end]
    signal = result.filtered_signal[i_start:i_end]
    baseline = result.baseline_result.local_baseline[i_start:i_end]
    residual = result.baseline_result.residual[i_start:i_end]

    path = Path(path)
    if fmt == "npz":
        if path.suffix != ".npz":
            path = path.with_suffix(".npz")
        np.savez_compressed(
            path,
            time_s=time,
            signal=signal,
            baseline=baseline,
            residual=residual,
            start_time=ev.start_time,
            end_time=ev.end_time,
            depth=ev.depth,
            duration=ev.duration,
        )
    elif fmt == "csv":
        if path.suffix != ".csv":
            path = path.with_suffix(".csv")
        import pandas as pd
        pd.DataFrame({
            "time_s": time,
            "signal": signal,
            "baseline": baseline,
            "residual": residual,
        }).to_csv(path, index=False)
    else:
        raise ValueError(f"Unknown format: {fmt!r}. Use 'npz' or 'csv'.")
