"""Jupyter notebook widget helpers for interactive nano_ext analysis.

Install the optional dependencies before use:
    pip install "nano_ext[notebook]"

Usage (in a Jupyter notebook cell):
    from nano_ext.outputs.notebook_widgets import NanoExtUI
    ui = NanoExtUI()
    ui.display()
"""

from __future__ import annotations

import io
import traceback
from pathlib import Path
from typing import Optional

import numpy as np


# ---------------------------------------------------------------------------
# Lazy imports — only needed when the widgets are actually displayed
# ---------------------------------------------------------------------------

def _require_widgets():
    try:
        import ipywidgets as widgets
        from IPython.display import display, HTML
        return widgets, display, HTML
    except ImportError:
        raise ImportError(
            "ipywidgets is required for the interactive UI.\n"
            'Install it with:  pip install "nano_ext[notebook]"'
        )


def _require_plotly():
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
        return go, make_subplots
    except ImportError:
        raise ImportError(
            "plotly is required for interactive plots.\n"
            'Install it with:  pip install "nano_ext[notebook]"'
        )


# ---------------------------------------------------------------------------
# Plotly figure builder
# ---------------------------------------------------------------------------

def make_plotly_figure(result) -> "plotly.graph_objects.Figure":
    """Build an interactive plotly figure from a PipelineResult.

    Parameters
    ----------
    result : PipelineResult
        Output of ``run_pipeline``.

    Returns
    -------
    plotly.graph_objects.Figure
        Interactive figure with signal, baseline, threshold, and event markers.
    """
    go, make_subplots = _require_plotly()

    sr = result.signal_data.sampling_rate
    n = len(result.filtered_signal)
    t = np.arange(n) / sr * 1000  # ms

    baseline = result.baseline_result.local_baseline
    threshold = result.threshold_result.threshold
    units = result.signal_data.units or "pA"

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.75, 0.25],
        vertical_spacing=0.05,
    )

    # ---- Row 1: signal + baseline + threshold ----
    fig.add_trace(
        go.Scatter(
            x=t, y=result.filtered_signal,
            mode="lines",
            line=dict(color="#4C72B0", width=0.8),
            name="Filtered signal",
            hovertemplate="t=%{x:.3f} ms<br>I=%{y:.4f} " + units + "<extra></extra>",
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=t, y=baseline,
            mode="lines",
            line=dict(color="#DD8452", width=1.2, dash="dash"),
            name="Baseline",
            hovertemplate="t=%{x:.3f} ms<br>baseline=%{y:.4f} " + units + "<extra></extra>",
        ),
        row=1, col=1,
    )

    # Threshold line(s)
    threshold_y = baseline + threshold
    fig.add_trace(
        go.Scatter(
            x=t, y=threshold_y,
            mode="lines",
            line=dict(color="#C44E52", width=1.0, dash="dot"),
            name="Threshold",
            hoverinfo="skip",
        ),
        row=1, col=1,
    )
    # If BOTH direction: also show upper threshold
    from nano_ext.models import EventDirection
    if result.config.event_direction == EventDirection.BOTH:
        upper_threshold_y = baseline - threshold  # symmetric
        fig.add_trace(
            go.Scatter(
                x=t, y=upper_threshold_y,
                mode="lines",
                line=dict(color="#8172B2", width=1.0, dash="dot"),
                name="Threshold (up)",
                hoverinfo="skip",
            ),
            row=1, col=1,
        )

    # ---- Event shading + hover markers ----
    _add_event_traces(fig, result, t, units)

    # ---- Row 2: event depth bar chart ----
    if result.events:
        ev_t = [ev.start_idx / sr * 1000 for ev in result.events]
        ev_depth = [ev.depth for ev in result.events]
        ev_dur = [ev.duration * 1000 for ev in result.events]
        ev_text = [
            f"#{i+1}<br>depth={d:.4f} {units}<br>dur={dur:.3f} ms"
            for i, (d, dur) in enumerate(zip(ev_depth, ev_dur))
        ]
        fig.add_trace(
            go.Bar(
                x=ev_t,
                y=ev_depth,
                width=[dur for dur in ev_dur],
                text=ev_text,
                hovertemplate="%{text}<extra></extra>",
                marker_color="#4C72B0",
                name="Event depth",
                showlegend=False,
            ),
            row=2, col=1,
        )

    fig.update_layout(
        height=600,
        margin=dict(l=60, r=20, t=40, b=40),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        template="plotly_white",
    )
    fig.update_xaxes(title_text="Time (ms)", row=2, col=1)
    fig.update_yaxes(title_text=f"Current ({units})", row=1, col=1)
    fig.update_yaxes(title_text=f"Depth ({units})", row=2, col=1)

    return fig


