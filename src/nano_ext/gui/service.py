"""UI-independent operations behind the GUI.

Everything here is plain Python on top of the analysis package, so it can be
unit-tested without a browser and reused by other front ends (the Jupyter
widgets, scripts): locating and loading recordings, turning a settings
dictionary into a :class:`~nano_ext.models.DetectionConfig`, running the
pipeline with progress reporting, and producing export files.
"""

from __future__ import annotations

import io
import json
import os
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from nano_ext.models import DetectionConfig, EventDirection, SignalData

RECORDING_SUFFIXES = (".abf", ".bin", ".dat", ".raw")

# Settings understood by build_config, with their defaults.  Values of None
# mean "automatic".
DEFAULT_SETTINGS: dict[str, Any] = {
    "event_direction": "down",
    "auto_tune": True,
    "apply_filter": True,
    "filter_cutoff_khz": None,
    "detrend_method": "none",
    "baseline_window_sec": None,
    "min_event_duration_ms": None,
    "merge_gap_ms": None,
    "threshold_method": "dpgmm",
    "sublevel_method": "dpgmm",
    "analyze_sublevels": True,
    "hmm_method": "off",
    "cluster_events": False,
    "bayes_stats": False,
    "n_jobs": -1,
}


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def list_directory(folder: str | os.PathLike) -> tuple[Path, list[Path], list[Path]]:
    """Sub-folders and recording files of *folder* (sorted, hidden skipped).

    Returns ``(resolved_folder, subfolders, recordings)``.
    """
    folder = Path(folder).expanduser()
    if not folder.is_dir():
        raise NotADirectoryError(f"Not a folder: {folder}")
    folder = folder.resolve()
    dirs, files = [], []
    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
    except PermissionError:
        entries = []
    for p in entries:
        if p.name.startswith("."):
            continue
        try:
            if p.is_dir():
                dirs.append(p)
            elif p.suffix.lower() in RECORDING_SUFFIXES:
                files.append(p)
        except OSError:
            continue
    return folder, dirs, files


class DialogUnavailable(RuntimeError):
    """The operating-system file dialog cannot be shown (no Tk / no display)."""


# Runs in a separate interpreter so Tk owns that process's main thread (the
# Dash callbacks run on worker threads, and macOS only allows GUI calls on the
# main thread).  Prints one JSON line.
_DIALOG_SCRIPT = r"""
import json, sys
args = json.loads(sys.argv[1])
try:
    import tkinter as tk
    from tkinter import filedialog
except Exception as exc:
    print(json.dumps({"error": "tkinter is not installed (%s)" % exc})); sys.exit(0)
try:
    root = tk.Tk()
except Exception as exc:
    print(json.dumps({"error": "no display available (%s)" % exc})); sys.exit(0)
root.withdraw()
try:
    root.attributes("-topmost", True)
except Exception:
    pass
root.update()
path = filedialog.askopenfilename(
    parent=root, title=args["title"], initialdir=args["initialdir"],
    filetypes=[tuple(ft) for ft in args["filetypes"]],
)
root.destroy()
print(json.dumps({"path": path or ""}))
"""

DIALOG_FILETYPES = [
    ["Recordings", "*.abf *.ABF *.bin *.dat *.raw"],
    ["Axon ABF", "*.abf *.ABF"],
    ["All files", "*"],
]


def native_file_dialog(
    initialdir: Optional[str] = None,
    title: str = "Open recording",
    timeout: float = 900.0,
) -> Optional[str]:
    """Show the operating system's "open file" dialog on this machine.

    Returns the chosen path, or ``None`` if the user cancelled.  Raises
    :class:`DialogUnavailable` when no dialog can be shown (Tk missing, no
    display, timeout) so the caller can fall back to the in-page browser.
    """
    import subprocess
    import sys

    args = json.dumps({
        "title": title,
        "initialdir": str(Path(initialdir).expanduser()) if initialdir else os.getcwd(),
        "filetypes": DIALOG_FILETYPES,
    })
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _DIALOG_SCRIPT, args],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise DialogUnavailable("the file dialog timed out") from exc
    except OSError as exc:
        raise DialogUnavailable(str(exc)) from exc
    return parse_dialog_output(proc.stdout, proc.stderr)


def parse_dialog_output(stdout: str, stderr: str = "") -> Optional[str]:
    """Interpret the helper's JSON line (see :func:`native_file_dialog`)."""
    line = next((ln for ln in reversed((stdout or "").splitlines()) if ln.strip()), "")
    try:
        msg = json.loads(line)
    except ValueError:
        detail = (stderr or stdout or "no output").strip().splitlines()
        raise DialogUnavailable(detail[-1] if detail else "no output")
    if "error" in msg:
        raise DialogUnavailable(msg["error"])
    return msg.get("path") or None


