#!/usr/bin/env python3
"""
thumbnail.py - Make YouTube thumbnail designs (1280x720) from the video:
a frame of the screen as background, your facecam cut out big on the right,
and 2-4 words of big text on the left.

TEXT
    --text "..." (repeat for several designs), else the lines of
    thumbnail_texts.txt written by meta.py in the output folder.

OUTPUT (in --out, default: next to the video)
    thumbs/design_1.jpg ...   one per text, each on a different frame
    thumbnail.jpg             a copy of design 1, only if it doesn't exist yet
                              (upload.py uses this file: replace it with your pick)

USAGE
    thumbnail.py video.mp4 -o pub/
    thumbnail.py video.mp4 -o pub/ --text "ADIÓS APT" --at 312
    thumbnail.py video.mp4 --cam 934:384:346:303      # facecam box (default: config CAM)

Treat them as a starting point: a thumbnail you design yourself will usually
beat a template.
"""

import argparse
import itertools
import os
import shutil
import subprocess
import tempfile

from yt_common import CONFIG, die, log, need

TW, TH = 1280, 720
FONTS = [("Anton", 0.5), ("Impact", 0.58), ("TeX Gyre HerosCn:bold", 0.56),
         ("DejaVu Sans:bold", 0.76)]   # (fontconfig pattern, width of an uppercase char / size)


def find_font():
    for pattern, ratio in FONTS:
        r = subprocess.run(["fc-match", "-f", "%{family}|%{file}", pattern], capture_output=True, text=True)
        family, _, path = r.stdout.partition("|")
        if r.returncode == 0 and family.lower().startswith(pattern.split(":")[0].lower()) and os.path.isfile(path):
            return path, ratio
    die("no bold font found. Install one: sudo pacman -S ttf-dejavu")


def duration(video):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        die("ffprobe could not read the video")


def split_lines(text):
    """1-3 lines with lengths as even as possible (big text needs short lines)."""
    words = text.upper().split()
    n = 1 if len(" ".join(words)) <= 11 else 2 if len(" ".join(words)) <= 22 else 3
    n = min(n, len(words))
    best = None
    for cuts in itertools.combinations(range(1, len(words)), n - 1):
        bounds = (0, *cuts, len(words))
        lines = [" ".join(words[a:b]) for a, b in zip(bounds, bounds[1:])]
        if best is None or max(map(len, lines)) < max(map(len, best)):
            best = lines
    return best or [""]


