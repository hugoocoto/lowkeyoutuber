#!/usr/bin/env python3
"""
capture.py - Record an app by driving it, following the "actions" of each script line, and log
every click and keystroke with its time and place. The log gives each line its start time (so
`tts.py --place` puts the voice there) and proposes zooms on what was clicked or typed into.

KINDS
    web       a page in Chrome (Playwright). A visible cursor glides to each element and
              shows each click. Needs: pip install playwright (uses the installed Chrome/Chromium).
    desktop   any X11 program on a virtual screen (Xvfb + xdotool + ffmpeg), so your own desktop
              isn't touched: a terminal app, an editor, a GUI. Needs: Xvfb, xdotool.
    terminal  desktop with a big-font xterm; "run" actions type a command and press Enter.

ACTIONS (script.json, per line: "actions": [...], done in order)
    web       {"goto": "/path or URL"}  {"click": "css or text=Label"}  {"type": ["css", "text"]}
              {"press": "Enter"}  {"hover": "css"}  {"select": ["css", "value"]}
              {"scroll": 600}  {"wait": 1.5}
    desktop   {"type": "text"}  {"key": "ctrl+s"}  {"click": [0.5, 0.3]} (fraction of the screen)
              {"run": "ls -l"}  {"wait": 1.5}
    When the line already has narration (tts.py ran first), capture waits at the end of each line
    until its narration has had time to play, so the voice never runs into the next action.

USAGE
    capture.py -P project/ --kind web --url http://localhost:3000
    capture.py -P project/ --kind terminal --cwd ~/code/demo
    capture.py -P project/ --kind desktop --app "gedit notes.txt" --size 1920x1080
    capture.py -P project/ --apply          # after approval: proposals/zooms.json -> timeline

OUTPUT
    media/capture.mp4             the recording (media id "cap"), NVENC when available
    media/capture.events.json     every action: time, kind, element rectangle, line
    script.json                   each line gets "at" = when its actions start
    timeline.video                one item: the whole capture, its own audio off
    proposals/zooms.json          a zoom per click/typing, for the person to approve

    Use demo data: whatever is on screen ends up in the video. capture.py refuses to type
    text that looks like an API key or token.
"""

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import project as proj
from tts import probe_duration
from yt_common import die, log, need, video_encoder

ZOOM_MIN = 0.4        # smallest zoom area, fraction of the frame width
ZOOM_LEAD = 0.35      # zoom starts this long before the action
SECRET = re.compile(r"(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|xox[bp]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|"
                    r"AIza[0-9A-Za-z_-]{30,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,})")

CURSOR_JS = r"""
(() => {
  const install = () => {
    if (document.getElementById('__lky_cursor')) return;
    const c = document.createElement('div');
    c.id = '__lky_cursor';
    c.style.cssText = 'position:fixed;left:-100px;top:-100px;width:26px;height:26px;z-index:2147483647;' +
      'pointer-events:none;transform:translate(-4px,-2px);';
    c.innerHTML = '<svg viewBox="0 0 24 24" width="26" height="26"><path d="M3 2l17 9-7 2-3 7z" ' +
      'fill="#fff" stroke="#000" stroke-width="1.5" stroke-linejoin="round"/></svg>';
    document.documentElement.appendChild(c);
    addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
    addEventListener('mousedown', e => {
      const r = document.createElement('div');
      r.style.cssText = `position:fixed;left:${e.clientX - 18}px;top:${e.clientY - 18}px;width:36px;height:36px;` +
        'border-radius:50%;border:3px solid #ffd166;z-index:2147483646;pointer-events:none;' +
        'transition:transform .45s ease-out,opacity .45s ease-out;';
      document.documentElement.appendChild(r);
      requestAnimationFrame(() => { r.style.transform = 'scale(2.2)'; r.style.opacity = '0'; });
      setTimeout(() => r.remove(), 600);
    }, true);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install); else install();
})();
"""


class CaptureError(RuntimeError):
    pass


def check_text(text):
    if SECRET.search(text or ""):
        raise CaptureError("refusing to type something that looks like an API key or token: use demo data")