def _add_event_traces(fig, result, t, units):
    """Add semi-transparent event region shading to row 1."""
    go, _ = _require_plotly()
    sr = result.signal_data.sampling_rate

    # Draw vrect-style shading via Scatter fill for each event
    # (plotly add_vrect is simpler but doesn't support custom hover)
    for i, ev in enumerate(result.events):
        x0 = ev.start_idx / sr * 1000
        x1 = ev.end_idx / sr * 1000
        depth = ev.depth
        dur_ms = ev.duration * 1000
        label = f"Event #{i+1}<br>depth={depth:.4f} {units}<br>dur={dur_ms:.3f} ms"
        color = "rgba(196,78,82,0.15)" if ev.direction.value == "down" else "rgba(129,114,178,0.15)"
        fig.add_vrect(
            x0=x0, x1=x1,
            fillcolor=color,
            opacity=1.0,
            layer="below",
            line_width=0,
            row=1, col=1,
            annotation_text="" if len(result.events) > 50 else f"#{i+1}",
            annotation_position="top left",
            annotation_font_size=9,
        )


# ---------------------------------------------------------------------------
# Main UI class
# ---------------------------------------------------------------------------

class NanoExtUI:
    """Interactive Jupyter widget UI for nano_ext analysis.

    Usage
    -----
    ::

        from nano_ext.outputs.notebook_widgets import NanoExtUI
        ui = NanoExtUI()
        ui.display()
    """

    def __init__(self):
        self._result = None
        self._widgets_built = False

    def display(self):
        """Render the full UI in the current Jupyter cell output."""
        widgets, display, HTML = _require_widgets()
        self._build(widgets, display, HTML)

    def _build(self, widgets, display, HTML):
        # ------------------------------------------------------------------ #
        # Section 1: File Loading
        # ------------------------------------------------------------------ #
        s1_title = widgets.HTML("<h3 style='margin-bottom:4px'>1. ファイル読み込み</h3>")

        self.w_filepath = widgets.Text(
            description="ファイルパス:",
            placeholder="/path/to/recording.abf",
            layout=widgets.Layout(width="500px"),
            style={"description_width": "100px"},
        )
        self.w_format = widgets.Dropdown(
            options=[("自動検出", None), ("ABF", "abf"), ("Binary", "binary")],
            description="フォーマット:",
            style={"description_width": "100px"},
        )
        self.w_channel = widgets.BoundedIntText(
            value=0, min=0, max=16,
            description="チャンネル:",
            layout=widgets.Layout(width="200px"),
            style={"description_width": "100px"},
        )
        self.w_sampling_rate = widgets.FloatText(
            value=100000.0,
            description="サンプリングレート (Hz):",
            layout=widgets.Layout(width="280px"),
            style={"description_width": "170px"},
        )
        self.w_sampling_rate_box = widgets.HBox(
            [self.w_sampling_rate,
             widgets.HTML("<span style='color:gray;font-size:0.85em;margin-left:8px'>(binary形式のみ必要)</span>")]
        )
        self.w_load_btn = widgets.Button(
            description="Load", button_style="info",
            layout=widgets.Layout(width="120px"),
        )
        self.w_load_status = widgets.HTML("")

        self.w_control_path = widgets.Text(
            description="コントロールパス:",
            placeholder="(オプション) /path/to/control.abf",
            layout=widgets.Layout(width="500px"),
            style={"description_width": "130px"},
        )

        section1 = widgets.VBox([
            s1_title,
            self.w_filepath,
            widgets.HBox([self.w_format, self.w_channel]),
            self.w_sampling_rate_box,
            self.w_control_path,
            self.w_load_btn,
            self.w_load_status,
        ], layout=widgets.Layout(border="1px solid #ddd", padding="10px", margin="5px 0"))

        # ------------------------------------------------------------------ #
        # Section 2: Preprocessing
        # ------------------------------------------------------------------ #
        s2_title = widgets.HTML("<h3 style='margin-bottom:4px'>2. 前処理</h3>")

        self.w_auto_tune = widgets.Checkbox(
            value=True, description="Auto-tune (ノイズから自動設定)",
            indent=False,
        )
        self.w_apply_filter = widgets.Checkbox(
            value=True, description="低域通過フィルタを適用",
            indent=False,
        )
        self.w_filter_cutoff = widgets.FloatText(
            value=10000.0,
            description="カットオフ周波数 (Hz):",
            layout=widgets.Layout(width="280px"),
            style={"description_width": "160px"},
        )
        self.w_filter_type = widgets.Dropdown(
            options=["bessel", "butterworth"],
            description="フィルタ種別:",
            style={"description_width": "100px"},
        )

        # Auto-tune ON → filter_cutoff/type の手動設定を無効化
        def _on_autotune(change):
            disabled = change["new"]
            self.w_filter_cutoff.disabled = disabled
            self.w_filter_type.disabled = disabled
        self.w_auto_tune.observe(_on_autotune, names="value")
        _on_autotune({"new": self.w_auto_tune.value})

        section2 = widgets.VBox([
            s2_title,
            self.w_auto_tune,
            self.w_apply_filter,
            widgets.HBox([self.w_filter_cutoff, self.w_filter_type]),
        ], layout=widgets.Layout(border="1px solid #ddd", padding="10px", margin="5px 0"))

        # ------------------------------------------------------------------ #
        # Section 3: Detection Parameters
        # ------------------------------------------------------------------ #
        s3_title = widgets.HTML("<h3 style='margin-bottom:4px'>3. 検出パラメータ</h3>")

        self.w_event_direction = widgets.Dropdown(
            options=[("下向き (blockade)", "down"), ("上向き (up)", "up"), ("両方向 (both)", "both")],
            description="イベント方向:",
            style={"description_width": "120px"},
        )
        self.w_baseline_window = widgets.FloatSlider(
            value=5.0, min=0.5, max=30.0, step=0.5,
            description="ベースライン窓 (s):",
            readout_format=".1f",
            layout=widgets.Layout(width="420px"),
            style={"description_width": "150px"},
        )
        self.w_min_duration = widgets.FloatLogSlider(
            value=1e-4, base=10, min=-5, max=-2, step=0.1,
            description="最小イベント長 (s):",
            readout_format=".2e",
            layout=widgets.Layout(width="420px"),
            style={"description_width": "150px"},
        )
        self.w_gmm_components = widgets.IntSlider(
            value=5, min=2, max=15,
            description="GMM最大コンポーネント:",
            layout=widgets.Layout(width="420px"),
            style={"description_width": "170px"},
        )

        # Auto-tune ON → 手動パラメータを無効化
        def _on_autotune2(change):
            disabled = change["new"]
            for w in [self.w_baseline_window, self.w_min_duration, self.w_gmm_components]:
                w.disabled = disabled
        self.w_auto_tune.observe(_on_autotune2, names="value")
        _on_autotune2({"new": self.w_auto_tune.value})

        section3 = widgets.VBox([
            s3_title,
            self.w_event_direction,
            self.w_baseline_window,
            self.w_min_duration,
            self.w_gmm_components,
        ], layout=widgets.Layout(border="1px solid #ddd", padding="10px", margin="5px 0"))

        # ------------------------------------------------------------------ #
        # Section 4: Run
        # ------------------------------------------------------------------ #
        s4_title = widgets.HTML("<h3 style='margin-bottom:4px'>4. 解析実行</h3>")
        self.w_run_btn = widgets.Button(
            description="Run Analysis",
            button_style="success",
            icon="play",
            layout=widgets.Layout(width="160px", height="36px"),
        )
        self.w_run_status = widgets.HTML("")

        section4 = widgets.VBox([
            s4_title,
            self.w_run_btn,
            self.w_run_status,
        ], layout=widgets.Layout(border="1px solid #ddd", padding="10px", margin="5px 0"))

        # ------------------------------------------------------------------ #
        # Section 5: Results
        # ------------------------------------------------------------------ #
        s5_title = widgets.HTML("<h3 style='margin-bottom:4px'>5. 結果</h3>")
        self.w_summary = widgets.HTML("")
        self.w_plot_out = widgets.Output()
        self.w_table_out = widgets.Output()
        self.w_download_btn = widgets.Button(
            description="Download CSV",
            button_style="",
            icon="download",
            layout=widgets.Layout(width="160px"),
            disabled=True,
        )
        self.w_download_status = widgets.HTML("")

        section5 = widgets.VBox([
            s5_title,
            self.w_summary,
            self.w_plot_out,
            self.w_table_out,
            widgets.HBox([self.w_download_btn, self.w_download_status]),
        ], layout=widgets.Layout(border="1px solid #ddd", padding="10px", margin="5px 0"))

        # ------------------------------------------------------------------ #
        # Wire up callbacks
        # ------------------------------------------------------------------ #
        self.w_load_btn.on_click(lambda _: self._on_load(widgets, HTML))
        self.w_run_btn.on_click(lambda _: self._on_run(widgets, display, HTML))
        self.w_download_btn.on_click(lambda _: self._on_download(display, HTML))

        full_ui = widgets.VBox([section1, section2, section3, section4, section5])
        display(full_ui)

    # ------------------------------------------------------------------ #
    # Callbacks
    # ------------------------------------------------------------------ #

    def _on_load(self, widgets, HTML):
        self.w_load_status.value = "<span style='color:gray'>読み込み中...</span>"
        filepath = self.w_filepath.value.strip()
        if not filepath:
            self.w_load_status.value = "<span style='color:red'>ファイルパスを入力してください。</span>"
            return
        p = Path(filepath)
        if not p.exists():
            self.w_load_status.value = f"<span style='color:red'>ファイルが見つかりません: {filepath}</span>"
            return
        try:
            sd = self._load_signal(filepath)
            msg = (
                f"<span style='color:green'>✓ 読み込み完了</span> — "
                f"{sd.n_samples:,} samples @ {sd.sampling_rate:.0f} Hz "
                f"({sd.duration_sec:.3f} s)"
            )
            self.w_load_status.value = msg
            # Update filter_cutoff default to sr/10 if not already customised
            if not self.w_auto_tune.value:
                self.w_filter_cutoff.value = sd.sampling_rate / 10.0
        except Exception as e:
            self.w_load_status.value = f"<span style='color:red'>エラー: {e}</span>"

    def _on_run(self, widgets, display, HTML):
        self.w_run_status.value = "<span style='color:gray'>解析中...</span>"
        self.w_run_btn.disabled = True
        try:
            self._run_pipeline()
            self.w_run_status.value = "<span style='color:green'>✓ 完了</span>"
            self._render_results(display, HTML)
            self.w_download_btn.disabled = False
        except Exception:
            tb = traceback.format_exc()
            self.w_run_status.value = (
                f"<span style='color:red'>エラーが発生しました。</span>"
                f"<pre style='font-size:0.8em;color:red'>{tb}</pre>"
            )
        finally:
            self.w_run_btn.disabled = False

    def _on_download(self, display, HTML):
        if self._result is None:
            return
        from nano_ext.outputs.csv_writer import write_events_to_csv
        import base64
        buf = io.StringIO()
        import csv as _csv
        sr = self._result.signal_data.sampling_rate
        # Write to StringIO via csv module
        events = self._result.events
        if not events:
            self.w_download_status.value = "<span style='color:gray'>イベントがありません。</span>"
            return
        import pandas as pd
        rows = []
        for ev in events:
            rows.append({
                "event_id": ev.event_id,
                "start_sec": ev.start_idx / sr,
                "end_sec": ev.end_idx / sr,
                "duration_ms": ev.duration * 1000,
                "depth": ev.depth,
                "area": ev.area,
                "n_sublevels": len(ev.sublevels),
                "direction": ev.direction.value,
            })
        df = pd.DataFrame(rows)
        csv_str = df.to_csv(index=False)
        b64 = base64.b64encode(csv_str.encode()).decode()
        stem = Path(self.w_filepath.value.strip()).stem
        filename = f"{stem}_events.csv"
        href = (
            f'<a download="{filename}" href="data:text/csv;base64,{b64}" '
            f'style="text-decoration:none">'
            f'<button style="padding:4px 12px">⬇ {filename}</button></a>'
        )
        self.w_download_status.value = href

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _load_signal(self, filepath: str):
        from nano_ext.models import SignalData
        p = Path(filepath)
        fmt = self.w_format.value
        if fmt is None:
            fmt = "abf" if p.suffix.lower() == ".abf" else "binary"
        if fmt == "abf":
            from nano_ext.io.abf_reader import read_abf
            return read_abf(str(p), channel=self.w_channel.value)
        else:
            from nano_ext.io.binary_reader import read_binary
            return read_binary(
                str(p),
                sampling_rate=self.w_sampling_rate.value,
                dtype="float32",
            )

    def _run_pipeline(self):
        from nano_ext.pipeline import run_pipeline
        from nano_ext.detection.autotune import suggest_config
        from nano_ext.models import EventDirection

        signal_data = self._load_signal(self.w_filepath.value.strip())

        direction = EventDirection(self.w_event_direction.value)

        if self.w_auto_tune.value:
            config = suggest_config(
                signal_data,
                apply_filter=self.w_apply_filter.value,
                event_direction=direction,
            )
        else:
            from nano_ext.models import DetectionConfig
            config = DetectionConfig(
                apply_filter=self.w_apply_filter.value,
                filter_cutoff=self.w_filter_cutoff.value,
                filter_type=self.w_filter_type.value,
                baseline_window_sec=self.w_baseline_window.value,
                min_event_duration_sec=self.w_min_duration.value,
                gmm_max_components=self.w_gmm_components.value,
                event_direction=direction,
            )

        # Optional control signal
        control_signal = None
        ctrl_path = self.w_control_path.value.strip()
        if ctrl_path:
            control_signal = self._load_signal(ctrl_path)

        self._result = run_pipeline(
            signal_data=signal_data,
            config=config,
            analyze_sublevel=True,
            verbose=False,
            control_signal=control_signal,
        )

    def _render_results(self, display, HTML):
        result = self._result

        # Summary
        self.w_summary.value = (
            f"<pre style='background:#f8f8f8;padding:8px;border-radius:4px'>"
            f"{result.summary()}"
            f"</pre>"
        )

        # Plot
        self.w_plot_out.clear_output(wait=True)
        with self.w_plot_out:
            fig = make_plotly_figure(result)
            fig.show()

        # Event table
        self.w_table_out.clear_output(wait=True)
        with self.w_table_out:
            import pandas as pd
            sr = result.signal_data.sampling_rate
            units = result.signal_data.units or "pA"
            if result.events:
                rows = []
                for ev in result.events:
                    rows.append({
                        "ID": ev.event_id,
                        "開始 (ms)": f"{ev.start_idx / sr * 1000:.3f}",
                        "終了 (ms)": f"{ev.end_idx / sr * 1000:.3f}",
                        f"深さ ({units})": f"{ev.depth:.4f}",
                        "持続 (ms)": f"{ev.duration * 1000:.3f}",
                        "面積": f"{ev.area:.4f}",
                        "サブレベル数": len(ev.sublevels),
                        "方向": ev.direction.value,
                    })
                df = pd.DataFrame(rows)
                display(df)
            else:
                display(HTML("<p style='color:gray'>イベントは検出されませんでした。</p>"))
