"""Jupyter notebook widget helpers for interactive nano_ext analysis.

Install the optional dependencies before use:
    pip install "nano_ext[notebook]"

Usage (in a Jupyter notebook cell):
    from nano_ext.outputs.notebook_widgets import NanoExtUI
    ui = NanoExtUI()
    ui.display()
"""

from __future__ import annotations

import traceback
from pathlib import Path

import numpy as np


# ---------------------------------------------------------------------------
# Lazy imports
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


def _require_filechooser():
    try:
        from ipyfilechooser import FileChooser
        return FileChooser
    except ImportError:
        raise ImportError(
            "ipyfilechooser is required for the file picker.\n"
            'Install it with:  pip install "nano_ext[notebook]"'
        )


def _hint(text: str) -> "ipywidgets.HTML":
    import ipywidgets as widgets
    return widgets.HTML(
        f"<span style='color:#888;font-size:0.82em;margin-left:4px'>{text}</span>"
    )


# ---------------------------------------------------------------------------
# Plotly figure builder
# ---------------------------------------------------------------------------

def make_plotly_figure(result) -> "plotly.graph_objects.Figure":
    """Build an interactive plotly figure from a PipelineResult."""
    go, _ = _require_plotly()

    sr = result.signal_data.sampling_rate
    n = len(result.filtered_signal)
    t = np.arange(n) / sr * 1000  # ms

    baseline = result.baseline_result.local_baseline
    threshold = result.threshold_result.threshold
    units = result.signal_data.units or "pA"

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=t, y=result.filtered_signal,
        mode="lines", line=dict(color="#4C72B0", width=0.8),
        name="Filtered signal",
        hovertemplate="t=%{x:.3f} ms<br>I=%{y:.4f} " + units + "<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=t, y=baseline,
        mode="lines", line=dict(color="#DD8452", width=1.2, dash="dash"),
        name="Baseline",
        hovertemplate="t=%{x:.3f} ms<br>baseline=%{y:.4f} " + units + "<extra></extra>",
    ))
    from nano_ext.models import EventDirection
    direction = result.config.event_direction
    if direction == EventDirection.UP:
        fig.add_trace(go.Scatter(
            x=t, y=baseline - threshold,
            mode="lines", line=dict(color="#8172B2", width=1.0, dash="dot"),
            name="Threshold", hoverinfo="skip",
        ))
    elif direction == EventDirection.BOTH:
        fig.add_trace(go.Scatter(
            x=t, y=baseline + threshold,
            mode="lines", line=dict(color="#C44E52", width=1.0, dash="dot"),
            name="Threshold (down)", hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=t, y=baseline - threshold,
            mode="lines", line=dict(color="#8172B2", width=1.0, dash="dot"),
            name="Threshold (up)", hoverinfo="skip",
        ))
    else:
        fig.add_trace(go.Scatter(
            x=t, y=baseline + threshold,
            mode="lines", line=dict(color="#C44E52", width=1.0, dash="dot"),
            name="Threshold", hoverinfo="skip",
        ))

    _add_event_traces(fig, result, t, units)

    fig.update_layout(
        height=500,
        margin=dict(l=60, r=20, t=40, b=50),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        template="plotly_white",
        xaxis_title="Time (ms)",
        yaxis_title=f"Current ({units})",
    )
    return fig