def design(args, video, t, text, out, font, ratio):
    cam = None
    if args.cam:
        try:
            cam = [int(v) for v in args.cam.split(":")]
            assert len(cam) == 4
        except (ValueError, AssertionError):
            die("--cam must look like x:y:w:h (source pixels)")

    graph = ["[0:v]split=2[s][c]"]
    if cam:
        x, y, w, h = cam
        # blur the small facecam inside the screen: it appears big on the right
        graph.append(f"[s]split=2[s1][s2];[s2]crop={w}:{h}:{x}:{y},boxblur=20:2[hb];[s1][hb]overlay={x}:{y}[s3]")
        src = "s3"
    else:
        src = "s"
    face_w = fh = cw = cx = 0
    face_h = TH - 2 * 30 - 16
    if cam:
        x, y, w, h = cam
        fh = int(h * args.face_crop)            # keep the top part: the face, not the desk
        cw = min(w, int(fh * 0.8)) // 2 * 2     # portrait crop around the middle of the box
        cx = x + min(max(int(w * args.face_x) - cw // 2, 0), w - cw)
        face_w = round(cw * face_h / fh / 2) * 2 + 16
    text_w = TW - face_w - (40 if cam else 0) - 60
    lines = split_lines(text)
    longest = max(len(line) for line in lines)
    size = int(min(190 if len(lines) == 1 else 160, text_w / (longest * ratio)))
    graph.append(f"[{src}]scale={TW}:{TH}:force_original_aspect_ratio=increase,crop={TW}:{TH},"
                 f"boxblur=3:1,eq=brightness=-0.12:saturation=1.2,"
                 f"drawbox=x=0:y=0:w={text_w + 60}:h={TH}:color=black@0.45:t=fill[bg]")
    last = "bg"
    if cam:
        x, y, w, h = cam
        graph.append(f"[c]crop={cw}:{fh}:{cx}:{y},scale=-2:{face_h},pad=iw+16:ih+16:8:8:white[face]")
        graph.append("[bg][face]overlay=W-w-30:(H-h)/2[t0]")
        last = "t0"
    else:
        graph.append("[c]nullsink")

    with tempfile.TemporaryDirectory() as tmp:
        total = len(lines) * size * 1.1
        draws = []
        for n, line in enumerate(lines):
            with open(os.path.join(tmp, f"l{n}.txt"), "w", encoding="utf-8") as f:
                f.write(line)
            color = "0xFFD700" if n == 1 else "white"   # middle/second line highlighted
            ypos = int((TH - total) / 2 + n * size * 1.1)
            draws.append(f"drawtext=fontfile='{font}':textfile=l{n}.txt:expansion=none:fontsize={size}:"
                         f"fontcolor={color}:borderw={max(size // 14, 4)}:bordercolor=black:"
                         f"shadowx=5:shadowy=5:shadowcolor=black@0.6:x=30+({text_w}-text_w)/2:y={ypos}")
        graph.append(f"[{last}]" + ",".join(draws) + "[out]")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-ss", f"{t:.2f}",
               "-i", os.path.abspath(video), "-filter_complex", ";".join(graph), "-map", "[out]",
               "-frames:v", "1", "-q:v", "3", os.path.abspath(out)]
        r = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True)
        if r.returncode:
            die("ffmpeg failed: " + r.stderr.strip()[-500:])


def main():
    p = argparse.ArgumentParser(description="YouTube thumbnail designs: frame + facecam + big text.")
    p.add_argument("video")
    p.add_argument("-o", "--out", help="output folder (default: next to the video)")
    p.add_argument("--text", action="append", help="thumbnail text, 2-4 words (repeatable)")
    p.add_argument("--at", type=float, action="append", help="frame time in seconds (repeatable, one per text)")
    p.add_argument("--cam", default=CONFIG.get("CAM") or None,
                   help="facecam box in source pixels, x:y:w:h (default: config CAM; none = text only)")
    p.add_argument("--face-crop", type=float, default=0.8,
                   help="top fraction of the facecam box to use, to leave out the desk (default 0.8)")
    p.add_argument("--face-x", type=float, default=float(CONFIG.get("FACE_X") or 0.5),
                   help="where your face is across the facecam box, 0-1 (default: config FACE_X, else 0.5)")
    args = p.parse_args()

    if not os.path.isfile(args.video):
        die(f"File not found: {args.video}")
    need("ffmpeg", "ffprobe", "fc-match")
    outdir = args.out or os.path.dirname(os.path.abspath(args.video))
    texts = args.text
    if not texts:
        path = os.path.join(outdir, "thumbnail_texts.txt")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                texts = [line.strip() for line in f if line.strip()]
    if not texts:
        die("no text: pass --text, or run meta.py first (it writes thumbnail_texts.txt)")

    font, ratio = find_font()
    dur = duration(args.video)
    times = args.at or []
    times += [dur * (i + 1) / (len(texts) + 1) for i in range(len(times), len(texts))]
    os.makedirs(os.path.join(outdir, "thumbs"), exist_ok=True)
    made = []
    for n, (text, t) in enumerate(zip(texts, times), 1):
        out = os.path.join(outdir, "thumbs", f"design_{n}.jpg")
        design(args, args.video, min(t, max(dur - 1, 0)), text, out, font, ratio)
        made.append(out)
        log(f"-> {out}  ({text!r} at {t:.0f}s)")
    final = os.path.join(outdir, "thumbnail.jpg")
    if not os.path.exists(final):
        shutil.copy(made[0], final)
        log(f"-> {final}  (copy of design 1: replace it with your favourite)")


if __name__ == "__main__":
    main()
