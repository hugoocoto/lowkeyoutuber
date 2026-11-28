#!/usr/bin/env python3
"""
frame.py - Put phone captures in a phone frame on a coloured background, one video per format, so an app
demo looks like a phone in the shot instead of a narrow strip in a wide video. Two captures side by side
(customer and shop, sender and receiver...) play in sync: give the second one its offset.

The phones end above the captions render.py burns (they sit 7 % / 12 % / 17 % from the bottom in
16:9 / 1:1 / 9:16), so captions never cover the screen. Silent video: the captures have no sound.

USAGE
    frame.py out/phone --cap media/capture.mp4
    frame.py out/duo --cap caps/customer.mp4 --cap caps/shop.mp4@73.2 --label Cliente --label Bar \\
             --start 76 --dur 10 --bg "#B0124A,#46061E"
  --cap FILE[@OFFSET]  a capture (any size; phone-shaped looks best); OFFSET = where its t=0 falls on the
                       first capture's clock, from the captures' start times (fine-tune by eye)
  --start/--dur        the part of the first capture's clock to render (default: all of it)
  --bg INNER,OUTER     radial background colours (default dark grey)
  --formats            default 16:9,9:16,1:1
Writes OUT_16x9.mp4, OUT_9x16.mp4, OUT_1x1.mp4; put them on the timeline as one media entry with
"variants" (docs/project.md). Needs: pip install pillow.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

from yt_common import die, log, need, video_encoder

SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350)}
# screen height and top edge per (format, number of phones): the phones end above the burned captions
SCREEN_H = {("16:9", 1): 800, ("16:9", 2): 760, ("9:16", 1): 1230, ("9:16", 2): 920,
            ("1:1", 1): 730, ("1:1", 2): 670, ("4:5", 1): 1000, ("4:5", 2): 860}
TOP = {("16:9", 1): 45, ("16:9", 2): 100, ("9:16", 1): 150, ("9:16", 2): 200,
       ("1:1", 1): 45, ("1:1", 2): 110, ("4:5", 1): 60, ("4:5", 2): 130}


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height:format=duration", "-of", "json", path], capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)
        return d["streams"][0]["width"], d["streams"][0]["height"], float(d["format"]["duration"])
    except (ValueError, KeyError, IndexError):
        die(f"can't read {path}")


def hex_rgb(c):
    c = c.strip().lstrip("#")
    if len(c) != 6:
        die(f"--bg: {c!r} is not a #RRGGBB colour")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def layout(fmt, n, cw, ch):
    """Screen size and top-left corner of each phone's screen, in pixels."""
    W, _ = SIZES[fmt]
    sh = SCREEN_H[(fmt, n)]
    sw = round(sh * cw / ch / 2) * 2
    gap = round(sw * 0.28)
    x0 = (W - (n * sw + (n - 1) * gap)) // 2
    return sw, sh, [(x0 + i * (sw + gap), TOP[(fmt, n)]) for i in range(n)]


