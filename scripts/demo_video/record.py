"""Record the GUI demo (step 2 of 3): scripted browser session + cue log.

Starts ``nano-ext gui`` on the folder written by ``make_data.py``, drives a
headless Chromium through the workflow (open → zoom → trim → methods → run
→ results) and saves

    <work>/frames/*.jpg   page screenshots at 1.5× (≈12 fps while active)
    <work>/log.json       timestamps of frames, mouse moves, clicks, camera
                          targets, caption ids and fast-forward segments

The mouse cursor is not in the screenshots; ``compose.py`` draws it from the
log so it stays smooth at the output frame rate.

    pip install playwright && playwright install chromium
    python scripts/demo_video/record.py [--work scripts/demo_video/_work] [--port 8061]

The run takes ~4 minutes (the analysis of the 10-minute file is ~70 s).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright

HERE = Path(__file__).parent
W, H, DSF = 1600, 900, 1.5  # viewport (CSS px) and capture scale

log = {"viewport": [W, H], "dsf": DSF, "frames": [], "mouse": [], "clicks": [], "cams": [],
       "caps": [], "speed": [], "marks": []}
T = time.time
CAP_DT = 0.0  # pause between captures (1 s while fast-forwarding a long wait)

# Injected into every page: hide Plotly chrome and show the end of long paths.
PAGE_JS = r"""
(() => {
  const init = () => {
    const st = document.createElement('style');
    st.textContent = '.modebar-container{display:none!important} .hoverlayer{display:none!important} .plotly-notifier{display:none!important}';
    document.head.appendChild(st);
    // show the end of long paths (…/nanopore/2026-09-28) in unfocused inputs
    setInterval(() => document.querySelectorAll('#folder, #path').forEach(el => {
      if (document.activeElement !== el) el.scrollLeft = el.scrollWidth; }), 100);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
"""


class Rec:
    def __init__(self, page):
        self.page, self.mx, self.my = page, W / 2, H / 2

    def cam(self, rect=None, dur=1.2):
        """Move the virtual camera to rect (x, y, w, h in CSS px; None = full view)."""
        log["cams"].append({"t": T(), "rect": rect or [0, 0, W, H], "dur": dur})

    def cap(self, cid=""):
        """Show caption *cid* (texts live in compose.py; "" hides the caption)."""
        log["caps"].append({"t": T(), "id": cid})

    def speed(self, factor, badge=""):
        global CAP_DT
        CAP_DT = 1.0 if factor >= 10 else 0.0
        log["speed"].append({"t": T(), "factor": factor, "badge": badge})

    def mark(self, name):
        log["marks"].append({"t": T(), "name": name})

    async def glide(self, x, y, dur=0.7):
        x0, y0 = self.mx, self.my
        n = max(2, int(dur * 60))
        for i in range(1, n + 1):
            u = i / n
            e = u * u * (3 - 2 * u)
            await self.move(x0 + (x - x0) * e, y0 + (y - y0) * e)
            await asyncio.sleep(dur / n)
        self.mx, self.my = x, y

    async def move(self, x, y):
        log["mouse"].append([T(), x, y])
        await self.page.mouse.move(x, y)

    async def down(self):
        log["clicks"].append([T(), self.mx, self.my])
        await self.page.mouse.down()

    async def box(self, sel):
        loc = self.page.locator(sel).first if isinstance(sel, str) else sel
        await loc.scroll_into_view_if_needed()
        b = await loc.bounding_box()
        return b

    async def click(self, sel, dur=0.7, pause=0.25):
        b = await self.box(sel)
        await self.glide(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, dur)
        await asyncio.sleep(pause)
        await self.down()
        await asyncio.sleep(0.08)
        await self.page.mouse.up()
        await asyncio.sleep(0.3)

    async def drag(self, x0, y0, x1, y1, dur=1.0):
        await self.glide(x0, y0, 0.7)
        await asyncio.sleep(0.2)
        await self.down()
        n = int(dur * 60)
        for i in range(1, n + 1):
            u = i / n
            e = u * u * (3 - 2 * u)
            await self.move(x0 + (x1 - x0) * e, y0 + (y1 - y0) * e)
            await asyncio.sleep(dur / n)
        self.mx, self.my = x1, y1
        await asyncio.sleep(0.15)
        await self.page.mouse.up()

    async def plot(self):
        """Plot area of the waveform (viewport px) and axis ranges."""
        return await self.page.evaluate("""() => {
            const gd = document.querySelector('#wave .js-plotly-plot');
            const xa = gd._fullLayout.xaxis, ya = gd._fullLayout.yaxis, b = gd.getBoundingClientRect();
            return {x: b.left + xa._offset, y: b.top + ya._offset, w: xa._length, h: ya._length,
                    gx: b.left, gy: b.top, gw: b.width, gh: b.height, xr: xa.range};
        }""")

    async def xpix(self, t):
        return await self.page.evaluate(f"""() => {{
            const gd = document.querySelector('#wave .js-plotly-plot'); const xa = gd._fullLayout.xaxis;
            return gd.getBoundingClientRect().left + xa._offset + xa.l2p({t}); }}""")

    async def view_text(self):
        return await self.page.inner_text("#view-info")

    async def wait_redraw(self, before, timeout=30):
        t0 = T()
        while T() - t0 < timeout:
            if (await self.view_text()) != before:
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.6)

    async def zoom_to(self, t0, t1, dur=1.0):
        if await self.page.evaluate("window.scrollY") > 0:
            await self.scroll_to(0, 0.6)
        p = await self.plot()
        x0, x1 = await self.xpix(t0), await self.xpix(t1)
        y = p["y"] + p["h"] * 0.5
        before = await self.view_text()
        await self.drag(x0, y - 3, x1, y + 3, dur)
        await self.wait_redraw(before)

    async def scroll_to(self, y, dur=1.0):
        await self.page.evaluate(f"window.scrollTo({{top: {y}, behavior: 'smooth'}})")
        await asyncio.sleep(dur)


async def main(work: Path, port: int):
    data = work / "nanopore"
    if not (data / "2026-09-28" / "pore07_10min.abf").exists():
        sys.exit(f"{data} is missing: run make_data.py first")
    frames = work / "frames"
    shutil.rmtree(frames, ignore_errors=True)
    frames.mkdir(parents=True)
    url = f"http://127.0.0.1:{port}/"
    server = subprocess.Popen([sys.executable, "-m", "nano_ext.cli", "gui", "--folder", str(data),
                               "--no-browser", "--port", str(port)],
                              stdout=open(work / "server.log", "w"), stderr=subprocess.STDOUT)
    for _ in range(120):
        try:
            urllib.request.urlopen(url, timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    status = None
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            ctx = await browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=1)
            await ctx.add_init_script(PAGE_JS)
            page = await ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(url)
            await page.wait_for_selector("#browser-details summary")
            await asyncio.sleep(1.5)

            cdp = await ctx.new_cdp_session(page)

            running = True

            async def capture():
                while running:
                    t0 = T()
                    lm = await cdp.send("Page.getLayoutMetrics")
                    vp = lm["cssVisualViewport"]
                    shot = await cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": 88,
                        "clip": {"x": vp["pageX"], "y": vp["pageY"], "width": W, "height": H, "scale": DSF}})
                    name = f"{len(log['frames']):06d}.jpg"
                    (frames / name).write_bytes(base64.b64decode(shot["data"]))
                    log["frames"].append([t0, name])
                    if CAP_DT:
                        await asyncio.sleep(CAP_DT)

            cap_task = asyncio.ensure_future(capture())
            await asyncio.sleep(1.0)
            r = Rec(page)
            await r.move(r.mx, r.my)
            r.mark("start")
            r.cam(None, 0)
            await asyncio.sleep(1.5)

            # ---- 1. open a recording ------------------------------------------------
            r.cap("open")
            r.cam([0, 0, 720, 405], 1.4)
            await asyncio.sleep(1.0)
            await r.click("#browser-details summary")
            await asyncio.sleep(0.6)
            await r.click(page.locator("#browser-list .entry", has_text="2026-09-28"))
            await page.wait_for_selector("#browser-list .entry.file >> text=pore07_10min.abf")
            await asyncio.sleep(0.8)
            await r.click(page.locator("#browser-list .entry.file", has_text="pore07_10min.abf"))
            r.speed(4, "▶▶ ×4")
            await page.wait_for_selector("#load-status:has-text('Loaded pore07_10min.abf')", timeout=180000)
            await page.wait_for_selector("#view-info:has-text('points drawn')", timeout=60000)
            await asyncio.sleep(0.4)
            r.speed(1)
            r.cam(None, 1.4)
            await asyncio.sleep(2.0)

            # ---- 2. zoom + trim ------------------------------------------------------------
            r.cap("zoom")
            p = await r.plot()
            PLOT_CAM = [p["gx"] - 20, p["gy"] - 60, p["gw"] + 40, (p["gw"] + 40) * 9 / 16]
            r.cam(PLOT_CAM, 1.2)
            await asyncio.sleep(1.0)
            await r.zoom_to(228, 262)
            await asyncio.sleep(0.6)

            r.cap("trim")
            await r.click(page.locator("#mouse-mode label", has_text="Select range"))
            await page.wait_for_function(
                "document.querySelector('#wave .js-plotly-plot')._fullLayout.dragmode === 'select'")
            await asyncio.sleep(0.3)
            p = await r.plot()
            x0, x1 = await r.xpix(242.7), await r.xpix(243.9)
            y = p["y"] + p["h"] * 0.35
            await r.drag(x0, y, x1, y + 2, 0.9)
            await page.wait_for_selector("#selection-bar:has-text('Selected')", timeout=20000)
            await asyncio.sleep(0.4)
            b = await r.box("#selection-bar")
            r.cam([b["x"] - 20, b["y"] - 40, 900, 506], 1.0)
            await asyncio.sleep(1.2)
            await r.click("#sel-exclude")
            await page.wait_for_selector("#selection-bar", state="hidden", timeout=10000)
            await asyncio.sleep(0.4)
            p = await r.plot()
            PLOT_CAM = [p["gx"] - 20, p["gy"] - 60, p["gw"] + 40, (p["gw"] + 40) * 9 / 16]
            r.cam(PLOT_CAM, 1.0)
            await asyncio.sleep(1.4)
            await r.click(page.locator("#mouse-mode label", has_text="Zoom"))
            await asyncio.sleep(0.3)

            r.cap("samples")
            await r.zoom_to(249.3, 250.8)
            await asyncio.sleep(0.8)
            await r.zoom_to(249.996, 250.0115, 1.0)
            await asyncio.sleep(0.3)
            p = await r.plot()
            x0, x1 = await r.xpix(249.9985), await r.xpix(250.0095)
            r.cam([x0 - 60, p["y"] - 10, (x1 - x0) + 120, ((x1 - x0) + 120) * 9 / 16], 1.4)
            r.cap("event")
            await asyncio.sleep(3.0)
            r.cam(None, 1.2)
            await asyncio.sleep(0.4)
            before = await r.view_text()
            await r.click("#reset-view")
            await r.wait_redraw(before)
            await asyncio.sleep(0.5)

            # ---- 4. methods & run ----------------------------------------------------------
            r.cap("methods")
            meth = page.locator("summary", has_text="Methods")
            await meth.evaluate("e => e.scrollIntoView({behavior: 'smooth', block: 'start'})")
            await asyncio.sleep(1.0)
            await r.click(meth)
            await asyncio.sleep(0.8)
            b = await r.box(meth)
            r.cam([0, max(0, b["y"] - 60), 800, 450], 1.2)
            await asyncio.sleep(1.4)
            await r.click(page.locator("#extras label", has_text="Cluster events"))
            await asyncio.sleep(0.3)
            await r.click(page.locator("#extras label", has_text="Bayesian statistics"))
            await asyncio.sleep(0.8)
            await r.scroll_to(0, 0.6)
            b = await r.box("#run")
            r.cam([0, H - 450, 800, 450], 1.0)
            await asyncio.sleep(0.8)
            await r.click("#run")
            r.cap("run")
            await asyncio.sleep(1.5)
            r.cam(None, 1.2)
            await asyncio.sleep(1.4)
            r.speed(30, "▶▶ ×30")
            await page.wait_for_selector("#run-status:has-text('Done')", timeout=900000)
            await page.wait_for_selector("#summary:has-text('Events detected')", timeout=60000)
            r.speed(1)
            status = await page.inner_text("#run-status")
            await asyncio.sleep(0.8)
            b = await r.box("#run-status")
            r.cam([0, H - 405, 720, 405], 1.0)
            await asyncio.sleep(2.0)

            # ---- 5. results ----------------------------------------------------------------------
            r.cap("markers")
            p = await r.plot()
            r.cam([p["gx"] - 20, p["gy"] - 60, p["gw"] + 40, (p["gw"] + 40) * 9 / 16], 1.2)
            await asyncio.sleep(0.8)
            await r.zoom_to(238, 262)
            await asyncio.sleep(2.0)
            before = await r.view_text()
            await r.click("#reset-view")
            await r.wait_redraw(before)
            r.cam(None, 1.0)

            await r.glide(W * 0.62, H * 0.8, 0.6)
            tabs_y = await page.evaluate("document.querySelector('#tabs').getBoundingClientRect().top + window.scrollY")
            await r.scroll_to(tabs_y - 12, 1.2)
            r.cap("histogram")
            b = await r.box("#hist")
            r.cam([b["x"] - 20, b["y"] - 80, b["width"] + 40, (b["width"] + 40) * 9 / 16], 1.2)
            await asyncio.sleep(3.2)
            r.cam(None, 1.0)
            await asyncio.sleep(0.6)

            r.cap("events")
            await r.click(page.locator("#tabs .tab", has_text="Events"))
            await page.wait_for_selector("#events input[type=radio]", timeout=20000)
            await asyncio.sleep(0.6)
            await r.click(page.locator("#events input[type=radio]").nth(5))
            await page.wait_for_function(
                "(document.querySelector('#event-detail .js-plotly-plot')?.data || []).length > 0", timeout=60000)
            await asyncio.sleep(0.8)
            b = await r.box("#event-detail")
            r.cam([b["x"] - 20, b["y"] - 30, b["width"] + 40, (b["width"] + 40) * 9 / 16], 1.2)
            await asyncio.sleep(2.2)
            r.cam(None, 1.0)
            await asyncio.sleep(0.8)
            await r.scroll_to(tabs_y - 12, 0.8)

            r.cap("populations")
            await r.click(page.locator("#tabs .tab", has_text="Scatter"))
            await asyncio.sleep(2.5)
            b = await r.box("#scatter")
            r.cam([b["x"] - 10, b["y"] - 60, b["width"] + 20, (b["width"] + 20) * 9 / 16], 1.2)
            await asyncio.sleep(2.6)
            r.cam(None, 1.0)
            await asyncio.sleep(0.6)

            r.cap("bayes")
            await r.click(page.locator("#tabs .tab", has_text="Dwell"))
            await asyncio.sleep(2.5)
            await r.glide(W * 0.9, H * 0.3, 0.5)
            await asyncio.sleep(2.8)

            r.cap("export")
            await r.click(page.locator("#tabs .tab", has_text="Export"))
            await asyncio.sleep(1.8)
            r.cap("")
            await r.scroll_to(0, 1.2)
            await asyncio.sleep(1.5)
            r.mark("end")
            await asyncio.sleep(0.5)
            running = False
            await cap_task
            await asyncio.sleep(0.5)
            log["status"] = status
            log["errors"] = errors
            await browser.close()
    finally:
        server.terminate()
        server.wait(10)
        (work / "log.json").write_text(json.dumps(log, indent=1))
        print("frames", len(log["frames"]), "status", status, "errors", log.get("errors"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work", default=str(HERE / "_work"), help="Working folder (default: %(default)s)")
    ap.add_argument("--port", type=int, default=8061)
    a = ap.parse_args()
    asyncio.run(main(Path(a.work), a.port))