def _add_event_traces(fig, result, t, units):
    sr = result.signal_data.sampling_rate
    for i, ev in enumerate(result.events):
        x0 = ev.start_idx / sr * 1000
        x1 = ev.end_idx / sr * 1000
        color = "rgba(196,78,82,0.15)" if ev.direction.value == "down" else "rgba(129,114,178,0.15)"
        fig.add_vrect(
            x0=x0, x1=x1, fillcolor=color, opacity=1.0,
            layer="below", line_width=0,
            annotation_text="" if len(result.events) > 50 else f"#{i+1}",
            annotation_position="top left", annotation_font_size=9,
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

    def display(self):
        """Render the full UI in the current Jupyter cell output."""
        widgets, display, HTML = _require_widgets()
        self._build(widgets, display, HTML)

    def _build(self, widgets, display, HTML):
        FileChooser = _require_filechooser()

        # ------------------------------------------------------------------ #
        # Section 1: File Loading
        # ------------------------------------------------------------------ #
        self.w_filechooser = FileChooser(
            path=str(Path.home()),
            title="Signal file",
            show_hidden=False,
            use_dir_icons=True,
        )
        self.w_control_chooser = FileChooser(
            path=str(Path.home()),
            title="Control file (optional — analyte-free recording for noise characterisation)",
            show_hidden=False,
            use_dir_icons=True,
        )
        self.w_format = widgets.Dropdown(
            options=[("Auto-detect", None), ("ABF", "abf"), ("Binary", "binary")],
            description="Format:",
            style={"description_width": "80px"},
        )
        self.w_channel = widgets.BoundedIntText(
            value=0, min=0, max=16,
            description="Channel:",
            layout=widgets.Layout(width="180px"),
            style={"description_width": "70px"},
        )
        self.w_sampling_rate = widgets.FloatText(
            value=100000.0,
            description="Sampling rate (Hz):",
            layout=widgets.Layout(width="260px"),
            style={"description_width": "150px"},
        )
        self.w_load_btn = widgets.Button(
            description="Load", button_style="info",
            layout=widgets.Layout(width="120px"),
        )
        self.w_load_status = widgets.HTML("")

        section1 = widgets.VBox([
            widgets.HTML("<h3 style='margin:0 0 6px'>1. File Loading</h3>"),
            self.w_filechooser,
            widgets.HBox([self.w_format, self.w_channel]),
            widgets.HBox([
                self.w_sampling_rate,
                _hint("(required for binary files only)"),
            ]),
            self.w_control_chooser,
            self.w_load_btn,
            self.w_load_status,
        ], layout=widgets.Layout(border="1px solid #ddd", padding="10px", margin="5px 0"))

        # ------------------------------------------------------------------ #
        # Section 2: Core Settings (always visible)
        # ------------------------------------------------------------------ #
        self.w_event_direction = widgets.Dropdown(
            options=[("Down (blockade — current decreases)", "down"),
                     ("Up (anti-blockade — current increases)", "up"),
                     ("Both (advanced — see note below)", "both")],
            description="Direction:",
            layout=widgets.Layout(width="400px"),
            style={"description_width": "80px"},
        )
        self.w_direction_warning = widgets.HTML("")

        def _on_direction(change):
            if change["new"] == "both":
                self.w_direction_warning.value = (
                    "<div style='background:#fff8e1;border:1px solid #ffe082;"
                    "border-radius:4px;padding:6px 10px;margin-top:4px;"
                    "font-size:0.85em;color:#7a5c00'>"
                    "⚠ <b>Both</b> applies the same threshold symmetrically in both directions. "
                    "Because noise fluctuates in both directions equally, this typically causes "
                    "many false positives in the direction opposite to your real events. "
                    "Use only if you have confirmed that both blockade and anti-blockade events "
                    "occur in this recording.</div>"
                )
            else:
                self.w_direction_warning.value = ""
        self.w_event_direction.observe(_on_direction, names="value")
        self.w_auto_tune = widgets.Checkbox(
            value=True,
            description="Auto-tune detection parameters",
            indent=False,
        )

        section2 = widgets.VBox([
            widgets.HTML("<h3 style='margin:0 0 6px'>2. Analysis Settings</h3>"),
            widgets.VBox([
                self.w_event_direction,
                self.w_direction_warning,
                _hint("Choose 'Down' if molecules block the pore (most common). "
                      "Choose 'Up' if your signal increases during translocation."),
            ]),
            widgets.HTML("<div style='margin-top:8px'></div>"),
            widgets.VBox([
                self.w_auto_tune,
                _hint("When enabled, baseline window, minimum event duration, and filter "
                      "cutoff are set automatically from the signal noise level. "
                      "Recommended for most users — disable only to fine-tune manually."),
            ]),
        ], layout=widgets.Layout(border="1px solid #ddd", padding="10px", margin="5px 0"))

        # ------------------------------------------------------------------ #
        # Section 3: Additional Settings (accordion, collapsed by default)
        # ------------------------------------------------------------------ #

        # --- Preprocessing ---
        self.w_apply_filter = widgets.Checkbox(
            value=False, description="Apply software low-pass filter", indent=False,
        )
        self.w_filter_cutoff = widgets.FloatText(
            value=10000.0,
            description="Cutoff (Hz):",
            layout=widgets.Layout(width="220px"),
            style={"description_width": "90px"},
        )
        self.w_filter_type = widgets.Dropdown(
            options=["bessel", "butterworth"],
            description="Filter type:",
            style={"description_width": "90px"},
        )

        preproc_box = widgets.VBox([
            widgets.HTML("<b>Preprocessing</b>"),
            widgets.HTML(
                "<p style='color:#555;font-size:0.88em;margin:4px 0 8px'>"
                "Most instruments apply a hardware low-pass filter during recording, "
                "so software filtering is usually <em>not</em> needed. "
                "Enable this only if your raw data is unfiltered.</p>"
            ),
            self.w_apply_filter,
            widgets.HBox([self.w_filter_cutoff, self.w_filter_type]),
            _hint("Bessel filter (recommended): minimal distortion of event shape. "
                  "Butterworth: sharper roll-off but more ringing."),
        ])

        # --- Detection Parameters ---
        self.w_baseline_window = widgets.FloatSlider(
            value=5.0, min=0.5, max=30.0, step=0.5,
            description="Baseline window (s):",
            readout_format=".1f",
            layout=widgets.Layout(width="420px"),
            style={"description_width": "160px"},
        )
        self.w_min_duration = widgets.FloatLogSlider(
            value=1e-4, base=10, min=-5, max=-2, step=0.1,
            description="Min event duration (s):",
            readout_format=".2e",
            layout=widgets.Layout(width="420px"),
            style={"description_width": "160px"},
        )
        self.w_gmm_components = widgets.IntSlider(
            value=5, min=2, max=15,
            description="Max GMM components:",
            layout=widgets.Layout(width="420px"),
            style={"description_width": "160px"},
        )

        detect_box = widgets.VBox([
            widgets.HTML("<b style='margin-top:12px;display:block'>Detection Parameters</b>"),
            widgets.HTML(
                "<p style='color:#555;font-size:0.88em;margin:4px 0 4px'>"
                "These are set automatically when Auto-tune is enabled.</p>"
            ),
            self.w_baseline_window,
            _hint("Sliding window used to track the open-pore baseline. "
                  "Should be several times longer than the typical gap between events. "
                  "Increase if the baseline drifts slowly; decrease if events are very frequent."),
            widgets.HTML("<div style='margin-top:6px'></div>"),
            self.w_min_duration,
            _hint("Events shorter than this are discarded as noise. "
                  "The lower bound is set by the filter rise time (~5 / filter cutoff). "
                  "Typical values: 0.05 ms (5×10⁻⁵ s) to 0.5 ms."),
            widgets.HTML("<div style='margin-top:6px'></div>"),
            self.w_gmm_components,
            _hint("Maximum number of current levels the GMM threshold model will consider. "
                  "Increase only if you expect many distinct blockade levels in one recording."),
        ])

        additional_content = widgets.VBox(
            [preproc_box, widgets.HTML("<hr style='margin:10px 0'>"), detect_box],
            layout=widgets.Layout(padding="8px"),
        )
        accordion = widgets.Accordion(children=[additional_content])
        accordion.set_title(0, "Additional Settings")
        accordion.selected_index = None  # collapsed by default

        # Disable manual detection params when auto-tune is ON
        def _on_autotune(change):
            disabled = change["new"]
            for w in [self.w_filter_cutoff, self.w_filter_type,
                      self.w_baseline_window, self.w_min_duration,
                      self.w_gmm_components]:
                w.disabled = disabled
        self.w_auto_tune.observe(_on_autotune, names="value")
        _on_autotune({"new": self.w_auto_tune.value})

        section3 = widgets.VBox(
            [accordion],
            layout=widgets.Layout(margin="5px 0"),
        )

        # ------------------------------------------------------------------ #
        # Section 4: Run
        # ------------------------------------------------------------------ #
        self.w_run_btn = widgets.Button(
            description="Run Analysis",
            button_style="success",
            icon="play",
            layout=widgets.Layout(width="160px", height="36px"),
        )
        self.w_run_status = widgets.HTML("")

        section4 = widgets.VBox([
            widgets.HTML("<h3 style='margin:0 0 6px'>3. Run</h3>"),
            self.w_run_btn,
            self.w_run_status,
        ], layout=widgets.Layout(border="1px solid #ddd", padding="10px", margin="5px 0"))

        # ------------------------------------------------------------------ #
        # Section 5: Results
        # ------------------------------------------------------------------ #
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
            widgets.HTML("<h3 style='margin:0 0 6px'>4. Results</h3>"),
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

        display(widgets.VBox([section1, section2, section3, section4, section5]))

    # ------------------------------------------------------------------ #
    # Callbacks
    # ------------------------------------------------------------------ #

    def _on_load(self, widgets, HTML):
        self.w_load_status.value = "<span style='color:gray'>Loading...</span>"
        filepath = self.w_filechooser.selected
        if not filepath:
            self.w_load_status.value = "<span style='color:red'>Please select a file.</span>"
            return
        p = Path(filepath)
        if not p.exists():
            self.w_load_status.value = f"<span style='color:red'>File not found: {filepath}</span>"
            return
        try:
            sd = self._load_signal(filepath)
            self.w_load_status.value = (
                f"<span style='color:green'>✓ Loaded</span> — "
                f"{sd.n_samples:,} samples @ {sd.sampling_rate:.0f} Hz "
                f"({sd.duration_sec:.3f} s)"
            )
            if not self.w_auto_tune.value:
                self.w_filter_cutoff.value = sd.sampling_rate / 10.0
        except Exception as e:
            self.w_load_status.value = f"<span style='color:red'>Error: {e}</span>"

    def _on_run(self, widgets, display, HTML):
        self.w_run_status.value = "<span style='color:gray'>Running analysis...</span>"
        self.w_run_btn.disabled = True
        try:
            self._run_pipeline()
            self.w_run_status.value = "<span style='color:green'>✓ Done</span>"
            self._render_results(display, HTML)
            self.w_download_btn.disabled = False
        except Exception:
            tb = traceback.format_exc()
            self.w_run_status.value = (
                f"<span style='color:red'>An error occurred.</span>"
                f"<pre style='font-size:0.8em;color:red'>{tb}</pre>"
            )
        finally:
            self.w_run_btn.disabled = False

    def _on_download(self, display, HTML):
        if self._result is None:
            return
        import base64
        import pandas as pd

        events = self._result.events
        if not events:
            self.w_download_status.value = "<span style='color:gray'>No events to download.</span>"
            return

        sr = self._result.signal_data.sampling_rate
        units = self._result.signal_data.units or "pA"
        rows = [
            {
                "event_id": i + 1,
                "start_sec": ev.start_idx / sr,
                "end_sec": ev.end_idx / sr,
                "duration_ms": ev.duration * 1000,
                f"depth_{units}": ev.depth,
                "area": ev.area,
                "n_sublevels": len(ev.sublevels),
                "direction": ev.direction.value,
            }
            for i, ev in enumerate(events)
        ]
        csv_str = pd.DataFrame(rows).to_csv(index=False)
        b64 = base64.b64encode(csv_str.encode()).decode()
        stem = Path(self.w_filechooser.selected or "recording").stem
        filename = f"{stem}_events.csv"
        self.w_download_status.value = (
            f'<a download="{filename}" href="data:text/csv;base64,{b64}" '
            f'style="text-decoration:none">'
            f'<button style="padding:4px 12px">⬇ {filename}</button></a>'
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _load_signal(self, filepath: str):
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

        signal_data = self._load_signal(self.w_filechooser.selected)
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

        control_signal = None
        ctrl_path = self.w_control_chooser.selected
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
        sr = result.signal_data.sampling_rate
        units = result.signal_data.units or "pA"

        self.w_summary.value = (
            f"<pre style='background:#f8f8f8;padding:8px;border-radius:4px'>"
            f"{result.summary()}"
            f"</pre>"
        )

        self.w_plot_out.clear_output(wait=True)
        with self.w_plot_out:
            make_plotly_figure(result).show()

        self.w_table_out.clear_output(wait=True)
        with self.w_table_out:
            import pandas as pd
            if result.events:
                rows = [
                    {
                        "ID": i + 1,
                        "Start (ms)": f"{ev.start_idx / sr * 1000:.3f}",
                        "End (ms)": f"{ev.end_idx / sr * 1000:.3f}",
                        f"Depth ({units})": f"{ev.depth:.4f}",
                        "Duration (ms)": f"{ev.duration * 1000:.3f}",
                        "Area": f"{ev.area:.4f}",
                        "Sub-levels": len(ev.sublevels),
                        "Direction": ev.direction.value,
                    }
                    for i, ev in enumerate(result.events)
                ]
                display(pd.DataFrame(rows))
            else:
                display(HTML("<p style='color:gray'>No events detected.</p>"))
