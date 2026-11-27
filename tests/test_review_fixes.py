"""Regressions for the problems found in review of the agent toolkit (PR #2). Fakes only: no models,
no network, no paid backend."""

import json
import os
import subprocess
import sys

import pytest

import align
import backends
import lky
import project as proj
from conftest import ROOT, ff
from fakes import FakeTranscribe, PlainTTS, TimedTTS, even_words, tone
from test_tts_flow import fakes, run_tts, script  # noqa: F401  (fixture)

LKY = [sys.executable, os.path.join(ROOT, "lky.py")]


def narr(d, lid, ext):
    return os.path.join(d, "narration", lid + ext)


# --- tts.py -------------------------------------------------------------------------------------
def test_editing_only_text_retimes_captions_without_regenerating(tmp_path, fakes, monkeypatch, capsys):
    d = str(tmp_path / "p")
    proj.new(d, "generated-short")
    script(d, [{"id": "a", "text": "abre main.c", "spoken": "abre main punto c"}])
    run_tts(monkeypatch, capsys, "-P", d, "--backend", "f_timed")
    script(d, [{"id": "a", "text": "abre main.cpp", "spoken": "abre main punto c"}])
    code, s = run_tts(monkeypatch, capsys, "-P", d, "--backend", "f_timed")
    assert code == 0 and s["cached"] == ["a"] and TimedTTS.calls == 1   # no new paid generation
    words = json.load(open(narr(d, "a", ".words.json")))["words"]
    assert [w["w"] for w in words] == ["abre", "main.cpp"]


def test_verify_checks_cached_lines(tmp_path, fakes, monkeypatch, capsys):
    d = str(tmp_path / "p")
    proj.new(d, "generated-short")
    script(d, [{"id": "a", "text": "hola esto es una prueba"}])
    run_tts(monkeypatch, capsys, "-P", d, "--backend", "f_timed")
    FakeTranscribe.heard = even_words("adiós nada que ver")
    code, s = run_tts(monkeypatch, capsys, "-P", d, "--backend", "f_timed", "--verify")
    assert s["cached"] == ["a"] and TimedTTS.calls == 1
    assert [x["id"] for x in s["low_match"]] == ["a"]
    assert json.load(open(narr(d, "a", ".json")))["match"] < 0.75


def test_failed_regeneration_keeps_the_previous_one(tmp_path, fakes, monkeypatch, capsys):
    d = str(tmp_path / "p")
    proj.new(d, "generated-short")
    script(d, [{"id": "a", "text": "hola esto es main"}])
    FakeTranscribe.heard = even_words("hola esto es main")
    assert run_tts(monkeypatch, capsys, "-P", d, "--backend", "f_plain")[0] == 0
    before = {e: open(narr(d, "a", e), "rb").read() for e in (".wav", ".words.json", ".json")}

    def broken(self, *a, **kw):
        raise backends.BackendError("whisper crashed")
    monkeypatch.setattr(FakeTranscribe, "words", broken)
    # the new take sounds different, so an overwritten .wav would show
    monkeypatch.setattr(PlainTTS, "speak", lambda self, text, out_base, **kw:
                        {"audio": tone(out_base + ".wav", 2.0, freq=900), "words": None, "model": self.model})
    code, s = run_tts(monkeypatch, capsys, "-P", d, "--backend", "f_plain", "-f")
    assert code == 1 and s["failed"][0]["id"] == "a"
    assert {e: open(narr(d, "a", e), "rb").read() for e in before} == before
    assert sorted(os.listdir(os.path.join(d, "narration"))) == ["a.json", "a.wav", "a.words.json"]


# --- align.py -----------------------------------------------------------------------------------
def test_align_apply_clamps_to_the_recording_and_keeps_the_track(tmp_path, media, fakes, monkeypatch, capsys):
    d = str(tmp_path / "p")
    proj.new(d, "script-then-record")
    pj = proj.load(d)
    pj["media"]["rec"] = {"file": str(media / "rec.mp4"), "kind": "video"}
    proj.save(d, pj)
    script(d, [{"id": "a", "text": "uno dos tres"}])
    FakeTranscribe.heard = [{"w": "uno", "s": 4.5, "e": 5.0}, {"w": "dos", "s": 5.1, "e": 5.5},
                            {"w": "tres", "s": 5.6, "e": 6.0}]   # the last word ends with the 6 s file
    seen = {}
    real = FakeTranscribe.words

    def spy(self, m, lang=None, prompt=None, track=0):
        seen["track"] = track
        return real(self, m, lang, prompt, track)
    monkeypatch.setattr(FakeTranscribe, "words", spy)
    monkeypatch.setattr(sys, "argv", ["align.py", "-P", d, "--media", "rec", "--track", "1", "--apply"])
    align.main()
    pj = proj.load(d)
    assert seen["track"] == 1 and pj["media"]["rec"]["track"] == 1
    assert pj["timeline"]["video"][0]["out"] <= 6.0 + 0.05


def test_align_uses_the_media_track_by_default(tmp_path, media, fakes, monkeypatch):
    d = str(tmp_path / "p")
    proj.new(d, "script-then-record")
    pj = proj.load(d)
    pj["media"]["rec"] = {"file": str(media / "rec.mp4"), "kind": "video", "track": 1}
    proj.save(d, pj)
    script(d, [{"id": "a", "text": "uno"}])
    FakeTranscribe.heard = [{"w": "uno", "s": 1.0, "e": 1.4}]
    seen = {}

    def spy(self, m, lang=None, prompt=None, track=0):
        seen["track"] = track
        return list(FakeTranscribe.heard)
    monkeypatch.setattr(FakeTranscribe, "words", spy)
    monkeypatch.setattr(sys, "argv", ["align.py", "-P", d, "--media", "rec"])
    align.main()
    assert seen["track"] == 1


