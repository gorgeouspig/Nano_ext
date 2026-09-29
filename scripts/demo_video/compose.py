"""Edit the recording into the demo video (step 3 of 3).

Reads ``<work>/frames`` and ``<work>/log.json`` from ``record.py`` and renders

* a 1920×1080, 30 fps MP4: title card, the page in a window on a gradient
  that the virtual camera zooms into and pulls back from, a drawn mouse
  cursor with click ripples, captions (texts in ``CAPTIONS`` below), waits
  sped up (analysis ×30, idle moments ×3) and an end card;
* optionally a short GIF of the zoom-to-one-event scene for the README.

    pip install imageio-ffmpeg        # provides an ffmpeg binary
    python scripts/demo_video/compose.py [--work DIR] [--out demo.mp4] [--gif demo.gif]
    python scripts/demo_video/compose.py --preview 5,30,60   # stills, to check framing

Rendering takes ~6 minutes on 4 cores.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).parent
OW, OH, FPS = 1920, 1080, 30
BAR = 34  # fake browser title bar (CSS px), drawn above the page
BASE = 1.4  # overall pace of the interactive parts
IDLE = 3.0  # extra speed-up while nothing moves (waiting for redraws etc.)
TITLE_LEN, XFADE, END_LEN = 2.8, 0.6, 4.0

# Caption ids used by record.py -> (title, subtitle)
CAPTIONS = {
    "open": ("Open a recording", "ABF · 10 min · 250 kHz · 150 M samples"),
    "zoom": ("Zoom in on the raw trace", "Min/max downsampling keeps 150 M samples responsive"),
    "trim": ("Trim artifacts with the mouse", "Select a range, then exclude it — or analyze only it"),
    "samples": ("Down to single samples", "From 10 minutes down to 15 ms, without leaving the page"),
    "event": ("Every sample is there", "A two-level event: 4.5 ms at 80 pA, then 2.5 ms at 115 pA"),
    "methods": ("Bayesian nonparametric defaults", "Dirichlet-process mixtures pick the number of levels for you"),
    "run": ("Run the whole pipeline", "Filter → baseline → threshold → sub-levels → populations"),
    "markers": ("Every event, marked on the trace", ""),
    "histogram": ("Current histogram with a DP mixture fit", "The number of current levels is inferred, not preset"),
    "events": ("Inspect single events", "Sub-levels fitted per event"),
    "populations": ("Populations found automatically", "Dwell time vs. blockade, clustered with a DP mixture"),
    "bayes": ("Bayesian statistics", "Capture rate and dwell-time mixtures with credible intervals"),
    "export": ("Export CSV / JSON", ""),
}
GIF_SCENE = ("samples", "event")  # README GIF: from this caption until the camera leaves that one


def _font_dir() -> Path:
    import matplotlib  # a nano-ext dependency that ships the DejaVu fonts

    return Path(matplotlib.get_data_path()) / "fonts" / "ttf"


@lru_cache(maxsize=None)
def font(name: str, size: int):
    return ImageFont.truetype(str(_font_dir() / name), size)


BOLD, REG, MONO = "DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "DejaVuSansMono.ttf"


def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def load_log(work: Path):
    global log, W, H, frames, ftimes, t_start, t_end, FULL, cams, caps, mouse, mtimes, clicks, FRAMES_DIR
    log = json.loads((work / "log.json").read_text())
    FRAMES_DIR = work / "frames"
    W, H = log["viewport"]
    frames = sorted(log["frames"])
    ftimes = [f[0] for f in frames]
    t_start = next(m["t"] for m in log["marks"] if m["name"] == "start")
    t_end = next(m["t"] for m in log["marks"] if m["name"] == "end")
    full_h = (H + BAR) * 1.12
    FULL = [W / 2 - full_h * 16 / 9 / 2, -BAR - (full_h - H - BAR) / 2, full_h * 16 / 9, full_h]
    cams = [(c["t"], norm(c["rect"]), c["dur"]) for c in log["cams"]]
    caps = [(c["t"], *CAPTIONS.get(c["id"], ("", ""))) for c in log["caps"]]
    mouse = log["mouse"]
    mtimes = [m[0] for m in mouse]
    clicks = log["clicks"]
    build_timeline()


# ---------------- camera --------------------------------------------------------------
def norm(rect):
    x, y, w, h = rect
    if [x, y, w, h] == [0, 0, W, H]:
        return FULL
    cx, cy = x + w / 2, y + h / 2
    if w / h > 16 / 9:
        h = w * 9 / 16
    else:
        w = h * 16 / 9
    w, h = min(w, W), min(h, H)
    x = min(max(cx - w / 2, 0), W - w)
    y = min(max(cy - h / 2, 0), H - h)
    return [x, y, w, h]


def ease(u):
    u = min(max(u, 0.0), 1.0)
    return 4 * u ** 3 if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2


def lerp(a, b, e):
    # interpolate zoom in log space for a natural feel
    wa, wb = a[2], b[2]
    w = math.exp(math.log(wa) + (math.log(wb) - math.log(wa)) * e)
    k = (w - wa) / (wb - wa) if abs(wb - wa) > 1e-6 else e
    cxa, cya = a[0] + a[2] / 2, a[1] + a[3] / 2
    cxb, cyb = b[0] + b[2] / 2, b[1] + b[3] / 2
    cx, cy = cxa + (cxb - cxa) * k, cya + (cyb - cya) * k
    h = w * 9 / 16
    return [cx - w / 2, cy - h / 2, w, h]


def camera(t):
    frm, to, t0, dur = FULL, FULL, float("-inf"), 0
    for tc, rect, d in cams:
        if tc > t:
            break
        at = lerp(frm, to, ease((tc - t0) / dur)) if dur > 0 else to
        frm, to, t0, dur = at, rect, tc, d
    return lerp(frm, to, ease((t - t0) / dur)) if dur > 0 else to


# ---------------- time remap (fast-forward) --------------------------------------------
def build_timeline():
    """Map video time to recording time: analysis waits ×30, idle moments ×IDLE."""
    global segs, seg_t, ts, vt, busy, VID_LEN
    segs = [(t_start, BASE, "")]  # (real_t0, speed, badge)
    for s in log["speed"]:
        segs.append((s["t"], s["factor"] if s["factor"] > 1 else BASE, s["badge"] if s["factor"] > 1 else ""))
    dt = 0.01
    ts = np.arange(t_start, t_end, dt)
    seg_t = [g[0] for g in segs]
    rate = np.array([segs[i][1] for i in np.searchsorted(seg_t, ts, side="right") - 1])
    busy = np.zeros(len(ts), bool)

    def mark_busy(a, b):
        busy[max(0, int((a - t_start) / dt)):max(0, int((b - t_start) / dt) + 1)] = True

    for m in log["mouse"]:
        mark_busy(m[0] - 0.5, m[0] + 0.6)
    for c in log["cams"]:
        mark_busy(c["t"] - 0.3, c["t"] + c["dur"] + 0.5)
    for c in log["caps"]:
        mark_busy(c["t"] - 0.3, c["t"] + 3.2)  # time to read
    for c in log["clicks"]:
        mark_busy(c[0], c[0] + 1.0)
    rate = np.where(~busy & (rate == BASE), BASE * IDLE, rate)
    k = int(0.3 / dt)  # soften speed changes: 0.3 s moving average of 1/rate
    inv = np.convolve(np.pad(1 / rate, (k, k), mode="edge"), np.ones(2 * k + 1) / (2 * k + 1), mode="same")[k:-k]
    vt = np.concatenate([[0.0], np.cumsum(inv * dt)])[:-1]
    VID_LEN = float(vt[-1])


def video_time(t: float) -> float:
    """Main-part video time (s, without the title card) of recording time *t*."""
    return float(np.interp(t, ts, vt))


def real_time(v):
    t = float(np.interp(v, vt, ts))
    i = int(np.searchsorted(seg_t, t, side="right") - 1)
    return t, segs[i][1], segs[i][2]


# ---------------- drawing helpers ---------------------------------------------------------
def gradient(w, h, c0=(15, 23, 42), c1=(30, 64, 120)):
    g = Image.new("RGB", (1, 256))
    for y in range(256):
        u = y / 255
        g.putpixel((0, y), tuple(int(c0[k] + (c1[k] - c0[k]) * u) for k in range(3)))
    return g.resize((w, h), Image.BICUBIC).rotate(0)


BG = Image.new("RGB", (OW, OH))
_g = gradient(OW * 2, OH * 2).rotate(-25, resample=Image.BICUBIC).crop((OW // 2, OH // 2, OW // 2 + OW, OH // 2 + OH))
BG.paste(_g)


@lru_cache(maxsize=8)
def load_frame(name):
    im = Image.open(FRAMES_DIR / name).convert("RGB")
    return im


def frame_at(t):
    i = max(0, bisect.bisect_right(ftimes, t) - 1)
    return frames[i][1]


def draw_window(cam, shot):
    """Render the page (plus fake window chrome) as seen by the camera."""
    cx, cy, cw, ch = cam
    s = OW / cw
    scale_px = shot.width / W
    inside = cx >= 0 and cy >= 0 and cx + cw <= W + 0.5 and cy + ch <= H + 0.5
    if inside:
        box = (max(0, cx * scale_px), max(0, cy * scale_px),
               min(shot.width, (cx + cw) * scale_px), min(shot.height, (cy + ch) * scale_px))
        return shot.resize((OW, OH), Image.LANCZOS, box=box)
    out = BG.copy()
    # window rectangle in output px (chrome + page)
    wx0, wy0 = (-cx) * s, (-BAR - cy) * s
    wx1, wy1 = (W - cx) * s, (H - cy) * s
    r = int(12 * s)
    # shadow
    sh = Image.new("L", (OW, OH), 0)
    ImageDraw.Draw(sh).rounded_rectangle((wx0, wy0 + 14 * s, wx1, wy1 + 14 * s), r, fill=150)
    sh = sh.filter(ImageFilter.GaussianBlur(28 * s))
    out.paste((0, 0, 0), (0, 0), sh)
    # chrome + page, composited with a rounded mask
    win = Image.new("RGB", (OW, OH), (226, 229, 234))
    d = ImageDraw.Draw(win)
    for k, col in enumerate([(255, 95, 87), (254, 188, 46), (40, 200, 64)]):
        ccx, ccy = wx0 + (20 + 20 * k) * s, wy0 + BAR / 2 * s
        d.ellipse((ccx - 6 * s, ccy - 6 * s, ccx + 6 * s, ccy + 6 * s), fill=col)
    ux0, ux1 = wx0 + 110 * s, wx0 + 560 * s
    d.rounded_rectangle((ux0, wy0 + 6 * s, ux1, wy0 + (BAR - 6) * s), int(10 * s), fill=(255, 255, 255))
    d.text((ux0 + 14 * s, wy0 + BAR / 2 * s), "127.0.0.1:8050  ·  Nano_ext", fill=(90, 96, 110),
           font=font(REG, max(8, int(14 * s))), anchor="lm")
    # page pixels
    px0, py0 = max(cx, 0), max(cy, 0)
    px1, py1 = min(cx + cw, W), min(cy + ch, H)
    if px1 > px0 and py1 > py0:
        crop = shot.resize((max(1, round((px1 - px0) * s)), max(1, round((py1 - py0) * s))), Image.LANCZOS,
                           box=(px0 * scale_px, py0 * scale_px,
                                min(shot.width, px1 * scale_px), min(shot.height, py1 * scale_px)))
        win.paste(crop, (round((px0 - cx) * s), round((py0 - cy) * s)))
    mask = Image.new("L", (OW, OH), 0)
    ImageDraw.Draw(mask).rounded_rectangle((wx0, wy0, wx1, wy1), r, fill=255)
    out.paste(win, (0, 0), mask)
    return out


def pill(img, xy, lines, anchor="lb", alpha=1.0):
    """Caption box: lines = [(text, font, colour)]."""
    if alpha <= 0:
        return
    pad_x, pad_y, gap = 34, 22, 10
    sizes = [ImageDraw.Draw(img).textbbox((0, 0), t, font=f) for t, f, _ in lines]
    tw = max(b[2] - b[0] for b in sizes)
    th = sum(b[3] - b[1] for b in sizes) + gap * (len(lines) - 1)
    bw, bh = tw + 2 * pad_x, th + 2 * pad_y
    x, y = xy
    if anchor[0] == "r":
        x -= bw
    if anchor[1] == "b":
        y -= bh
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((x + 4, y + 8, x + bw + 4, y + bh + 8), 18, fill=(0, 0, 0, int(70 * alpha)))
    layer = layer.filter(ImageFilter.GaussianBlur(8))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((x, y, x + bw, y + bh), 18, fill=(15, 23, 42, int(228 * alpha)))
    d.rounded_rectangle((x, y, x + 7, y + bh), 3, fill=(59, 130, 246, int(255 * alpha)))
    cy = y + pad_y
    for (t, f, col), b in zip(lines, sizes):
        d.text((x + pad_x, cy - b[1]), t, font=f, fill=col + (int(255 * alpha),))
        cy += b[3] - b[1] + gap
    img.paste(layer, (0, 0), layer)


ARROW = [(3, 2), (3, 18), (7.5, 13.8), (10.5, 20.5), (13.2, 19.3), (10.3, 12.8), (16.5, 12.8)]


def draw_cursor(img, t, cam):
    cx, cy, cw, ch = cam
    s = OW / cw
    k = min(max(s * 1.2, 1.9), 3.2)  # cursor scale (px per SVG unit), bounded
    i = bisect.bisect_right(mtimes, t) - 1
    if i < 0:
        return
    mx, my = mouse[i][1], mouse[i][2]
    ox, oy = (mx - cx) * s, (my - cy) * s
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for tc, x, y in clicks:
        age = t - tc
        if 0 <= age < 0.55:
            u = age / 0.55
            rad = (10 + 34 * u) * k / 2.2
            px, py = (x - cx) * s, (y - cy) * s
            d.ellipse((px - rad, py - rad, px + rad, py + rad), fill=(37, 99, 235, int(110 * (1 - u))))
    pts = [(ox + (a - 3) * k, oy + (b - 2) * k) for a, b in ARROW]
    sh = [(x + 2, y + 3) for x, y in pts]
    d.polygon(sh, fill=(0, 0, 0, 70))
    d.polygon(pts, fill=(17, 17, 17, 255))
    d.line(pts + [pts[0]], fill=(255, 255, 255, 255), width=max(2, int(k * 0.8)), joint="curve")
    img.paste(layer, (0, 0), layer)


def caption_at(t):
    """(text, sub, alpha) — alpha fades over 0.35 s of real-ish time."""
    idx = bisect.bisect_right([c[0] for c in caps], t) - 1
    if idx < 0:
        return None
    t0, text, sub = caps[idx]
    if not text:
        return None
    a = min(1.0, (t - t0) / 0.35)
    if idx + 1 < len(caps):
        a = min(a, max(0.0, (caps[idx + 1][0] - t) / 0.25))
    return text, sub, a


def card(title, sub, lines=()):
    img = BG.copy()
    d = ImageDraw.Draw(img)
    d.text((OW / 2, OH / 2 - 110), title, font=font(BOLD, 120), fill=(255, 255, 255), anchor="mm")
    d.text((OW / 2, OH / 2 + 5), sub, font=font(REG, 44), fill=(191, 219, 254), anchor="mm")
    y = OH / 2 + 120
    for text, f, col in lines:
        d.text((OW / 2, y), text, font=f, fill=col, anchor="mm")
        y += 72
    return img


def title_card():
    return card("Nano_ext", "Nanopore event analysis — right in your browser",
                [("Bayesian nonparametric event detection · 10-min recordings on a laptop",
                  font(REG, 30), (148, 163, 184))])


def end_card():
    return card("Nano_ext", "Open source · MIT", [
        ('pip install "nano-ext[gui]"', font(MONO, 46), (255, 255, 255)),
        ("nano-ext gui", font(MONO, 46), (134, 239, 172)),
        ("github.com/gorgeouspig/Nano_ext", font(REG, 34), (148, 163, 184))])


def screen(v):
    t, f, badge = real_time(v)
    cam = camera(t)
    img = draw_window(cam, load_frame(frame_at(t)))
    draw_cursor(img, t, cam)
    c = caption_at(t)
    if c:
        text, sub, a = c
        lines = [(text, font(BOLD, 46), (255, 255, 255))]
        if sub:
            lines.append((sub, font(REG, 30), (191, 219, 254)))
        pill(img, (70, OH - 60), lines, "lb", a)
    if badge:
        pill(img, (OW - 60, 60), [(badge, font(BOLD, 40), (253, 224, 71))], "rt", 1.0)
    return img



def render(out: Path) -> None:
    enc = subprocess.Popen([ffmpeg_exe(), "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{OW}x{OH}",
                            "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "slow", "-crf", "20",
                            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
                           stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    title, end = title_card(), end_card()

    def emit(img):
        enc.stdin.write(img.tobytes())

    for _ in range(int(TITLE_LEN * FPS)):
        emit(title)
    n_x, n_main = int(XFADE * FPS), int(VID_LEN * FPS)
    for i in range(n_main):
        img = screen(i / FPS)
        if i < n_x:
            img = Image.blend(title, img, i / n_x)
        if i >= n_main - n_x:
            img = Image.blend(img, end, (i - (n_main - n_x)) / n_x)
        emit(img)
        if i % 300 == 0:
            print(f"{i}/{n_main} frames", flush=True)
    for _ in range(int(END_LEN * FPS)):
        emit(end)
    enc.stdin.close()
    enc.wait()
    print("wrote", out, f"({TITLE_LEN + VID_LEN + END_LEN:.0f} s)")


def gif_window() -> tuple[float, float]:
    """Start and length (s, in the MP4) of the README GIF scene."""
    first, last = GIF_SCENE
    t0 = next(t for t, cid in ((c["t"], c["id"]) for c in log["caps"]) if cid == first)
    t_last = next(c["t"] for c in log["caps"] if c["id"] == last)
    t1 = next((c["t"] for c in log["cams"] if c["t"] > t_last + 0.5), t_end)  # camera pulls back
    a, b = video_time(t0) + TITLE_LEN, video_time(t1) + TITLE_LEN
    return a - 0.2, b - a


def make_gif(mp4: Path, gif: Path, width: int = 800, fps: int = 12) -> None:
    start, length = gif_window()
    vf = (f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];"
          "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle")
    subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{length:.2f}",
                    "-i", str(mp4), "-vf", vf, str(gif)], check=True)
    print("wrote", gif, f"({length:.1f} s)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work", default=str(HERE / "_work"), help="Working folder (default: %(default)s)")
    ap.add_argument("--out", default=None, help="MP4 path (default: <work>/nano_ext_gui_demo.mp4)")
    ap.add_argument("--gif", default=None, help="Also write the README GIF here")
    ap.add_argument("--preview", default=None, help="Comma-separated video times: write PNG stills only")
    args = ap.parse_args()
    work = Path(args.work)
    load_log(work)
    if args.preview:
        print(f"main part: {VID_LEN:.1f} s (+ {TITLE_LEN} s title, {END_LEN} s end card)")
        for v in map(float, args.preview.split(",")):
            screen(v).resize((960, 540), Image.LANCZOS).save(work / f"preview_{v:06.1f}.png")
        return
    out = Path(args.out) if args.out else work / "nano_ext_gui_demo.mp4"
    render(out)
    if args.gif:
        make_gif(out, Path(args.gif))


if __name__ == "__main__":
    main()