class Clock:
    def __init__(self):
        self.t0 = time.monotonic()

    def now(self):
        return round(time.monotonic() - self.t0, 3)


def narration_len(root, lid):
    wav = os.path.join(root, "narration", lid + ".wav")
    return probe_duration(wav) if os.path.isfile(wav) else 0.0


def web_base(url):
    """The app's address with a scheme (http:// when missing), no trailing slash; https: and file: stay."""
    url = url.strip()
    if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", url) and not url.startswith("file:"):
        url = "http://" + url
    return url.rstrip("/")


def chrome_path():
    from yt_common import CONFIG
    for c in (CONFIG.get("CHROME"), "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if c and shutil.which(os.path.expanduser(c)):
            return shutil.which(os.path.expanduser(c))
    return None


# ---------------------------------------------------------------------------
# Web (Playwright)
# ---------------------------------------------------------------------------
def capture_web(lines, root, url, W, H, tmp, headed):
    try:
        from playwright.sync_api import Error as PWError, sync_playwright
    except ImportError:
        raise CaptureError("Playwright is not installed: pip install playwright")
    events, spans = [], {}
    base = web_base(url)
    with sync_playwright() as p:
        exe = chrome_path()
        browser = p.chromium.launch(headless=not headed, executable_path=exe) if exe else \
            p.chromium.launch(headless=not headed)
        ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=1,
                                  record_video_dir=tmp, record_video_size={"width": W, "height": H})
        ctx.add_init_script(CURSOR_JS)
        page = ctx.new_page()
        clock = Clock()   # the video starts when the page is created
        mouse = [W / 2, H / 2]

        def rect_of(loc):
            b = loc.bounding_box()
            return [round(b["x"] / W, 4), round(b["y"] / H, 4), round(b["width"] / W, 4), round(b["height"] / H, 4)] \
                if b else None

        def glide(loc):
            loc.scroll_into_view_if_needed(timeout=10000)
            b = loc.bounding_box()
            if not b:
                raise CaptureError("element has no box on screen")
            x, y = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
            dist = ((x - mouse[0]) ** 2 + (y - mouse[1]) ** 2) ** 0.5
            page.mouse.move(x, y, steps=max(8, min(40, int(dist / 25))))
            mouse[:] = [x, y]
            page.wait_for_timeout(120)

        def target(sel):
            return page.locator(sel).first

        page.goto(base, wait_until="load")
        page.wait_for_timeout(600)
        for line in lines:
            start = clock.now()
            for a in line.get("actions", []):
                (kind, arg), = a.items() if len(a) == 1 else [(next(iter(a)), a[next(iter(a))])]
                ev = {"t": clock.now(), "type": kind, "line": line["id"]}
                try:
                    if kind == "goto":
                        dest = arg if re.match(r"^\w+:", arg) else base + "/" + arg.lstrip("/")
                        page.goto(dest, wait_until="load")
                        page.wait_for_timeout(400)
                    elif kind in ("click", "hover"):
                        loc = target(arg)
                        glide(loc)
                        ev["t"], ev["rect"] = clock.now(), rect_of(loc)
                        loc.click() if kind == "click" else loc.hover()
                        page.wait_for_timeout(350)
                    elif kind == "type":
                        sel, text = arg
                        check_text(text)
                        loc = target(sel)
                        glide(loc)
                        ev["t"], ev["rect"], ev["text"] = clock.now(), rect_of(loc), text
                        loc.click()
                        page.keyboard.type(text, delay=70)
                        page.wait_for_timeout(250)
                    elif kind == "select":
                        sel, value = arg
                        loc = target(sel)
                        glide(loc)
                        ev["t"], ev["rect"] = clock.now(), rect_of(loc)
                        loc.select_option(value)
                        page.wait_for_timeout(350)
                    elif kind == "press":
                        page.keyboard.press(arg)
                        page.wait_for_timeout(300)
                    elif kind == "scroll":
                        steps = 12
                        for _ in range(steps):
                            page.mouse.wheel(0, float(arg) / steps)
                            page.wait_for_timeout(30)
                        page.wait_for_timeout(300)
                    elif kind == "wait":
                        page.wait_for_timeout(float(arg) * 1000)
                    else:
                        raise CaptureError(f"unknown web action {kind!r}")
                except PWError as e:
                    raise CaptureError(f"line {line['id']}, action {a}: {str(e).splitlines()[0]}")
                events.append(ev)
            hold = narration_len(root, line["id"]) + 0.35 - (clock.now() - start)
            if hold > 0:
                page.wait_for_timeout(hold * 1000)
            spans[line["id"]] = [start, clock.now()]
        page.wait_for_timeout(800)
        video = page.video
        ctx.close()
        browser.close()
        src = video.path()
    return src, events, spans


