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


def test_square_and_4x5_formats(tmp_path, media):
    d = make_project(tmp_path, media, formats=("1:1", "4:5"))
    s = render(d)
    assert s["outputs"] == ["out/video.mp4", "out/video_4x5.mp4"]
    for f, (w, h) in (("video.mp4", (1080, 1080)), ("video_4x5.mp4", (1080, 1350))):
        v = next(x for x in probe(os.path.join(d, "out", f))["streams"] if x["codec_type"] == "video")
        assert (v["width"], v["height"]) == (w, h)


def test_square_captions_are_short_and_big():
    import render as r
    words = [{"w": w, "s": i * 0.3, "e": i * 0.3 + 0.25} for i, w in enumerate("uno dos tres cuatro cinco seis".split())]
    wide, square = r.build_ass(words, 1920, 1080, "plain"), r.build_ass(words, 1080, 1080, "plain")
    assert "UNO DOS TRES CUATRO\n" in square and "uno dos tres cuatro cinco seis" in wide
    fs = [int(t.split("Style: Default,")[1].split(",")[1]) for t in (wide, square)]
    assert fs[1] > fs[0]


def test_captions_hang_from_a_fixed_top_edge():
    # top-centre alignment: a caption that wraps to two lines grows downwards, its first line doesn't move
    import render as r
    words = [{"w": "hola", "s": 0.0, "e": 0.5}]
    for W, H in ((1920, 1080), (1080, 1920), (1080, 1080)):
        style = r.build_ass(words, W, H, "plain").split("Style: Default,")[1].split("\n")[0].split(",")
        fs, align, top = int(style[1]), int(style[17]), int(style[20])
        assert align == 8 and top + 2 * fs * 1.2 < H   # room for a second line below the first


def test_caption_colours():
    import render as r
    words = [{"w": "hola", "s": 0.0, "e": 0.5}]
    style = lambda **k: r.build_ass(words, 1920, 1080, "plain", **k).split("Style: Default,")[1].split("\n")[0].split(",")
    assert style()[4] == "&H00000000" and style()[14] == "1"              # black outline by default
    assert style(outline="460618")[4] == "&H00180646"                     # RRGGBB -> ASS BGR
    s = style(color="1C1F24", box="FFFFFF")
    assert s[2] == "&H00241F1C" and s[4] == "&H00FFFFFF" and s[14] == "3"   # ink text in a white box


def test_render_writes_a_transcript_cache_the_tools_reuse(tmp_path, media):
    import argparse
    import yt_common
    d = make_project(tmp_path, media, formats=("16:9", "9:16"), burn=False)
    render(d)
    for name in ("video", "video_9x16"):
        video = os.path.join(d, "out", name + ".mp4")
        args = argparse.Namespace(model=yt_common.WHISPER_MODEL, prompt=yt_common.CONFIG.get("JARGON") or None,
                                  retranscribe=False, language=None)
        data = yt_common.cached_transcript(args, video, yt_common.file_src(video, 0, args))
        assert data and [w["w"] for w in data["words"]] == ["Hola,", "esto", "es", "una", "prueba."]


def test_relative_project_path(tmp_path, media):
    # -P relative to the current folder: ffmpeg runs in a temp folder, so project files must be absolute
    d = make_project(tmp_path, media, formats=("16:9",), burn=True)
    r = subprocess.run(RENDER + ["-P", os.path.basename(d), "--json", "--preview"], capture_output=True, text=True,
                       cwd=os.path.dirname(d))
    assert r.returncode == 0, r.stderr
    assert os.path.isfile(os.path.join(d, "out", "preview.mp4"))


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


def test_sfx_bus_and_loop_fade(tmp_path):
    import render as rd
    from conftest import ff
    ff("-f", "lavfi", "-i", "sine=f=3000:d=2", str(tmp_path / "m.wav"))
    ff("-f", "lavfi", "-i", "sine=f=200:d=1", str(tmp_path / "sfx.wav"))
    auds = [
        {"file": str(tmp_path / "m.wav"), "a": 0, "d": 6.0, "seg_d": 2.0, "at": 0, "duck": True,
         "bus": "music", "loop": True, "fade_in": 0.5, "fade_out": 0.8, "gain": -12, "track": 0},
        {"file": str(tmp_path / "sfx.wav"), "a": 0, "d": 1.0, "at": 2.5, "duck": False,
         "bus": "sfx", "loop": False, "fade_in": 0.0, "fade_out": 0.0, "gain": -3, "track": 0},
    ]
    rd.mix_audio([], auds, 6.0, str(tmp_path / "mix.wav"), str(tmp_path))

    def band_rms(a, b, filt):
        r = subprocess.run(["ffmpeg", "-nostdin", "-ss", str(a), "-to", str(b), "-i", str(tmp_path / "mix.wav"),
                            "-af", f"{filt},astats=metadata=0", "-f", "null", "-"],
                           capture_output=True, text=True)
        return float([x for x in r.stderr.splitlines() if "RMS level dB" in x][-1].split()[-1])

    # Looped music still sounds past 2 s (at 4.2 s), fades at start/end, and isn't ducked by sfx at 2.7 s
    head = band_rms(0.05, 0.2, "highpass=f=2000,highpass=f=2000")
    mid = band_rms(4.0, 4.8, "highpass=f=2000,highpass=f=2000")
    during_sfx = band_rms(2.6, 3.3, "highpass=f=2000,highpass=f=2000")
    tail = band_rms(5.5, 5.95, "highpass=f=2000,highpass=f=2000")
    assert mid > -42
    assert head < mid - 5.0
    assert abs(during_sfx - mid) < 2.0
    assert tail < mid - 5.0
