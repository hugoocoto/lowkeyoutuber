import json
import os
import shutil
import subprocess
import sys
import zipfile

import pytest

import backends
import project as proj
from backends.music._midi import MidiFile
from backends.music._styles import build_midi
from conftest import ROOT, ff, probe

SOUND = [sys.executable, os.path.join(ROOT, "sound.py")]


def test_midi_writer_and_adaptive_sections(tmp_path):
    mf = MidiFile(bpm=100, seed=7)
    tr = mf.add_track("Lead", channel=0, program=4)
    mf.add_note(tr, 0.0, 1.0, 60, 90, jitter=0.01, vel_jitter=4)
    mf.add_note(tr, 1.0, 1.0, 64, 85)
    raw = mf.to_bytes()
    assert raw.startswith(b"MThd") and raw.count(b"MTrk") == 2

    mid = str(tmp_path / "adaptive.mid")
    sections = [
        {"start": 0.0, "end": 4.0, "mood": "hook", "energy": 0.8, "drums": True},
        {"start": 4.0, "end": 10.0, "mood": "explain", "energy": 0.35, "drums": False},
        {"start": 10.0, "end": 14.0, "mood": "tension", "energy": 0.7, "drums": True},
    ]
    _, meta = build_midi(mid, duration=14.0, style="lofi", sections=sections, seed=3)
    assert meta["style"] == "lofi" and meta["bars"] >= 3
    assert os.path.getsize(mid) > 100


@pytest.mark.skipif(not shutil.which("fluidsynth"), reason="fluidsynth not installed")
def test_composer_renders_real_wav(tmp_path):
    comp = backends.load("music", "composer")
    ok, why = comp.check()
    if not ok:
        pytest.skip(why)
    out = str(tmp_path / "song.wav")
    path, meta = comp.compose(
        out,
        prompt="calm coding tutorial",
        duration=8.0,
        style="ambient_tech",
        sections=[{"start": 0.0, "end": 4.0, "mood": "hook", "energy": 0.75},
                  {"start": 4.0, "end": 8.0, "mood": "resolve", "energy": 0.4}],
    )
    assert os.path.isfile(path)
    info = probe(path)
    dur = float(info["format"]["duration"])
    assert 7.5 <= dur <= 8.2
    assert meta["style"] == "ambient_tech"


def test_sfx_synth_and_import_pack(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    synth = backends.load("sfx", "synth")
    for q in ("soft whoosh", "ui click", "success chime", "error buzz"):
        p = synth.get(q, max_dur=0.8)
        assert os.path.isfile(p)
        assert float(probe(p)["format"]["duration"]) <= 0.85

    # Import a zip pack and find it via sfx/library
    src_wav = tmp_path / "retro_laser_click.wav"
    ff("-f", "lavfi", "-i", "sine=f=880:d=0.2", str(src_wav))
    zpath = tmp_path / "pack.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(src_wav, "Audio/retro_laser_click.wav")

    r = subprocess.run(SOUND + ["import-pack", str(zpath), "--tag", "retro", "--json"],
                       capture_output=True, text=True,
                       env={**os.environ, "XDG_CACHE_HOME": str(tmp_path / "cache")})
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["imported"] == 1

    lib = backends.load("sfx", "library").__class__()
    found = lib.get("laser click", max_dur=1.0)
    assert os.path.basename(found) == "retro_laser_click.wav"


@pytest.mark.skipif(not shutil.which("fluidsynth"), reason="fluidsynth not installed")
def test_sound_propose_place_and_render(tmp_path, media):
    comp = backends.load("music", "composer")
    if not comp.check()[0]:
        pytest.skip("no soundfont")
    d = str(tmp_path / "proj")
    proj.new(d, "generated-short", formats=("16:9",))
    with open(os.path.join(d, "script.json"), "w", encoding="utf-8") as f:
        json.dump({
            "title": "Test",
            "lang": "en",
            "lines": [
                {"id": "01-hook", "text": "Look at this bug!", "scene": {"type": "title"}},
                {"id": "02-code", "text": "This pointer crashes on error.", "scene": {"type": "code"}},
                {"id": "03-out", "text": "Fixed cleanly.", "scene": {"type": "title"}},
            ],
        }, f)
    p = proj.load(d)
    vo = tmp_path / "vo.wav"
    ff("-f", "lavfi", "-i", "sine=f=300:d=1.5", str(vo))
    p["media"] = {"rec": {"file": str(media / "rec.mp4")}, "vo:01-hook": {"file": str(vo), "kind": "audio"}}
    p["timeline"] = {
        "video": [{"src": "rec", "in": 0, "out": 6.0}],
        "audio": [{"src": "vo:01-hook", "at": 0.2}],
        "captions": {"burn": False, "style": "word-highlight"},
    }
    proj.save(d, p)

    env = {**os.environ, "XDG_CACHE_HOME": str(tmp_path / "cache")}
    r = subprocess.run(
        SOUND + ["-P", d, "--propose", "--place", "--no-llm", "--sfx-backend", "synth", "--json"],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, r.stderr
    res = json.loads(r.stdout)
    assert os.path.isfile(os.path.join(d, "proposals", "audio.json"))
    assert res["placed"]["music"]["backend"] == "composer"
    assert len(res["placed"]["sfx"]) >= 1

    # Re-placing does not duplicate music/sfx entries and preserves vo:01-hook
    r2 = subprocess.run(
        SOUND + ["-P", d, "--place", "--no-llm", "--sfx-backend", "synth", "--json"],
        capture_output=True, text=True, env=env,
    )
    assert r2.returncode == 0, r2.stderr
    pj2 = proj.load(d)
    srcs = [it["src"] for it in pj2["timeline"]["audio"]]
    assert "vo:01-hook" in srcs
    assert srcs.count("bg-music") == 1
    assert sum(s.startswith("sfx:") for s in srcs) == len(res["placed"]["sfx"])

    # Full render with generated music + synth SFX + voice succeeds
    rr = subprocess.run([sys.executable, os.path.join(ROOT, "render.py"), "-P", d, "--json"],
                        capture_output=True, text=True)
    assert rr.returncode == 0, rr.stderr
    assert os.path.isfile(os.path.join(d, "out", "video.mp4"))
