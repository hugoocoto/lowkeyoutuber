import json
import os
import subprocess
import sys

import pytest

import project as proj
from conftest import ROOT, probe

RENDER = [sys.executable, os.path.join(ROOT, "render.py")]


def make_project(tmp_path, media, formats=("16:9", "9:16"), burn=True):
    d = str(tmp_path / "p")
    proj.new(d, "edit-recording", formats=formats)
    with open(os.path.join(d, "media", "rec.words.json"), "w") as f:
        json.dump({"words": [{"w": "Hola,", "s": 0.5, "e": 0.9}, {"w": "esto", "s": 1.0, "e": 1.3},
                             {"w": "es", "s": 1.35, "e": 1.5}, {"w": "una", "s": 1.6, "e": 1.8},
                             {"w": "prueba.", "s": 1.9, "e": 2.4}]}, f)
    p = proj.load(d)
    p["media"] = {"rec": {"file": str(media / "rec.mp4"), "words": "media/rec.words.json"},
                  "card": {"file": str(media / "card.png"), "kind": "image"}}
    p["timeline"] = {
        "video": [{"color": "#202020", "dur": 1.0},
                  {"src": "rec", "in": 0.5, "out": 4.5, "zooms": [{"at": 1.0, "dur": 2.0, "rect": [0.5, 0.5, 0.4, 0.4]}]},
                  {"src": "card", "dur": 1.5}],
        "audio": [{"src": str(media / "music.wav"), "at": 0, "duck": True}],
        "captions": {"burn": burn, "style": "word-highlight"}}
    proj.save(d, p)
    return d


def render(d, *extra):
    r = subprocess.run(RENDER + ["-P", d, "--json", *extra], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def loudness(path):
    r = subprocess.run(["ffmpeg", "-nostdin", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True)
    tail = r.stderr[r.stderr.rfind("Summary:"):]
    return float(tail.split("I:")[1].split("LUFS")[0])


def test_render_formats_captions_loudness(tmp_path, media):
    d = make_project(tmp_path, media)
    s = render(d, "--frames", "2.5")
    assert s["outputs"] == ["out/video.mp4", "out/video_9x16.mp4"] and s["duration"] == 6.5
    for f, (w, h) in (("video.mp4", (1920, 1080)), ("video_9x16.mp4", (1080, 1920))):
        info = probe(os.path.join(d, "out", f))
        v = next(x for x in info["streams"] if x["codec_type"] == "video")
        assert (v["width"], v["height"]) == (w, h)
        assert abs(float(info["format"]["duration"]) - 6.5) < 0.1
        assert abs(loudness(os.path.join(d, "out", f)) + 14) < 1
    srt = open(os.path.join(d, "out", "video.srt")).read()
    # words moved to the timeline: rec starts at 1.0 s and is cut from 0.5 s, so "Hola," is at 1.0 s
    assert "00:00:01,000 -->" in srt and "Hola, esto es una prueba." in srt
    assert len(s["frames"]) == 2
    # nothing changed: skipped; forced: rendered again
    assert render(d)["skipped"] == ["out/video.mp4", "out/video_9x16.mp4"]
    assert render(d, "-f", "--format", "9:16")["outputs"] == ["out/video_9x16.mp4"]


def test_zoom_changes_the_picture(tmp_path, media):
    d = make_project(tmp_path, media, formats=("16:9",), burn=False)
    render(d, "--frames", "1.2,3.0")   # 0.2 s into the clip (no zoom) and 2 s in (zoomed)
    frames = sorted(os.listdir(os.path.join(d, "checks", "frames")))
    sizes = [os.path.getsize(os.path.join(d, "checks", "frames", f)) for f in frames]
    assert len(frames) == 2 and sizes[0] != sizes[1]


def test_preview_is_half_size(tmp_path, media):
    d = make_project(tmp_path, media, formats=("16:9",))
    s = render(d, "--preview")
    assert s["outputs"] == ["out/preview.mp4"]
    v = next(x for x in probe(os.path.join(d, "out", "preview.mp4"))["streams"] if x["codec_type"] == "video")
    assert (v["width"], v["height"]) == (960, 540)


@pytest.mark.parametrize("change, message", [
    (lambda p: p["timeline"]["video"][1]["zooms"].append({"at": 1.5, "dur": 1.0, "rect": [0, 0, 0.5, 0.5]}), "overlap"),
    (lambda p: p["timeline"]["video"][1].update(out=60), "past the end"),
    (lambda p: p["timeline"].update(video=[]), "empty"),
])
def test_errors_are_clear(tmp_path, media, change, message):
    d = make_project(tmp_path, media, formats=("16:9",))
    p = proj.load(d)
    change(p)
    proj.save(d, p)
    r = subprocess.run(RENDER + ["-P", d], capture_output=True, text=True)
    assert r.returncode != 0 and message in r.stderr


def test_audio_track_is_as_long_as_the_video(tmp_path, media):
    """Regression: apad leaves broken timestamps and atrim cut the mix short."""
    d = make_project(tmp_path, media, formats=("16:9",), burn=False)
    render(d)
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", os.path.join(d, "out", "video.mp4"), "-map", "0:a",
                        "-f", "wav", os.path.join(d, "a.wav")], capture_output=True)
    assert r.returncode == 0
    dur = float(probe(os.path.join(d, "a.wav"))["format"]["duration"])
    assert abs(dur - 6.5) < 0.05


def test_music_ducks_under_the_voice(tmp_path):
    import render as rd
    from conftest import ff
    ff("-f", "lavfi", "-i", "sine=f=3000:d=10", str(tmp_path / "m.wav"))
    ff("-f", "lavfi", "-i", "sine=f=200:d=2", str(tmp_path / "v.wav"))
    auds = [{"file": str(tmp_path / "v.wav"), "a": 0, "d": 2, "at": 2.0, "duck": False, "gain": 0, "track": 0},
            {"file": str(tmp_path / "m.wav"), "a": 0, "d": 10, "at": 0, "duck": True, "gain": -22, "track": 0}]
    rd.mix_audio([], auds, 6.0, str(tmp_path / "mix.wav"), str(tmp_path))

    def music_rms(a, b):   # the 3 kHz music only (the voice is a 200 Hz tone)
        r = subprocess.run(["ffmpeg", "-nostdin", "-ss", str(a), "-to", str(b), "-i", str(tmp_path / "mix.wav"), "-af",
                            "highpass=f=2000,highpass=f=2000,astats=metadata=0", "-f", "null", "-"],
                           capture_output=True, text=True)
        return float([x for x in r.stderr.splitlines() if "RMS level dB" in x][-1].split()[-1])

    before, during = music_rms(0.2, 1.8), music_rms(2.6, 3.8)
    assert during < before - 5
