import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAME = [sys.executable, os.path.join(ROOT, "frame.py")]

pytest.importorskip("PIL")


def clip(path, color):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=390x844:r=30:d=2",
                    "-pix_fmt", "yuv420p", path], check=True)


def pixel(video, t, x, y):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", video, "-frames:v", "1",
                          "-vf", f"crop=2:2:{x // 2 * 2}:{y // 2 * 2}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return tuple(raw[:3])


def test_two_phones_in_every_format(tmp_path):
    a, b = str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4")
    clip(a, "red")
    clip(b, "blue")
    out = str(tmp_path / "duo")
    r = subprocess.run(FRAME + [out, "--cap", a, "--cap", b + "@0.5", "--label", "A", "--label", "B",
                                "--dur", "1.5", "--formats", "16:9,9:16", "--json"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    outs = json.loads(r.stdout)["outputs"]
    assert [os.path.basename(o) for o in outs] == ["duo_16x9.mp4", "duo_9x16.mp4"]
    import frame
    for o, fmt in zip(outs, ("16:9", "9:16")):
        info = frame.probe(o)
        assert info[:2] == frame.SIZES[fmt] and abs(info[2] - 1.5) < 0.1
        sw, sh, phones = frame.layout(fmt, 2, 390, 844)
        (xa, ya), (xb, yb) = phones
        ra, rb = pixel(o, 1.0, xa + sw // 2, ya + sh // 2), pixel(o, 1.0, xb + sw // 2, yb + sh // 2)
        assert ra[0] > 200 and ra[2] < 60        # first phone shows the red capture
        assert rb[2] > 200 and rb[0] < 60        # second phone shows the blue one
        bottom = ya + sh                          # phones end above the burned captions
        assert bottom < frame.SIZES[fmt][1] * (0.83 if fmt == "9:16" else 0.93)


def test_rejects_bad_input(tmp_path):
    r = subprocess.run(FRAME + [str(tmp_path / "x"), "--cap", str(tmp_path / "missing.mp4")], capture_output=True, text=True)
    assert r.returncode != 0 and "no such file" in r.stderr
