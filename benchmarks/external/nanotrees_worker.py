"""Fit sub-levels with Nano Trees (Poriscope's NanoTrees event fitter).

Executed inside benchmarks/_external/venv-poriscope:

    python nanotrees_worker.py INPUT.npz SETTINGS_JSON

INPUT.npz holds ``signal``, ``sr`` and ``chunks`` (N × 4 integer array: chunk
start, chunk end, padding before, padding after; the event lies between the
paddings).
Nano Trees is only a level fitter: event windows come from another detector.
The plugin is used as in Poriscope's own unit tests (instance created without
its GUI/event-loader plumbing, settings injected). Prints one JSON line: per
chunk, the fitted level edges (samples, relative to the chunk start, including
the baseline levels before and after) and level currents, or null if the fit
failed.
"""

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from poriscope.plugins.eventfitters.NanoTrees import NanoTrees  # noqa: E402

DEFAULTS = {  # the plugin's own defaults (get_empty_settings)
    "Smallest Significant Sublevel": 600.0,  # pA
    "Time Scaling": 1.1,
    "Exceptional Sublevel Sensitivity": 0.3,
}


def main():
    data = np.load(sys.argv[1])
    settings = {**DEFAULTS, **json.loads(sys.argv[2])}
    x, sr = data["signal"].astype(np.float64), float(data["sr"])
    nt = object.__new__(NanoTrees)
    nt.sublevel_metadata, nt.eventfitting_status, nt.event_lengths = {}, {}, {}
    nt.eventloader = None
    nt.logger = NanoTrees.logger
    nt.settings = {k: {"Value": float(v)} for k, v in settings.items()}
    out = []
    for start, end, pad_before, pad_after in data["chunks"]:
        chunk = x[int(start):int(end)]
        try:
            res = nt._locate_sublevel_transitions(chunk, sr, int(pad_before), int(pad_after), None, None)
            out.append({"edges": [int(e) for e in res], "currents": [float(h) for h in res.self.sublevels.heights]})
        except Exception as exc:  # a failed fit is a result too
            out.append({"error": f"{type(exc).__name__}: {exc}"})
    print(json.dumps(out))


if __name__ == "__main__":
    main()