# ---------------------------------------------------------------------------
# Desktop / terminal (Xvfb + xdotool + ffmpeg x11grab)
# ---------------------------------------------------------------------------
def free_display():
    for n in range(91, 140):
        if not os.path.exists(f"/tmp/.X11-unix/X{n}") and not os.path.exists(f"/tmp/.X{n}-lock"):
            return f":{n}"
    raise CaptureError("no free X display")


def capture_desktop(lines, root, app, W, H, fps, tmp, cwd, terminal):
    need("Xvfb", "xdotool", "ffmpeg")
    disp = free_display()
    env = dict(os.environ, DISPLAY=disp)
    procs = []
    rec = None   # the recorder is stopped first on any exit, before the screen goes away
    try:
        procs.append(subprocess.Popen(["Xvfb", disp, "-screen", "0", f"{W}x{H}x24", "-nolisten", "tcp"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        for _ in range(50):
            if os.path.exists(f"/tmp/.X11-unix/X{disp[1:]}"):
                break
            time.sleep(0.1)
        if terminal:
            need("xterm")
            fs = max(16, round(H / 1080 * 30))   # readable on a phone
            app = (f"xterm -fa 'DejaVu Sans Mono' -fs {fs} -geometry 200x60+0+0 -bg '#0f1117' -fg '#e8e8e8' "
                   f"-xrm 'XTerm*cursorColor: #ffd166' -e bash --norc")
        procs.append(subprocess.Popen(shlex.split(app), env=dict(env, PS1="$ "), cwd=cwd,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(1.5)
        if terminal:   # full screen
            subprocess.run(["xdotool", "search", "--sync", "--class", "xterm", "windowsize", str(W), str(H),
                            "windowmove", "0", "0"], env=env, capture_output=True, timeout=10)
        raw = os.path.join(tmp, "desktop.mkv")
        rec = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "x11grab", "-draw_mouse", "1",
                                "-video_size", f"{W}x{H}", "-framerate", str(fps), "-i", disp,
                                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", raw],
                               env=env, stdin=subprocess.DEVNULL)
        time.sleep(0.4)
        clock = Clock()
        events, spans = [], {}

        def xdo(*a):
            r = subprocess.run(["xdotool", *a], env=env, capture_output=True, text=True, timeout=60)
            if r.returncode:
                raise CaptureError(f"xdotool {' '.join(a)}: {r.stderr.strip()}")

        for line in lines:
            start = clock.now()
            for a in line.get("actions", []):
                kind, arg = next(iter(a.items()))
                ev = {"t": clock.now(), "type": kind, "line": line["id"]}
                if kind in ("type", "run"):
                    check_text(arg)
                    xdo("type", "--delay", "70", "--", arg)   # "--": text like "--help" is typed, not parsed
                    if kind == "run":
                        xdo("key", "Return")
                        time.sleep(0.8)
                    ev["text"] = arg
                elif kind == "key":
                    xdo("key", arg)
                    time.sleep(0.3)
                elif kind == "click":
                    x, y = arg
                    px, py = (int(x * W), int(y * H)) if x <= 1 and y <= 1 else (int(x), int(y))
                    xdo("mousemove", str(px), str(py))
                    time.sleep(0.25)
                    ev["t"] = clock.now()
                    xdo("click", "1")
                    ev["rect"] = [round(px / W - 0.05, 4), round(py / H - 0.04, 4), 0.1, 0.08]
                    time.sleep(0.35)
                elif kind == "wait":
                    time.sleep(float(arg))
                else:
                    raise CaptureError(f"unknown desktop action {kind!r}")
                events.append(ev)
            hold = narration_len(root, line["id"]) + 0.35 - (clock.now() - start)
            if hold > 0:
                time.sleep(hold)
            spans[line["id"]] = [start, clock.now()]
        time.sleep(0.8)
        rec.send_signal(signal.SIGINT)
        rec.wait(timeout=30)
        return raw, events, spans
    finally:
        if rec is not None and rec.poll() is None:
            rec.send_signal(signal.SIGINT)
            try:
                rec.wait(timeout=30)
            except subprocess.TimeoutExpired:
                rec.kill()
                rec.wait()
        for p in reversed(procs):
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


# ---------------------------------------------------------------------------
def encode_capture(src, out, fps):
    """Encode into a temporary sibling and replace `out` only once it probes as a video, so a
    failed recapture (no disk space, a bad encode) leaves the previous capture alone."""
    part = out + ".part.mp4"
    try:
        r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", src, "-vf",
                            f"fps={fps},format=yuv420p", *video_encoder("auto", 18), "-an",
                            "-movflags", "+faststart", part], capture_output=True, text=True)
        if r.returncode:
            raise CaptureError("ffmpeg failed: " + r.stderr.strip()[-500:])
        total = probe_duration(part)
        if total <= 0:
            raise CaptureError("ffmpeg wrote a file that can't be read: the previous capture was kept")
        os.replace(part, out)
        return total
    finally:
        if os.path.exists(part):
            os.remove(part)


def propose_zooms(events, total):
    """A zoom on each click/typing target, held until the next action (1.2-4 s). Actions close
    together in time become one zoom covering all their targets, so the picture doesn't bounce."""
    zooms = []
    for e in (e for e in events if e.get("rect")):
        x, y, w, h = e["rect"]
        at = max(0.0, e["t"] - ZOOM_LEAD)
        nxt = next((n["t"] for n in events if n["t"] > e["t"] + 0.5), total)
        end = min(total, at + max(1.2, min(4.0, nxt - at)))
        box = [x, y, x + w, y + h]
        if zooms and at < zooms[-1]["end"] + 0.3:
            z = zooms[-1]
            merged = [min(z["box"][0], box[0]), min(z["box"][1], box[1]), max(z["box"][2], box[2]),
                      max(z["box"][3], box[3])]
            if max(merged[2] - merged[0], merged[3] - merged[1]) * 1.35 <= 0.9:
                z["box"], z["end"] = merged, max(z["end"], end)
                z["why"].append(f"{e['type']} ({e['line']})")
                continue
            z["end"] = min(z["end"], at)   # the next zoom takes over
        zooms.append({"at": at, "end": end, "box": box, "why": [f"{e['type']} ({e['line']})"]})
    out = []
    for z in zooms:
        bx0, by0, bx1, by1 = z["box"]
        size = min(0.9, max(ZOOM_MIN, (bx1 - bx0) * 1.35, (by1 - by0) * 3))
        cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
        rx, ry = min(max(cx - size / 2, 0), 1 - size), min(max(cy - size / 2, 0), 1 - size)
        dur = z["end"] - z["at"]
        if dur >= 1.0:
            out.append({"at": round(z["at"], 2), "dur": round(dur, 2),
                        "rect": [round(rx, 4), round(ry, 4), round(size, 4), round(size, 4)],
                        "why": ", ".join(z["why"])})
    return out


def main():
    p = argparse.ArgumentParser(description="Record an app by driving it from script.json actions.")
    p.add_argument("-P", "--project", required=True)
    p.add_argument("--kind", choices=["web", "desktop", "terminal"])
    p.add_argument("--url", help="web: the app's address")
    p.add_argument("--app", help="desktop: the command that starts the program")
    p.add_argument("--cwd", help="desktop/terminal: folder to start in")
    p.add_argument("--size", default="1920x1080", help="screen size (default 1920x1080)")
    p.add_argument("--headed", action="store_true", help="web: show the browser (for debugging)")
    p.add_argument("--apply", action="store_true", help="put the approved proposals/zooms.json on the timeline")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    try:
        pj = proj.load(args.project)
        script = proj.load_script(args.project)
    except proj.ProjectError as e:
        die(str(e))
    root = proj.root(args.project)

    if args.apply:
        zp = os.path.join(root, "proposals", "zooms.json")
        if not os.path.isfile(zp):
            die("no proposals/zooms.json: run a capture first")
        with open(zp, encoding="utf-8") as f:
            zooms = json.load(f)
        items = [it for it in pj["timeline"].get("video", []) if it.get("src") == "cap"]
        if not items:
            die("the timeline has no capture item (src 'cap')")
        items[0]["zooms"] = [{k: z[k] for k in ("at", "dur", "rect")} for z in zooms]
        proj.set_state(pj, "zooms", "approved")
        proj.save(args.project, pj)
        log(f"Applied {len(zooms)} zooms.")
        return

    if not args.kind:
        die("--kind web|desktop|terminal is required")
    m = re.fullmatch(r"(\d+)x(\d+)", args.size)
    if not m:
        die("--size takes WIDTHxHEIGHT, e.g. 1920x1080")
    W, H = int(m.group(1)) // 2 * 2, int(m.group(2)) // 2 * 2
    lines = script["lines"]
    if not any(line.get("actions") for line in lines):
        die("no line in script.json has 'actions' (see capture.py -h)")
    for line in lines:
        for a in line.get("actions", []):
            if not isinstance(a, dict) or len(a) != 1:
                die(f"line {line['id']}: each action is one {{\"kind\": value}} object, got {a!r}")
    cwd = os.path.expanduser(args.cwd) if args.cwd else root
    out = os.path.join(root, "media", "capture.mp4")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            if args.kind == "web":
                if not args.url:
                    die("--url is required for web captures")
                src, events, spans = capture_web(lines, root, args.url, W, H, tmp, args.headed)
            else:
                if args.kind == "desktop" and not args.app:
                    die("--app is required for desktop captures")
                src, events, spans = capture_desktop(lines, root, args.app, W, H, pj["fps"], tmp, cwd,
                                                     args.kind == "terminal")
            total = encode_capture(src, out, pj['fps'])
    except CaptureError as e:
        die(str(e))
    with open(os.path.join(root, "media", "capture.events.json"), "w", encoding="utf-8") as f:
        json.dump({"kind": args.kind, "size": [W, H], "lines": spans, "events": events}, f, ensure_ascii=False, indent=2)
    pj["media"]["cap"] = {"file": "media/capture.mp4", "kind": "video", "events": "media/capture.events.json"}
    pj["timeline"]["video"] = [{"src": "cap", "in": 0, "out": round(total, 3), "audio": False}]
    for line in lines:
        if line["id"] in spans:
            line["at"] = round(spans[line["id"]][0] + 0.2, 3)
    with open(os.path.join(root, "script.json"), "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)
    zooms = propose_zooms(events, total)
    with open(os.path.join(root, "proposals", "zooms.json"), "w", encoding="utf-8") as f:
        json.dump(zooms, f, ensure_ascii=False, indent=2)
    proj.set_state(pj, "capture", "done")
    proj.set_state(pj, "zooms", "proposed")
    proj.save(args.project, pj)
    log(f"Captured {total:.1f}s ({len(events)} actions) -> media/capture.mp4; {len(zooms)} zooms proposed "
        "in proposals/zooms.json (approve, then capture.py --apply).")
    if args.json:
        json.dump({"file": "media/capture.mp4", "duration": round(total, 3), "events": len(events),
                   "lines": spans, "zooms_proposed": len(zooms)}, sys.stdout, ensure_ascii=False)
        print()


if __name__ == "__main__":
    main()