def stills(fmt, phones, sw, sh, labels, bg, font, tmp):
    """Background (gradient, shadows, labels), phone bodies overlay, and the rounded screen mask."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
    W, H = SIZES[fmt]
    grad = Image.radial_gradient("L").resize((W, H))
    img = ImageOps.colorize(grad, black=bg[0], white=bg[1]).convert("RGB")
    rs, bez = round(sw * 0.105), round(sw * 0.035)
    shadow = Image.new("L", (W, H), 0)
    for x, y in phones:
        ImageDraw.Draw(shadow).rounded_rectangle([x - bez, y - bez + 18, x + sw + bez, y + sh + bez + 18],
                                                 radius=rs + bez, fill=110)
    img = Image.composite(Image.new("RGB", (W, H), (10, 10, 12)), img, shadow.filter(ImageFilter.GaussianBlur(28)))
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(font, 44 if fmt != "9:16" else 48)
    for (x, y), text in zip(phones, labels):
        if text:
            d.text((x + sw / 2, y - 34), text, font=f, fill=(255, 255, 255), anchor="ms")
    over = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(over)
    for x, y in phones:
        body = Image.new("L", (W, H), 0)
        ImageDraw.Draw(body).rounded_rectangle([x - bez, y - bez, x + sw + bez, y + sh + bez], radius=rs + bez, fill=255)
        ImageDraw.Draw(body).rounded_rectangle([x, y, x + sw - 1, y + sh - 1], radius=rs, fill=0)
        over.paste((17, 17, 19, 255), (0, 0), body)
        od.rounded_rectangle([x - bez, y - bez, x + sw + bez, y + sh + bez], radius=rs + bez, outline=(70, 70, 74, 255), width=3)
    mask = Image.new("L", (sw, sh), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, sw - 1, sh - 1], radius=rs, fill=255)
    paths = [os.path.join(tmp, f"{n}.png") for n in ("bg", "bezel", "mask")]
    for im, p in zip((img, over, mask), paths):
        im.save(p)
    return paths


def render(out, caps, labels, start, dur, fmt, bg, font, tmp):
    cw, ch, _ = probe(caps[0][0])
    sw, sh, phones = layout(fmt, len(caps), cw, ch)
    bgp, bzp, mkp = stills(fmt, phones, sw, sh, labels, bg, font, tmp)
    args = ["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", "30", "-i", bgp]
    graph = []
    for i, (f, off) in enumerate(caps):
        s = start - off                   # this capture's own time at the start of the output
        args += ["-ss", f"{max(0.0, s):.3f}", "-t", f"{dur:.3f}", "-i", f]
        graph.append(f"[{i + 1}:v]fps=30,tpad=start_duration={max(0.0, -s):.3f}:start_mode=clone,"
                     f"tpad=stop_duration={dur:.3f}:stop_mode=clone,trim=duration={dur:.3f},setpts=PTS-STARTPTS,"
                     f"scale={sw}:{sh}:flags=lanczos,format=rgba[c{i}]")
    n = len(caps)
    args += ["-loop", "1", "-framerate", "30", "-i", mkp, "-loop", "1", "-framerate", "30", "-i", bzp]
    graph.append(f"[{n + 1}:v]format=gray,split={n}" + "".join(f"[m{i}]" for i in range(n)))
    cur = "[0:v]"
    for i, (x, y) in enumerate(phones):
        graph += [f"[c{i}][m{i}]alphamerge[r{i}]", f"{cur}[r{i}]overlay={x}:{y}[o{i}]"]
        cur = f"[o{i}]"
    graph.append(f"{cur}[{n + 2}:v]overlay=0:0,trim=duration={dur:.3f},format=yuv420p[v]")
    args += ["-filter_complex", ";".join(graph), "-map", "[v]", "-r", "30", *video_encoder("auto", 18), "-an", out]
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode:
        die(f"ffmpeg failed for {fmt}: {r.stderr.strip()[-400:]}")
    return out


def main():
    p = argparse.ArgumentParser(description="Phone captures in a phone frame, one video per format.")
    p.add_argument("out", help="output base: writes OUT_16x9.mp4, OUT_9x16.mp4...")
    p.add_argument("--cap", action="append", required=True, help="FILE[@OFFSET], once or twice")
    p.add_argument("--label", action="append", default=[], help="text above each phone (in --cap order)")
    p.add_argument("--start", type=float, default=0.0, help="start on the first capture's clock (s)")
    p.add_argument("--dur", type=float, help="length (default: to the end of the first capture)")
    p.add_argument("--bg", default="#2B2F36,#111317", help="radial background: INNER,OUTER colours")
    p.add_argument("--formats", default="16:9,9:16,1:1", help=f"comma-separated, of {', '.join(SIZES)}")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    need("ffmpeg")
    need("ffprobe")
    try:
        import PIL  # noqa: F401
    except ImportError:
        die("frame.py needs Pillow: lky pip install pillow")
    caps = []
    for c in a.cap:
        f, _, off = c.partition("@")
        if not os.path.isfile(f):
            die(f"--cap: no such file: {f}")
        try:
            caps.append((f, float(off) if off else 0.0))
        except ValueError:
            die(f"--cap {c}: the offset after @ must be seconds")
    if len(caps) > 2:
        die("at most two phones (--cap twice)")
    fmts = [x.strip() for x in a.formats.split(",") if x.strip()]
    bad = [x for x in fmts if x not in SIZES]
    if bad:
        die(f"unknown format(s) {', '.join(bad)} (known: {', '.join(SIZES)})")
    bg = [hex_rgb(x) for x in a.bg.split(",")][:2]
    if len(bg) != 2:
        die("--bg takes two colours: INNER,OUTER")
    from thumbnail import find_font
    font, _ = find_font()
    dur = a.dur or probe(caps[0][0])[2] - a.start
    if dur <= 0:
        die("nothing to render: --start is past the end of the first capture")
    labels = a.label + [""] * (len(caps) - len(a.label))
    outs = []
    with tempfile.TemporaryDirectory() as tmp:
        for fmt in fmts:
            log(f"{fmt}: framing {dur:.1f}s ({len(caps)} phone{'s' if len(caps) > 1 else ''})...")
            outs.append(render(f"{a.out}_{fmt.replace(':', 'x')}.mp4", caps, labels, a.start, dur, fmt, bg, font, tmp))
    if a.json:
        json.dump({"outputs": outs, "duration": round(dur, 3)}, sys.stdout)
        print()
    else:
        for o in outs:
            print(o)


if __name__ == "__main__":
    main()
