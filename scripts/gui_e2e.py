"""End-to-end check of the GUI in a real browser (manual / release QA).

Generates a demo recording (two event populations and a zap artifact),
starts ``nano-ext gui`` on it, drives a headless Chromium through the main
workflow and saves screenshots:

    pip install "nano-ext[gui]" playwright && playwright install chromium
    python scripts/gui_e2e.py [--out DIR] [--duration 30]

Exits non-zero if any step fails, the server returns an HTTP error, or the
page logs a JavaScript error.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def make_demo(path: Path, duration: float, sampling_rate: float = 250_000) -> int:
    import numpy as np
    from pyabf import abfWriter
    from nano_ext.testing.synthetic import SyntheticEventSpec, generate_synthetic_signal

    rng = np.random.default_rng(3)
    events, t = [], 0.2
    while t < duration - 0.5:
        if rng.random() < 0.5:
            events.append(SyntheticEventSpec(t, [(rng.exponential(0.0008) + 0.0003, 140.0)]))
        else:
            events.append(SyntheticEventSpec(t, [(rng.exponential(0.004) + 0.001, 80.0), (0.002, 110.0)]))
        t += rng.exponential(0.12) + 0.02
    sig = generate_synthetic_signal(duration_sec=duration, sampling_rate=sampling_rate,
                                    events=events, seed=1).signal_data.signal.astype(np.float32)
    a, b = int(0.4 * duration * sampling_rate), int((0.4 * duration + 0.5) * sampling_rate)
    sig[a:b] += 400 * np.sin(np.linspace(0, 60, b - a))  # zap artifact
    abfWriter.writeABF1(sig[None, :], str(path), sampling_rate)
    # Negative control: same noise, no events.
    ctrl = generate_synthetic_signal(duration_sec=min(duration, 5.0), sampling_rate=sampling_rate,
                                     events=[], seed=2).signal_data.signal.astype(np.float32)
    abfWriter.writeABF1(ctrl[None, :], str(path.with_name("control.abf")), sampling_rate)
    return len(events)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=None, help="Folder for the demo file and screenshots")
    ap.add_argument("--duration", type=float, default=30.0)
    ap.add_argument("--port", type=int, default=8057)
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    out = Path(args.out or tempfile.mkdtemp(prefix="nano_ext_e2e_"))
    out.mkdir(parents=True, exist_ok=True)
    abf = out / "demo.abf"
    n_true = make_demo(abf, args.duration)
    print(f"demo: {abf} ({n_true} events)")

    url = f"http://127.0.0.1:{args.port}/"
    server = subprocess.Popen(
        [sys.executable, "-m", "nano_ext.cli", "gui", str(abf), "--no-browser", "--port", str(args.port)],
        stdout=open(out / "server.log", "w"), stderr=subprocess.STDOUT,
    )
    problems: list[str] = []
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(url, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1500, "height": 950}, accept_downloads=True)
            page.on("response", lambda r: problems.append(f"HTTP {r.status} {r.url}") if r.status >= 400 else None)
            page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
            page.on("console", lambda m: problems.append(f"console: {m.text}") if m.type == "error" else None)

            page.goto(url)
            page.wait_for_selector("#file-info:has-text('kHz')", timeout=30000)

            # System file dialog: headless here, so it must report that it is
            # unavailable and open the in-page folder list instead.
            env_display = os.environ.get("DISPLAY")
            if not env_display:
                page.click("#browse-native")
                page.wait_for_selector("#dialog-status:has-text('unavailable')", timeout=60000)
                page.wait_for_function("document.querySelector('#browser-details').open === true")
            else:
                page.evaluate("document.querySelector('#browser-details').open = true")

            # In-page folder list: up one level, back into the demo folder,
            # then click the recording -> it loads without pressing Load.
            page.locator("#browser-list .entry", has_text="⬆").click()
            page.wait_for_function(f"document.querySelector('#folder').value === {str(out.parent)!r}")
            page.locator("#browser-list .entry", has_text=out.name).click()
            page.wait_for_function(f"document.querySelector('#folder').value === {str(out)!r}")
            page.locator("#browser-list .entry.file", has_text="demo.abf").click()
            page.wait_for_selector("#load-status:has-text('Loaded demo.abf')", timeout=120000)
            page.wait_for_selector("#view-info:has-text('points drawn')", timeout=60000)
            page.screenshot(path=str(out / "01_loaded.png"))

            # Negative control by path.
            page.fill("#control-path", str(out / "control.abf"))
            page.press("#control-path", "Enter")
            page.wait_for_selector("#control-info:has-text('Control: control.abf')", timeout=60000)

            # Exclude the artifact by dragging across it in "Select range" mode.
            a = 0.4 * args.duration - 0.1
            page.locator("#mouse-mode label", has_text="Select range").click()
            page.wait_for_function("document.querySelector('#wave .js-plotly-plot')._fullLayout.dragmode === 'select'",
                                   timeout=20000)
            x0, x1, y = page.evaluate(f"""() => {{
                const gd = document.querySelector('#wave .js-plotly-plot');
                const xa = gd._fullLayout.xaxis, ya = gd._fullLayout.yaxis;
                const box = gd.getBoundingClientRect();
                return [box.left + xa._offset + xa.l2p({a}), box.left + xa._offset + xa.l2p({a + 0.7}),
                        box.top + ya._offset + ya._length / 2];
            }}""")
            page.mouse.move(x0, y)
            page.mouse.down()
            page.mouse.move((x0 + x1) / 2, y, steps=5)
            page.mouse.move(x1, y, steps=5)
            page.mouse.up()
            page.wait_for_selector("#selection-bar:has-text('Selected')", timeout=20000)
            print(page.inner_text("#selection-label"))
            page.screenshot(path=str(out / "01b_selection.png"))
            page.click("#sel-exclude")
            page.wait_for_selector("#ranges td[data-dash-column='start']", timeout=10000)
            page.wait_for_selector("#selection-bar", state="hidden", timeout=10000)
            assert page.is_checked("#range-mode input[value='exclude']"), "range mode not set to exclude"
            print("excluded:", page.inner_text("#ranges").split())
            page.locator("#mouse-mode label", has_text="Zoom").click()

            page.click("text=Methods")
            page.click("text=Cluster events into populations")
            page.click("text=Bayesian statistics (rate, dwell times)")
            page.click("#run")
            page.wait_for_selector("#run-status:has-text('Done')", timeout=600000)
            page.wait_for_selector("#summary:has-text('Events detected')", timeout=30000)
            print(page.inner_text("#run-status"))
            page.mouse.move(5, 5)  # no hover tooltip in the screenshot
            page.add_style_tag(content=".hoverlayer { display: none !important; }")
            page.wait_for_timeout(500)
            page.screenshot(path=str(out / "02_results.png"), full_page=True)

            tab = lambda name: page.locator("#tabs .tab", has_text=name).click()  # noqa: E731
            tab("Events")
            page.wait_for_selector("#events input[type=radio]", timeout=10000)
            page.locator("#events input[type=radio]").nth(1).check()
            page.wait_for_timeout(2000)
            page.screenshot(path=str(out / "03_event.png"), full_page=True)
            tab("Scatter")
            page.wait_for_timeout(1500)
            page.screenshot(path=str(out / "04_scatter.png"), full_page=True)
            tab("Dwell")
            page.wait_for_timeout(1500)
            print(page.inner_text("#stats-text"))
            page.screenshot(path=str(out / "05_stats.png"), full_page=True)
            tab("Noise")
            page.click("#psd-btn")
            page.wait_for_selector("#psd .main-svg", timeout=60000)
            tab("Export")
            with page.expect_download() as dl:
                page.click("#dl-events")
            dl.value.save_as(str(out / dl.value.suggested_filename))
            browser.close()
    except Exception as exc:
        problems.append(f"step failed: {exc}")
    finally:
        server.terminate()
        server.wait(10)

    print(f"screenshots and downloads in {out}")
    if problems:
        print("PROBLEMS:\n  " + "\n  ".join(problems))
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
