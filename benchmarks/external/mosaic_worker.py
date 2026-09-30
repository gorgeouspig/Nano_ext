"""Run MOSAIC (NIST) on one recording; executed inside benchmarks/_external/venv-mosaic.

    python mosaic_worker.py SIGNAL.npy SAMPLING_RATE PARAMS_JSON

Event detection is MOSAIC's ``eventSegment`` partitioner; each event is then
fitted with ``cusumPlus`` (CUSUM+), which reports the sub-levels. Only events
MOSAIC marks as ``normal`` are returned (as a MOSAIC user would analyse them).
PARAMS_JSON: {"eventSegment": {...}, "cusumPlus": {...}} overrides of the
default settings. Prints one JSON line: list of events with start/end (s),
depth (pA), n_levels and level_bounds (s).
"""

import base64
import json
import os
import sqlite3
import sys
import tempfile

import numpy as np

# No usage telemetry: importing mosaic.utilities.ga fetches analytics settings
# from the web unless a fresh cache exists in the temp directory, and its
# decorators post events. Give it a private temp dir with analytics disabled.
tempfile.tempdir = tempfile.mkdtemp(prefix="mosaic-")
with open(os.path.join(tempfile.tempdir, ".ga"), "w") as _f:
    json.dump({"gaenable": "False", "gaid": "", "gaurl": "", "gamode": ""}, _f)

import mosaic.utilities.ga as _ga  # noqa: E402

_ga._gaPost = lambda *a, **k: None

from mosaic.apps.SingleChannelAnalysis import run_eventpartition  # noqa: E402
from mosaic.partition.eventSegment import eventSegment  # noqa: E402
from mosaic.process.cusumPlus import cusumPlus  # noqa: E402
from mosaic.settings import __settings__  # noqa: E402
from mosaic.trajio.binTrajIO import binTrajIO  # noqa: E402


def _num(v):
    """The default settings are strings; the readers expect numbers where numeric."""
    if isinstance(v, str):
        try:
            f = float(v)
            return int(f) if f.is_integer() and "." not in v and "e" not in v.lower() else f
        except ValueError:
            return v
    return v


def _real_list(v):
    """MOSAIC stores REAL_LIST columns as base64-encoded float64 arrays."""
    if isinstance(v, (bytes, str)):
        return np.frombuffer(base64.b64decode(v), dtype=np.float64)
    return np.atleast_1d(np.asarray(v, dtype=np.float64))


def main():
    signal_path, sr, params = sys.argv[1], float(sys.argv[2]), json.loads(sys.argv[3])
    x = np.load(signal_path).astype(np.float64)
    # MOSAIC's partitioner fails on an empty final data block (length a multiple of
    # its block size); append the last 20 ms of the trace (open pore in every
    # benchmark recording), mirrored, so that block is never empty or tiny.
    tail = int(0.02 * sr)
    x = np.concatenate([x, x[-tail:][::-1]])
    settings = json.loads(__settings__)
    settings["binTrajIO"].update({"SamplingFrequency": sr, "filter": "*.bin",
                                  "ColumnTypes": "[('curr_pA', 'float64')]"})
    for section, values in params.items():
        settings[section].update(values)
    settings = {sec: {k: _num(v) for k, v in vals.items()} for sec, vals in settings.items()}
    with tempfile.TemporaryDirectory() as d:
        x.tofile(os.path.join(d, "signal.bin"))
        with open(os.path.join(d, ".settings"), "w") as f:
            json.dump(settings, f)
        devnull = os.open(os.devnull, os.O_WRONLY)
        saved = os.dup(1)
        os.dup2(devnull, 1)  # MOSAIC prints progress to stdout
        try:
            run_eventpartition(d, binTrajIO, None, eventSegment, cusumPlus, "events.sqlite")
        finally:
            os.dup2(saved, 1)
        con = sqlite3.connect(os.path.join(d, "events.sqlite"))
        cols = [r[1] for r in con.execute("PRAGMA table_info(metadata)")]
        rows = con.execute("SELECT * FROM metadata").fetchall()
        con.close()
    events = []
    for row in rows:
        r = dict(zip(cols, row))
        if r.get("ProcessingStatus") != "normal":
            continue
        delay, steps, res = (_real_list(r[k]) for k in ("EventDelay", "CurrentStep", "StateResTime"))
        t0 = (r["AbsEventStart"] - r["EventStart"]) / 1000.0  # ms -> s, start of the event chunk
        edges = t0 + delay / 1000.0
        n_levels = len(edges) - 1
        depth = float(-np.sum(steps[:n_levels] * res[:n_levels]) / np.sum(res[:n_levels])) if n_levels else 0.0
        events.append({"start": float(edges[0]), "end": float(edges[-1]), "depth": depth,
                       "n_levels": int(n_levels), "level_bounds": [float(e) for e in edges[1:-1]]})
    print(json.dumps(events))


if __name__ == "__main__":
    main()