def recording_info(path: str | os.PathLike) -> dict:
    """Header information for display before loading (ABF only)."""
    path = Path(path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    info: dict[str, Any] = {
        "path": str(path),
        "name": path.name,
        "size_mb": path.stat().st_size / 1e6,
        "format": "abf" if path.suffix.lower() == ".abf" else "binary",
    }
    if info["format"] == "abf":
        from nano_ext.io.abf_reader import get_abf_info
        abf = get_abf_info(path)
        info.update(
            n_channels=abf["n_channels"],
            channel_names=[_printable(n) for n in abf["channel_names"]],
            channel_units=[_printable(u) for u in abf["channel_units"]],
            sampling_rate_hz=float(abf["sampling_rate_hz"]),
            duration_sec=float(abf["duration_sec"]),
            n_sweeps=abf["n_sweeps"],
        )
    return info


def _printable(text) -> str:
    """Header strings can contain padding bytes; keep printable characters."""
    cleaned = "".join(ch for ch in str(text) if ch.isprintable()).strip()
    return cleaned or "?"


def load_recording(
    path: str | os.PathLike,
    channel: int = 0,
    sampling_rate: Optional[float] = None,
    dtype: str = "int16",
    scale_factor: float = 1.0,
) -> SignalData:
    """Load an ABF or raw binary recording (float32, memory-mapped read)."""
    path = Path(path).expanduser()
    if path.suffix.lower() == ".abf":
        from nano_ext.io.abf_reader import read_abf
        return read_abf(path, channel=channel)
    if not sampling_rate:
        raise ValueError("A sampling rate is required for raw binary files.")
    from nano_ext.io.binary_reader import read_binary
    return read_binary(path, sampling_rate=float(sampling_rate), dtype=dtype,
                       scale_factor=float(scale_factor))


# ---------------------------------------------------------------------------
# Settings -> DetectionConfig
# ---------------------------------------------------------------------------

def _num(value, scale: float = 1.0) -> Optional[float]:
    """Parse an optional positive number from a form value."""
    if value is None or value == "":
        return None
    v = float(value)
    return v * scale if v > 0 else None


def build_config(settings: dict, signal_data: Optional[SignalData] = None) -> DetectionConfig:
    """Turn GUI settings into a :class:`DetectionConfig`.

    With ``auto_tune`` (and a loaded signal), noise-aware values from
    :func:`~nano_ext.detection.autotune.suggest_config` fill every field the
    user left automatic; explicit values always win.
    """
    s = {**DEFAULT_SETTINGS, **(settings or {})}
    explicit: dict[str, Any] = {
        "event_direction": EventDirection(s["event_direction"]),
        "apply_filter": bool(s["apply_filter"]),
        "detrend_method": s["detrend_method"],
        "threshold_method": s["threshold_method"],
        "sublevel_method": s["sublevel_method"],
        "hmm_analysis": s["hmm_method"] not in (None, "off"),
        "hmm_method": s["hmm_method"] if s["hmm_method"] not in (None, "off") else "bic",
        "cluster_events": bool(s["cluster_events"]),
        "n_jobs": int(s["n_jobs"]) if s["n_jobs"] not in (None, "") else -1,
    }
    cutoff = _num(s["filter_cutoff_khz"], 1e3)
    if cutoff is not None:
        if s["apply_filter"]:
            explicit["filter_cutoff"] = cutoff
        else:
            explicit["pre_applied_filter_cutoff"] = cutoff
    for key, src, scale in (
        ("baseline_window_sec", "baseline_window_sec", 1.0),
        ("min_event_duration_sec", "min_event_duration_ms", 1e-3),
        ("merge_gap_sec", "merge_gap_ms", 1e-3),
    ):
        v = _num(s[src], scale)
        if v is not None:
            explicit[key] = v

    if s["auto_tune"] and signal_data is not None:
        from nano_ext.detection.autotune import suggest_config
        cfg = suggest_config(signal_data, **explicit)
        if not cfg.apply_filter and "filter_cutoff" not in explicit:
            # suggest_config sets filter_cutoff; for pre-filtered data it is
            # the assumed hardware cutoff instead.
            cfg.pre_applied_filter_cutoff = cfg.pre_applied_filter_cutoff or cfg.filter_cutoff
        return cfg
    return DetectionConfig(**explicit)


def hmm_bic_available() -> bool:
    try:
        import hmmlearn  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Ranges
# ---------------------------------------------------------------------------

def normalise_ranges(ranges, duration: float) -> list[tuple[float, float]]:
    """Clamp, sort and merge ``(t0, t1)`` ranges; drop empty ones."""
    out = []
    for r in ranges or []:
        try:
            a, b = float(r[0]), float(r[1])
        except (TypeError, ValueError, IndexError):
            continue
        a, b = sorted((a, b))
        a, b = max(0.0, a), min(duration, b)
        if b > a:
            out.append((a, b))
    out.sort()
    merged: list[tuple[float, float]] = []
    for a, b in out:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


# ---------------------------------------------------------------------------
# Running the analysis
# ---------------------------------------------------------------------------

@dataclass
class Job:
    """State of one background analysis run."""

    status: str = "idle"          # idle | running | done | error
    step: str = ""
    steps: list = field(default_factory=list)
    started: float = 0.0
    finished: float = 0.0
    error: str = ""
    result: Any = None
    stats: Optional[dict] = None
    config: Optional[DetectionConfig] = None

    @property
    def elapsed(self) -> float:
        end = self.finished if self.finished else time.time()
        return end - self.started if self.started else 0.0


def run_analysis(
    signal_data: SignalData,
    config: DetectionConfig,
    analysis_range: Optional[tuple[float, float]] = None,
    exclude_ranges: Optional[list[tuple[float, float]]] = None,
    analyze_sublevels: bool = True,
    bayes_stats: bool = False,
    control: Optional[SignalData] = None,
    on_step: Optional[Callable[[str], None]] = None,
):
    """Run the pipeline (and optional statistics); returns ``(result, stats)``."""
    from nano_ext.pipeline import run_pipeline

    result = run_pipeline(
        signal_data,
        config=config,
        analyze_sublevel=analyze_sublevels,
        control_signal=control,
        on_step=on_step,
        analysis_range=analysis_range,
        exclude_ranges=exclude_ranges or None,
    )
    stats = None
    if bayes_stats and result.events:
        if on_step:
            on_step("Bayesian statistics")
        from nano_ext.analysis.bayes_stats import summarize_event_statistics
        stats = summarize_event_statistics(
            result.events, observation_time=result.signal_data.duration_sec,
        )
    return result, stats


def start_job(job: Job, **kwargs) -> threading.Thread:
    """Run :func:`run_analysis` in a background thread, updating *job*."""

    def on_step(name: str) -> None:
        job.step = name
        job.steps.append((name, time.time() - job.started))

    def target() -> None:
        try:
            job.result, job.stats = run_analysis(on_step=on_step, **kwargs)
            job.status = "done"
        except Exception as exc:  # reported in the UI
            job.error = f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
            job.status = "error"
        finally:
            job.finished = time.time()

    job.status, job.step, job.steps, job.error = "running", "Starting", [], ""
    job.result, job.stats, job.config = None, None, kwargs.get("config")
    job.started, job.finished = time.time(), 0.0
    thread = threading.Thread(target=target, name="nano-ext-analysis", daemon=True)
    thread.start()
    return thread


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

def events_rows(result) -> list[dict]:
    """One table row per event (display units: ms, file time in s)."""
    rows = []
    for i, ev in enumerate(result.events):
        rows.append({
            "id": i,
            "start_s": round(ev.start_time, 6),
            "duration_ms": round(ev.duration * 1e3, 4),
            "depth": round(ev.depth, 3),
            "rel_depth": round(ev.relative_depth, 4),
            "levels": ev.n_levels,
            "direction": ev.direction.value,
            "cluster": ev.cluster_id if ev.cluster_id is not None else "",
            "hmm_states": ev.hmm_result.n_states if ev.hmm_result is not None else "",
        })
    return rows


def events_csv(result) -> str:
    from nano_ext.outputs.csv_writer import write_events_to_csv
    return _csv_text(write_events_to_csv, result)


def sublevels_csv(result) -> str:
    from nano_ext.outputs.csv_writer import write_sublevels_to_csv
    return _csv_text(write_sublevels_to_csv, result)


def _csv_text(writer, result) -> str:
    buf = io.StringIO()
    writer(result.events, buf, result.signal_data.sampling_rate)
    return buf.getvalue()


def clusters_csv(result) -> Optional[str]:
    if result.cluster_result is None:
        return None
    return result.cluster_result.summary_table().to_csv(index=False)


def stats_json(stats: Optional[dict]) -> Optional[str]:
    return None if stats is None else json.dumps(stats, indent=2)


def summary_lines(result, config: Optional[DetectionConfig] = None) -> list[str]:
    lines = result.summary().splitlines()
    if config is not None:
        lines.append(
            "Methods:         threshold={} · sub-levels={} · HMM={}".format(
                config.threshold_method,
                config.sublevel_method,
                config.hmm_method if config.hmm_analysis else "off",
            )
        )
    return lines


def effective_thresholds(result) -> tuple[float, float]:
    """(down, up) thresholds in residual units, as used by detect_events."""
    from nano_ext.outputs.event_viewer import _eff_thresholds
    return _eff_thresholds(result)


def residual_sample(result, n: int = 200_000, seed: int = 0) -> np.ndarray:
    """Random subsample of the residual for histograms."""
    res = result.baseline_result.residual
    if len(res) <= n:
        return np.asarray(res, dtype=np.float64)
    idx = np.sort(np.random.default_rng(seed).choice(len(res), n, replace=False))
    return np.asarray(res[idx], dtype=np.float64)
