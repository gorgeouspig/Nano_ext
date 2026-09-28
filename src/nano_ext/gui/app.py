"""Dash application: ``nano-ext gui``.

A local, single-user web app.  The server keeps the loaded recording and the
analysis result in memory (:class:`GuiState`); the browser only ever receives
downsampled views, so 10-minute 250 kHz recordings stay responsive.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

import numpy as np

from nano_ext.gui import figures, service
from nano_ext.gui.downsample import TraceIndex

VIEW_POINTS = 4000          # points per trace in the main view
MAX_EVENT_SHAPES = 400      # shade event spans only when fewer are visible


class GuiState:
    """Server-side state shared by all callbacks (one user, one recording)."""

    def __init__(self, folder: Optional[str] = None):
        self.lock = threading.RLock()
        self.folder = str(Path(folder or os.getcwd()).expanduser().resolve())
        self.path: Optional[str] = None
        self.info: dict = {}
        self.sd = None            # loaded SignalData (original file coordinates)
        self.index: Optional[TraceIndex] = None
        self.control = None
        self.control_path: Optional[str] = None
        self.job = service.Job()
        self.result_version = 0

    @property
    def result(self):
        return self.job.result if self.job.status == "done" else None


# ---------------------------------------------------------------------------
# Pure helpers used by callbacks (unit-tested without Dash)
# ---------------------------------------------------------------------------

def browse_options(folder: str) -> tuple[str, list[dict]]:
    folder, dirs, files = service.list_directory(folder)
    opts = [{"label": "⬆  ..", "value": "dir:" + str(folder.parent)}]
    opts += [{"label": f"📁  {d.name}", "value": "dir:" + str(d)} for d in dirs]
    opts += [{"label": f"📄  {f.name}", "value": "file:" + str(f)} for f in files]
    return str(folder), opts


def main_view(state: GuiState, t0: Optional[float], t1: Optional[float],
              exclude_ranges=(), keep_range=None, selected_event: Optional[int] = None):
    """Build the main waveform figure for the window ``[t0, t1)``."""
    if state.sd is None:
        return figures.empty_figure("Load a recording to start"), {}
    units = state.sd.units or "pA"
    raw_view = state.index.view({"raw": state.sd.signal}, t0, t1, n_out=VIEW_POINTS)
    view = {"t": raw_view["t"], "raw": raw_view["raw"]}
    names = {"raw": "Raw signal"}
    info = {"raw_samples": raw_view["n_raw"], "points": len(raw_view["t"])}

    result = state.result
    markers = None
    spans = []
    fig_views = [view]
    if result is not None:
        idx = TraceIndex(result.signal_data)
        rv = idx.view(
            {"signal": result.filtered_signal, "baseline": result.baseline_result.local_baseline},
            t0, t1, n_out=VIEW_POINTS,
        )
        down, up = service.effective_thresholds(result)
        direction = result.config.event_direction.value
        extra = {"t": rv["t"], "signal": rv["signal"], "baseline": rv["baseline"]}
        if direction in ("down", "both"):
            extra["threshold"] = rv["baseline"] + down
        if direction in ("up", "both"):
            extra["threshold_up"] = rv["baseline"] + up
        fig_views.append(extra)
        names.update(signal="Filtered", baseline="Baseline", threshold="Threshold",
                     threshold_up="Threshold (up)")
        evs = result.events
        if evs:
            starts = np.array([e.start_time for e in evs])
            ends = np.array([e.end_time for e in evs])
            lo = -np.inf if t0 is None else t0
            hi = np.inf if t1 is None else t1
            visible = np.flatnonzero((ends >= lo) & (starts <= hi))
            markers = {
                "t": 0.5 * (starts[visible] + ends[visible]),
                "y": np.array([evs[i].mean_current for i in visible]),
                "id": visible,
                "text": [f"event {i}<br>{evs[i].duration * 1e3:.3f} ms<br>"
                         f"depth {evs[i].depth:.2f} {units}<br>{evs[i].n_levels} level(s)"
                         for i in visible],
            }
            if len(visible) <= MAX_EVENT_SHAPES:
                spans = [(starts[i], ends[i]) for i in visible]
            info["events_visible"] = int(len(visible))

    fig = figures.waveform_figure(
        view, units=units, names=names, exclude_ranges=exclude_ranges,
        keep_range=keep_range, event_markers=None, event_spans=spans,
        x_range=None if t0 is None else (t0, t1),
        uirevision=f"{state.result_version}-{t0}-{t1}",
    )
    # Analysed traces share the time axis but have their own sample times.
    import plotly.graph_objects as go
    styles = {"signal": dict(color=figures.C_SIGNAL, width=1),
              "baseline": dict(color=figures.C_BASELINE, width=1.5, dash="dash"),
              "threshold": dict(color=figures.C_THRESHOLD, width=1, dash="dot"),
              "threshold_up": dict(color=figures.C_THRESHOLD, width=1, dash="dot")}
    for extra in fig_views[1:]:
        for key, y in extra.items():
            if key == "t":
                continue
            fig.add_trace(go.Scattergl(
                x=extra["t"], y=y, mode="lines", line=styles[key], name=names[key],
                hovertemplate=("t=%{x:.5f} s<br>I=%{y:.2f} " + units + "<extra></extra>")
                if key == "signal" else None,
                hoverinfo=None if key == "signal" else "skip",
            ))
    if result is not None:
        fig.data[0].update(opacity=0.35, name="Raw signal")
    if markers is not None and len(markers["t"]):
        fig.add_trace(go.Scattergl(
            x=markers["t"], y=markers["y"], mode="markers", name="Events",
            marker=dict(color=figures.C_EVENT, size=8, symbol="triangle-down",
                        line=dict(width=0.5, color="white")),
            customdata=markers["id"], text=markers["text"],
            hovertemplate="%{text}<extra></extra>",
        ))
    if selected_event is not None and result is not None and 0 <= selected_event < len(result.events):
        ev = result.events[selected_event]
        fig.add_vrect(x0=ev.start_time, x1=ev.end_time, line=dict(color=figures.C_EVENT, width=1.5),
                      fillcolor="rgba(0,0,0,0)")
    return fig, info


def parse_relayout(relayout: Optional[dict], current: Optional[list]) -> Optional[list]:
    """New ``[t0, t1]`` view from a plotly relayout event (None = full)."""
    if not relayout:
        return current
    if relayout.get("xaxis.autorange") or relayout.get("autosize"):
        return None
    if "xaxis.range[0]" in relayout and "xaxis.range[1]" in relayout:
        return [float(relayout["xaxis.range[0]"]), float(relayout["xaxis.range[1]"])]
    if "xaxis.range" in relayout:
        a, b = relayout["xaxis.range"]
        return [float(a), float(b)]
    return current


def selection_range(selected: Optional[dict]) -> Optional[list]:
    """x-range of a box selection (``selectedData``)."""
    if not selected:
        return None
    rng = selected.get("range") or {}
    x = rng.get("x") or rng.get("x2")
    if x and len(x) == 2:
        a, b = sorted(float(v) for v in x)
        return [a, b] if b > a else None
    return None


def ranges_from_rows(rows) -> list[tuple[float, float]]:
    return [(r.get("start"), r.get("end")) for r in rows or []]


def rows_from_ranges(ranges) -> list[dict]:
    return [{"start": round(a, 6), "end": round(b, 6)} for a, b in ranges]


def status_text(job: service.Job) -> tuple[str, str]:
    """(message, css class) for the run status line."""
    if job.status == "running":
        return f"⏳ {job.step} … {job.elapsed:.0f} s", "status running"
    if job.status == "done":
        n = job.result.n_events if job.result is not None else 0
        return f"✅ Done in {job.elapsed:.1f} s — {n} events", "status done"
    if job.status == "error":
        first = job.error.splitlines()[0] if job.error else "error"
        return f"⚠️ {first}", "status error"
    return "", "status"


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def _section(title, children, open_=True):
    from dash import html
    return html.Details([html.Summary(title), html.Div(children, className="section-body")],
                        open=open_, className="section")


def _field(label, component, hint=None):
    from dash import html
    items = [html.Label(label), component]
    if hint:
        items.append(html.Div(hint, className="hint"))
    return html.Div(items, className="field")


def build_layout(state: GuiState, path: Optional[str] = None):
    from dash import dash_table, dcc, html

    s = service.DEFAULT_SETTINGS
    hmm_opts = [{"label": "Off", "value": "off"},
                {"label": "Sticky HDP-HMM (states inferred)", "value": "sticky_hdp"},
                {"label": "EM + BIC (needs hmmlearn)", "value": "bic",
                 "disabled": not service.hmm_bic_available()}]

    sidebar = html.Div([
        html.Div([html.Span("Nano_ext", className="brand"),
                  html.Span("nanopore event analysis", className="brand-sub")], className="brand-row"),

        _section("1 · Recording", [
            _field("Folder", html.Div([
                dcc.Input(id="folder", type="text", value=state.folder, debounce=True,
                          className="grow"),
            ], className="row")),
            dcc.Dropdown(id="browse", placeholder="Open a folder or a recording…",
                         clearable=False, className="browse"),
            _field("File", dcc.Input(id="path", type="text", debounce=True, value=path,
                                     placeholder="/path/to/recording.abf", className="grow")),
            html.Div(id="file-info", className="hint"),
            html.Div([
                _field("Channel", dcc.Dropdown(id="channel", clearable=False,
                                               options=[{"label": "0", "value": 0}], value=0)),
            ], id="channel-box"),
            html.Div([
                _field("Sampling rate (Hz)", dcc.Input(id="bin-sr", type="number", value=250000)),
                _field("Data type", dcc.Dropdown(id="bin-dtype", clearable=False, value="int16",
                                                 options=["int16", "int32", "float32", "float64"])),
                _field("Scale (units per count)", dcc.Input(id="bin-scale", type="number", value=1.0)),
            ], id="binary-box", style={"display": "none"}),
            html.Button("Load recording", id="load", className="btn primary"),
            dcc.Loading(html.Div(id="load-status", className="hint"), type="dot"),
        ]),

        _section("2 · Analysis range", [
            dcc.RadioItems(id="range-mode", value="all", className="radio", options=[
                {"label": " Whole recording", "value": "all"},
                {"label": " Only one range", "value": "keep"},
                {"label": " Exclude artifact ranges", "value": "exclude"},
            ]),
            html.Div("Zoom the trace or box-select (toolbar ▭), then add the range.",
                     className="hint"),
            html.Div([
                html.Button("Add selection", id="add-selection", className="btn"),
                html.Button("Add current view", id="add-view", className="btn"),
                html.Button("Clear", id="clear-ranges", className="btn ghost"),
            ], className="row wrap"),
            html.Div(id="selection-label", className="hint"),
            dash_table.DataTable(
                id="ranges", columns=[{"name": "start (s)", "id": "start", "type": "numeric"},
                                      {"name": "end (s)", "id": "end", "type": "numeric"}],
                data=[], editable=True, row_deletable=True,
                style_table={"maxHeight": "180px", "overflowY": "auto"},
                style_cell={"fontSize": 12, "padding": "4px"},
            ),
        ]),

        _section("3 · Settings", [
            _field("Event direction", dcc.RadioItems(
                id="direction", value=s["event_direction"], className="radio inline",
                options=[{"label": " Down", "value": "down"}, {"label": " Up", "value": "up"},
                         {"label": " Both", "value": "both"}])),
            dcc.Checklist(id="flags", className="checks", value=["auto_tune", "apply_filter", "sublevels"],
                          options=[{"label": " Auto-tune from noise", "value": "auto_tune"},
                                   {"label": " Apply low-pass filter", "value": "apply_filter"},
                                   {"label": " Sub-level analysis", "value": "sublevels"}]),
            html.Details([html.Summary("Filter & baseline"), html.Div([
                _field("Filter cutoff (kHz)", dcc.Input(id="cutoff", type="number", placeholder="auto"),
                       "Blank = sampling rate / 10. With the filter off, the hardware cutoff."),
                _field("Baseline window (s)", dcc.Input(id="bl-window", type="number", placeholder="auto")),
                _field("Detrend", dcc.Dropdown(id="detrend", value="none", clearable=False,
                                               options=["none", "linear", "polynomial", "spline"])),
                _field("Min. event duration (ms)", dcc.Input(id="min-dur", type="number", placeholder="auto")),
                _field("Merge gap (ms)", dcc.Input(id="merge-gap", type="number", placeholder="auto")),
                _field("Negative control file (optional)",
                       dcc.Input(id="control-path", type="text", debounce=True, placeholder="/path/to/control.abf",
                                 className="grow")),
            ], className="section-body")], className="sub"),
            html.Details([html.Summary("Methods"), html.Div([
                _field("Threshold", dcc.Dropdown(id="thr-method", value="gmm", clearable=False, options=[
                    {"label": "GMM + BIC", "value": "gmm"},
                    {"label": "Dirichlet-process GMM", "value": "dpgmm"}])),
                _field("Sub-levels", dcc.Dropdown(id="sub-method", value="gmm", clearable=False, options=[
                    {"label": "GMM + BIC", "value": "gmm"},
                    {"label": "Dirichlet-process GMM", "value": "dpgmm"},
                    {"label": "Bayesian change points (BOCPD)", "value": "bocpd"}])),
                _field("HMM per event", dcc.Dropdown(id="hmm", value="off", clearable=False, options=hmm_opts)),
                dcc.Checklist(id="extras", className="checks", value=[], options=[
                    {"label": " Cluster events into populations", "value": "cluster"},
                    {"label": " Bayesian statistics (rate, dwell times)", "value": "bayes"}]),
                _field("Worker processes", dcc.Input(id="jobs", type="number", value=-1),
                       "-1 = all CPUs"),
            ], className="section-body")], className="sub"),
        ]),

        html.Div([
            html.Button("▶  Run analysis", id="run", className="btn primary big"),
            html.Div(id="run-status", className="status"),
        ], className="run-box"),
    ], className="sidebar")

    tabs = dcc.Tabs(id="tabs", value="summary", className="tabs", children=[
        dcc.Tab(label="Summary", value="summary", children=[
            html.Pre(id="summary", className="summary"),
            dcc.Graph(id="hist", config={"displaylogo": False}, style={"height": "340px"}),
        ]),
        dcc.Tab(label="Events", value="events", children=[
            dash_table.DataTable(
                id="events", data=[], page_size=12, sort_action="native", filter_action="native",
                row_selectable="single", selected_rows=[],
                columns=[{"name": n, "id": i} for i, n in [
                    ("id", "#"), ("start_s", "start (s)"), ("duration_ms", "dwell (ms)"),
                    ("depth", "depth"), ("rel_depth", "ΔI/I₀"), ("levels", "levels"),
                    ("cluster", "population"), ("hmm_states", "HMM states"), ("direction", "dir")]],
                style_cell={"fontSize": 12, "padding": "4px 8px"},
                style_header={"fontWeight": "600"},
            ),
            dcc.Graph(id="event-detail", config={"displaylogo": False}, style={"height": "420px"}),
        ]),
        dcc.Tab(label="Scatter", value="scatter", children=[
            dcc.Graph(id="scatter", config={"displaylogo": False}, style={"height": "460px"}),
            html.Div(id="clusters-table"),
        ]),
        dcc.Tab(label="Dwell & statistics", value="stats", children=[
            dcc.Graph(id="dwell", config={"displaylogo": False}, style={"height": "380px"}),
            html.Pre(id="stats-text", className="summary"),
        ]),
        dcc.Tab(label="Noise (PSD)", value="psd", children=[
            html.Button("Compute power spectrum", id="psd-btn", className="btn"),
            dcc.Loading(dcc.Graph(id="psd", config={"displaylogo": False}, style={"height": "400px"})),
        ]),
        dcc.Tab(label="Export", value="export", children=[
            html.Div([
                html.Button("Events CSV", id="dl-events", className="btn"),
                html.Button("Sub-levels CSV", id="dl-sublevels", className="btn"),
                html.Button("Populations CSV", id="dl-clusters", className="btn"),
                html.Button("Statistics JSON", id="dl-stats", className="btn"),
            ], className="row wrap"),
            html.Div("Files are generated from the last completed run.", className="hint"),
            dcc.Download(id="download"),
        ]),
    ])

    main = html.Div([
        html.Div([
            html.Div(id="view-info", className="hint"),
            html.Button("Reset view", id="reset-view", className="btn ghost small"),
        ], className="row between"),
        dcc.Graph(id="wave", style={"height": "48vh"},
                  config={"scrollZoom": True, "displaylogo": False,
                          "modeBarButtonsToRemove": ["lasso2d", "autoScale2d"]}),
        tabs,
    ], className="main")

    stores = [
        dcc.Store(id="view-range", data=None),
        dcc.Store(id="selection", data=None),
        dcc.Store(id="data-version", data=0),
        dcc.Store(id="result-version", data=0),
        dcc.Store(id="selected-event", data=None),
        dcc.Interval(id="poll", interval=500, disabled=True),
    ]
    return html.Div([sidebar, main, *stores], className="app")


# ---------------------------------------------------------------------------
# App factory and callbacks
# ---------------------------------------------------------------------------

def create_app(folder: Optional[str] = None, path: Optional[str] = None):
    """Create the Dash app (call ``.run()`` on the result)."""
    import dash
    from dash import Input, Output, State, ctx, dcc, html, no_update

    state = GuiState(folder)
    app = dash.Dash(
        __name__, title="Nano_ext", update_title=None,
        assets_folder=str(Path(__file__).parent / "assets"),
    )
    app.layout = build_layout(state, str(Path(path).expanduser()) if path else None)
    app._nano_state = state  # for tests / debugging

    # --- Browsing ---------------------------------------------------------
    @app.callback(Output("browse", "options"), Output("folder", "value"),
                  Input("folder", "value"))
    def _browse(folder):
        try:
            resolved, opts = browse_options(folder or state.folder)
        except (NotADirectoryError, OSError):
            return [], no_update
        state.folder = resolved
        return opts, (resolved if resolved != folder else no_update)

    @app.callback(Output("folder", "value", allow_duplicate=True), Output("path", "value"),
                  Output("browse", "value"),
                  Input("browse", "value"), prevent_initial_call=True)
    def _pick(value):
        if not value:
            return no_update, no_update, no_update
        kind, target = value.split(":", 1)
        if kind == "dir":
            return target, no_update, None
        return no_update, target, None

    @app.callback(Output("file-info", "children"), Output("channel", "options"),
                  Output("channel", "value"), Output("binary-box", "style"),
                  Output("channel-box", "style"),
                  Input("path", "value"))
    def _info(path):
        if not path:
            return "", [{"label": "0", "value": 0}], 0, {"display": "none"}, {}
        try:
            info = service.recording_info(path)
        except Exception as exc:
            return f"⚠️ {exc}", [{"label": "0", "value": 0}], 0, {"display": "none"}, {}
        if info["format"] == "abf":
            names = info["channel_names"]
            opts = [{"label": f"{i}: {names[i]} ({info['channel_units'][i]})", "value": i}
                    for i in range(info["n_channels"])]
            text = (f"{info['name']} · {info['sampling_rate_hz'] / 1e3:g} kHz · "
                    f"{info['duration_sec']:.1f} s · {info['n_channels']} ch · {info['size_mb']:.0f} MB")
            return text, opts, 0, {"display": "none"}, {}
        return (f"{info['name']} · raw binary · {info['size_mb']:.0f} MB",
                [{"label": "0", "value": 0}], 0, {}, {"display": "none"})

    # --- Loading ------------------------------------------------------------
    @app.callback(Output("load-status", "children"), Output("data-version", "data"),
                  Output("view-range", "data", allow_duplicate=True),
                  Input("load", "n_clicks"),
                  State("path", "value"), State("channel", "value"), State("bin-sr", "value"),
                  State("bin-dtype", "value"), State("bin-scale", "value"),
                  State("data-version", "data"), prevent_initial_call=True)
    def _load(_, path, channel, sr, dtype, scale, version):
        if not path:
            return "Choose a file first.", no_update, no_update
        if state.job.status == "running":
            return "Wait for the running analysis to finish.", no_update, no_update
        try:
            sd = service.load_recording(path, channel=channel or 0, sampling_rate=sr,
                                        dtype=dtype or "int16", scale_factor=scale or 1.0)
        except Exception as exc:
            return f"⚠️ {exc}", no_update, no_update
        with state.lock:
            state.sd, state.path = sd, path
            state.index = TraceIndex(sd)
            state.job = service.Job()
            state.result_version += 1
        return (f"Loaded {len(sd.signal) / 1e6:.1f} M samples "
                f"({sd.duration_sec:.1f} s, {sd.units})."), (version or 0) + 1, None

    # --- View / ranges ------------------------------------------------------
    @app.callback(Output("view-range", "data"), Input("wave", "relayoutData"),
                  Input("reset-view", "n_clicks"), State("view-range", "data"),
                  prevent_initial_call=True)
    def _relayout(relayout, _, current):
        if ctx.triggered_id == "reset-view":
            return None
        new = parse_relayout(relayout, current)
        return no_update if new == current else new

    @app.callback(Output("selection", "data"), Output("selection-label", "children"),
                  Input("wave", "selectedData"), prevent_initial_call=True)
    def _select(selected):
        rng = selection_range(selected)
        if rng is None:
            return None, ""
        return rng, f"Selected {rng[0]:.4f} – {rng[1]:.4f} s"

    @app.callback(Output("ranges", "data"),
                  Input("add-selection", "n_clicks"), Input("add-view", "n_clicks"),
                  Input("clear-ranges", "n_clicks"),
                  State("selection", "data"), State("view-range", "data"),
                  State("ranges", "data"), State("range-mode", "value"),
                  prevent_initial_call=True)
    def _ranges(_a, _b, _c, selection, view, rows, mode):
        trig = ctx.triggered_id
        if trig == "clear-ranges":
            return []
        rng = selection if trig == "add-selection" else view
        if rng is None:
            if state.sd is None:
                return no_update
            rng = [state.index.t_start, state.index.t_end]
        ranges = ranges_from_rows(rows)
        ranges = [tuple(rng)] if mode == "keep" else ranges + [tuple(rng)]
        dur = state.index.t_end if state.index else float("inf")
        return rows_from_ranges(service.normalise_ranges(ranges, dur))

    @app.callback(Output("wave", "figure"), Output("view-info", "children"),
                  Input("view-range", "data"), Input("data-version", "data"),
                  Input("result-version", "data"), Input("ranges", "data"),
                  Input("range-mode", "value"), Input("selected-event", "data"))
    def _wave(view, _dv, _rv, rows, mode, selected_event):
        with state.lock:
            t0, t1 = (view or [None, None])
            ranges = []
            if state.index is not None:
                ranges = service.normalise_ranges(ranges_from_rows(rows), state.index.t_end)
            exclude = ranges if mode == "exclude" else []
            keep = ranges[0] if (mode == "keep" and ranges) else None
            fig, info = main_view(state, t0, t1, exclude, keep, selected_event)
        text = ""
        if info:
            text = f"{info['raw_samples']:,} samples in view → {info['points']:,} points drawn"
            if "events_visible" in info:
                text += f" · {info['events_visible']} events"
        return fig, text

    # --- Running --------------------------------------------------------------
    @app.callback(Output("run-status", "children"), Output("run-status", "className"),
                  Output("poll", "disabled"),
                  Input("run", "n_clicks"),
                  State("direction", "value"), State("flags", "value"), State("cutoff", "value"),
                  State("bl-window", "value"), State("detrend", "value"), State("min-dur", "value"),
                  State("merge-gap", "value"), State("thr-method", "value"), State("sub-method", "value"),
                  State("hmm", "value"), State("extras", "value"), State("jobs", "value"),
                  State("control-path", "value"), State("range-mode", "value"), State("ranges", "data"),
                  prevent_initial_call=True)
    def _run(_, direction, flags, cutoff, bl_window, detrend, min_dur, merge_gap, thr, sub,
             hmm, extras, jobs, control_path, mode, rows):
        if state.sd is None:
            return "Load a recording first.", "status error", True
        if state.job.status == "running":
            return no_update, no_update, False
        flags, extras = flags or [], extras or []
        settings = {
            "event_direction": direction, "auto_tune": "auto_tune" in flags,
            "apply_filter": "apply_filter" in flags, "filter_cutoff_khz": cutoff,
            "detrend_method": detrend, "baseline_window_sec": bl_window,
            "min_event_duration_ms": min_dur, "merge_gap_ms": merge_gap,
            "threshold_method": thr, "sublevel_method": sub, "hmm_method": hmm,
            "cluster_events": "cluster" in extras, "n_jobs": jobs if jobs is not None else -1,
        }
        try:
            config = service.build_config(settings, state.sd)
            control = None
            if control_path:
                if control_path != state.control_path:
                    state.control = service.load_recording(control_path)
                    state.control_path = control_path
                control = state.control
        except Exception as exc:
            return f"⚠️ {exc}", "status error", True
        ranges = service.normalise_ranges(ranges_from_rows(rows), state.index.t_end)
        kwargs = dict(signal_data=state.sd, config=config, control=control,
                      analyze_sublevels="sublevels" in flags, bayes_stats="bayes" in extras)
        if mode == "keep" and ranges:
            kwargs["analysis_range"] = ranges[0]
        elif mode == "exclude" and ranges:
            kwargs["exclude_ranges"] = ranges
        state.job = service.Job()
        service.start_job(state.job, **kwargs)
        msg, cls = status_text(state.job)
        return msg, cls, False

    @app.callback(Output("run-status", "children", allow_duplicate=True),
                  Output("run-status", "className", allow_duplicate=True),
                  Output("poll", "disabled", allow_duplicate=True),
                  Output("result-version", "data"),
                  Input("poll", "n_intervals"), State("result-version", "data"),
                  prevent_initial_call=True)
    def _poll(_, version):
        job = state.job
        msg, cls = status_text(job)
        if job.status == "running":
            return msg, cls, False, no_update
        if job.status == "done":
            with state.lock:
                state.result_version += 1
            return msg, cls, True, (version or 0) + 1
        return msg, cls, True, no_update

    # --- Results ----------------------------------------------------------------
    @app.callback(Output("summary", "children"), Output("hist", "figure"),
                  Output("events", "data"), Output("events", "selected_rows"),
                  Output("scatter", "figure"), Output("dwell", "figure"),
                  Output("stats-text", "children"), Output("clusters-table", "children"),
                  Input("result-version", "data"))
    def _results(_):
        result = state.result
        if result is None:
            empty = figures.empty_figure("Run an analysis to see results")
            return "", empty, [], [], empty, empty, "", None
        units = result.signal_data.units or "pA"
        summary = "\n".join(service.summary_lines(result, state.job.config))
        hist = figures.histogram_figure(result, service.residual_sample(result), units)
        rows = service.events_rows(result)
        scatter = figures.scatter_figure(result, units)
        dwell = figures.dwell_figure(result, state.job.stats)
        stats = _stats_text(state.job.stats)
        clusters = None
        if result.cluster_result is not None:
            table = result.cluster_result.summary_table().round(4)
            from dash import dash_table
            clusters = dash_table.DataTable(
                data=table.to_dict("records"),
                columns=[{"name": c, "id": c} for c in table.columns],
                style_cell={"fontSize": 12, "padding": "4px"})
        return summary, hist, rows, [], scatter, dwell, stats, clusters

    @app.callback(Output("selected-event", "data"),
                  Input("events", "selected_rows"), Input("wave", "clickData"),
                  Input("scatter", "clickData"), State("events", "data"),
                  prevent_initial_call=True)
    def _pick_event(selected_rows, wave_click, scatter_click, rows):
        trig = ctx.triggered_id
        if trig == "events":
            if not selected_rows:
                return no_update
            return int(rows[selected_rows[0]]["id"])
        click = wave_click if trig == "wave" else scatter_click
        try:
            point = click["points"][0]
            cd = point.get("customdata")
        except (TypeError, KeyError, IndexError):
            return no_update
        if cd is None:
            return no_update
        return int(cd[0] if isinstance(cd, list) else cd)

    @app.callback(Output("event-detail", "figure"), Input("selected-event", "data"),
                  Input("result-version", "data"))
    def _event_detail(index, _):
        result = state.result
        if result is None or index is None or not (0 <= index < len(result.events)):
            return figures.empty_figure("Select an event in the table, the trace or the scatter plot")
        return figures.event_figure(result, index)

    @app.callback(Output("psd", "figure"), Input("psd-btn", "n_clicks"), prevent_initial_call=True)
    def _psd(_):
        if state.sd is None:
            return figures.empty_figure("Load a recording first")
        from nano_ext.analysis.spectrum import compute_psd, make_psd_figure
        control_psd = compute_psd(state.control) if state.control is not None else None
        psd = compute_psd(state.sd)
        fig = make_psd_figure(psd, control_psd=control_psd) if control_psd is not None else make_psd_figure(psd)
        fig.update_layout(template="plotly_white")
        return fig

    @app.callback(Output("download", "data"),
                  Input("dl-events", "n_clicks"), Input("dl-sublevels", "n_clicks"),
                  Input("dl-clusters", "n_clicks"), Input("dl-stats", "n_clicks"),
                  prevent_initial_call=True)
    def _download(*_):
        result = state.result
        if result is None:
            return no_update
        stem = Path(state.path).stem if state.path else "nano_ext"
        trig = ctx.triggered_id
        if trig == "dl-events":
            return dict(content=service.events_csv(result), filename=f"{stem}_events.csv")
        if trig == "dl-sublevels":
            return dict(content=service.sublevels_csv(result), filename=f"{stem}_sublevels.csv")
        if trig == "dl-clusters":
            text = service.clusters_csv(result)
            return no_update if text is None else dict(content=text, filename=f"{stem}_clusters.csv")
        text = service.stats_json(state.job.stats)
        return no_update if text is None else dict(content=text, filename=f"{stem}_bayes_stats.json")

    return app


def _stats_text(stats: Optional[dict]) -> str:
    if not stats:
        return "Enable “Bayesian statistics” in Settings → Methods to estimate rates and dwell times."
    lines = []
    for label, block in [("All events", stats["all"])] + [
            (f"Population {k}", v) for k, v in stats.get("clusters", {}).items()]:
        r = block["capture_rate"]
        lines.append(f"{label}: {r['n_events']} events, capture rate {r['mean']:.3f} /s "
                     f"(95% CrI {r['lower']:.3f}–{r['upper']:.3f})")
        dw = block.get("dwell_time")
        if dw:
            taus = ", ".join(f"{t * 1e3:.3f} ms [{lo * 1e3:.3f}–{hi * 1e3:.3f}]"
                             for t, lo, hi in zip(dw["tau_mean"], dw["tau_lower"], dw["tau_upper"]))
            probs = ", ".join(f"{k}: {v:.2f}" for k, v in sorted(dw["n_components_probs"].items(),
                                                                 key=lambda kv: int(kv[0])))
            lines.append(f"   dwell time constants: {taus}")
            lines.append(f"   P(number of components) — {probs}")
    return "\n".join(lines)


def run_gui(host: str = "127.0.0.1", port: int = 8050, folder: Optional[str] = None,
            path: Optional[str] = None, open_browser: bool = True, debug: bool = False) -> None:
    """Start the GUI server (blocking)."""
    app = create_app(folder=folder, path=path)
    url = f"http://{host}:{port}/"
    if open_browser:
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    if not debug:
        import logging
        logging.getLogger("werkzeug").setLevel(logging.WARNING)
    print(f"Nano_ext GUI running at {url}  (Ctrl+C to stop)")
    if host not in ("127.0.0.1", "localhost"):
        print("Warning: the GUI can read any file this user can; only expose it on trusted networks.")
    app.run(host=host, port=port, debug=debug)