# --- backends -----------------------------------------------------------------------------------
def test_faster_whisper_failures_become_backend_errors(monkeypatch):
    from backends.transcribe import faster_whisper as fw
    from yt_common import die

    monkeypatch.setattr(fw, "get_transcript", lambda args, media: die("could not read audio track 1"))
    with pytest.raises(backends.BackendError, match="could not read audio track 1"):
        fw.Backend().words("x.mkv")

    def oserror(args, media):
        raise OSError("model download failed")
    monkeypatch.setattr(fw, "get_transcript", oserror)
    with pytest.raises(backends.BackendError, match="model download failed"):
        fw.Backend().words("x.mkv")


# --- render.py ----------------------------------------------------------------------------------
def test_changing_a_media_track_renders_again(tmp_path, media):
    two = str(tmp_path / "two.mkv")
    ff("-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=2", "-f", "lavfi", "-i", "sine=f=300:d=2",
       "-f", "lavfi", "-i", "sine=f=900:d=2", "-map", "0", "-map", "1", "-map", "2",
       "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", two)
    d = str(tmp_path / "p")
    proj.new(d, "edit-recording")
    pj = proj.load(d)
    pj["media"]["rec"] = {"file": two, "kind": "video", "track": 0}
    pj["timeline"]["video"] = [{"src": "rec", "in": 0, "out": 2}]
    proj.save(d, pj)
    cmd = [sys.executable, os.path.join(ROOT, "render.py"), "-P", d, "--json"]
    assert subprocess.run(cmd, capture_output=True, text=True).returncode == 0
    pj = proj.load(d)
    pj["media"]["rec"]["track"] = 1
    proj.save(d, pj)
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["skipped"] == []


# --- lky ----------------------------------------------------------------------------------------
def test_skill_frontmatter_is_valid_yaml():
    yaml = pytest.importorskip("yaml")
    for f in lky.flows():
        meta = yaml.safe_load(lky.skill_text(f).split("---")[1])
        assert meta["name"] == f["name"] and meta["description"].startswith("lowkeyoutuber flow: ")


def test_new_keeps_an_existing_script(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    (d / "script.json").write_text(json.dumps({"lines": [{"id": "a", "text": "hola"}]}))
    r = subprocess.run(LKY + ["new", "voice-over", str(d)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads((d / "script.json").read_text())["lines"] == [{"id": "a", "text": "hola"}]


def test_copied_media_with_the_same_name_dont_overwrite_each_other(tmp_path, media):
    d = str(tmp_path / "p")
    proj.new(d, "edit-recording")
    for cam, freq in (("a", 300), ("b", 900)):
        os.makedirs(tmp_path / cam)
        ff("-f", "lavfi", "-i", f"sine=f={freq}:d=1", str(tmp_path / cam / "rec.wav"))
        r = subprocess.run(LKY + ["add-media", "-P", d, str(tmp_path / cam / "rec.wav"), "--id", f"cam-{cam}",
                                  "--copy"], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    m = proj.load(d)["media"]
    assert m["cam-a"]["file"] != m["cam-b"]["file"]
    for cam in ("a", "b"):
        copied = proj.resolve(d, m[f"cam-{cam}"]["file"])
        assert open(copied, "rb").read() == open(tmp_path / cam / "rec.wav", "rb").read()


def test_transcribe_falls_back_to_the_next_backend(tmp_path, media, fakes, monkeypatch):
    class Broken(FakeTranscribe):
        def words(self, *a, **kw):
            raise backends.BackendError("CUDA out of memory")
    backends.register("transcribe", "f_broken", Broken)
    try:
        monkeypatch.setitem(backends.CONFIG, "TRANSCRIBE", "f_broken,f_whisper")
        d = str(tmp_path / "p")
        proj.new(d, "script-then-record")
        pj = proj.load(d)
        pj["media"]["rec"] = {"file": str(media / "rec.mp4"), "kind": "video"}
        proj.save(d, pj)
        script(d, [{"id": "a", "text": "uno"}])
        FakeTranscribe.heard = [{"w": "uno", "s": 1.0, "e": 1.4}]
        monkeypatch.setattr(sys, "argv", ["align.py", "-P", d, "--media", "rec", "--apply"])
        align.main()
        assert proj.load(d)["timeline"]["video"][0]["src"] == "rec"
    finally:
        backends.unregister("transcribe", "f_broken")


def test_interrupted_commit_leaves_no_cache_key(tmp_path, monkeypatch):
    import tts
    base = str(tmp_path / "a")
    for ext in (".wav", ".words.json", ".json"):
        open(base + ext, "w").write("old")
        open(base + {".wav": ".new.wav"}.get(ext, ext + ".tmp"), "w").write("new")
    real, calls = os.replace, []

    def interrupted(src, dst):
        calls.append(dst)
        if len(calls) == 2:
            raise KeyboardInterrupt
        real(src, dst)
    monkeypatch.setattr(tts.os, "replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        tts.commit([(base + ".new.wav", base + ".wav"), (base + ".words.json.tmp", base + ".words.json"),
                    (base + ".json.tmp", base + ".json")], base + ".json")
    assert open(base + ".wav").read() == "new" and not os.path.exists(base + ".json")
