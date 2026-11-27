"""tts.py with fake backends, then --place and render: the generated-short and voice-over paths."""

import json
import os
import subprocess
import sys

import pytest

import backends
import project as proj
import tts
from conftest import ROOT, probe
from fakes import FakeTranscribe, PlainTTS, TimedTTS, even_words


@pytest.fixture
def fakes(monkeypatch):
    backends.register("tts", "f_timed", TimedTTS)
    backends.register("tts", "f_plain", PlainTTS)
    backends.register("transcribe", "f_whisper", FakeTranscribe)
    monkeypatch.setitem(backends.CONFIG, "TRANSCRIBE", "f_whisper")
    TimedTTS.calls = 0
    yield
    for k, n in (("tts", "f_timed"), ("tts", "f_plain"), ("transcribe", "f_whisper")):
        backends.unregister(k, n)


def run_tts(monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, "argv", ["tts.py", *argv, "--json"])
    code = 0
    try:
        tts.main()
    except SystemExit as e:
        code = e.code
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip() else None)


def script(d, lines):
    with open(os.path.join(d, "script.json"), "w") as f:
        json.dump({"lang": "es", "lines": lines}, f)


def test_generated_short_end_to_end(tmp_path, media, fakes, monkeypatch, capsys):
    d = str(tmp_path / "short")
    proj.new(d, "generated-short", formats=("9:16",))
    script(d, [{"id": "01-hook", "text": "¿Sabías que esto compila?", "visual": str(media / "card.png")},
               {"id": "02-code", "text": "int x = (1, 2, 3);", "spoken": "int x igual a uno dos tres",
                "visual": {"src": str(media / "rec.mp4"), "in": 1.0}}])
    code, s = run_tts(monkeypatch, capsys, "-P", d, "--backend", "f_timed", "--place")
    assert code == 0 and s["generated"] == ["01-hook", "02-code"] and s["placed"] == 2
    words = json.load(open(os.path.join(d, "narration", "02-code.words.json")))["words"]
    assert [w["w"] for w in words] == ["int", "x", "=", "(1,", "2,", "3);"]   # captions show the text, not spoken
    p = proj.load(d)
    v, a = p["timeline"]["video"], p["timeline"]["audio"]
    assert [x["line"] for x in v] == ["01-hook", "02-code"] and v[1]["in"] == 1.0 and "out" in v[1]
    assert a[0]["at"] == 0.3   # pad_before
    # same script again: nothing generated (paid backends aren't charged twice)
    code, s = run_tts(monkeypatch, capsys, "-P", d, "--backend", "f_timed")
    assert s["cached"] == ["01-hook", "02-code"] and s["generated"] == [] and TimedTTS.calls == 2
    # render it with captions burned
    p["timeline"]["captions"]["burn"] = True
    proj.save(d, p)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "render.py"), "-P", d, "--json"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    info = probe(os.path.join(d, "out", "video.mp4"))
    total = sum(x.get("dur", x.get("out", 0) - x.get("in", 0)) for x in v)
    assert abs(float(info["format"]["duration"]) - total) < 0.1
    assert "int x = (1, 2, 3);" in open(os.path.join(d, "out", "video.srt")).read()


def test_backend_without_timestamps_is_transcribed(tmp_path, fakes, monkeypatch, capsys):
    d = str(tmp_path / "p")
    proj.new(d, "generated-short")
    script(d, [{"id": "a", "text": "Hola, esto es main.c", "spoken": "Hola esto es main punto c"}])
    FakeTranscribe.heard = even_words("hola esto es main punto c")
    code, s = run_tts(monkeypatch, capsys, "-P", d, "--backend", "f_plain")
    assert code == 0
    meta = json.load(open(os.path.join(d, "narration", "a.json")))
    assert meta["timestamps"] == "transcribed" and meta["backend"] == "f_plain"
    words = json.load(open(os.path.join(d, "narration", "a.words.json")))["words"]
    assert [w["w"] for w in words] == ["Hola,", "esto", "es", "main.c"]


def test_verify_flags_garbled_lines(tmp_path, fakes, monkeypatch, capsys):
    d = str(tmp_path / "p")
    proj.new(d, "generated-short")
    script(d, [{"id": "a", "text": "El operador coma evalúa todo"}])
    FakeTranscribe.heard = even_words("el operador cegra y conoce")
    code, s = run_tts(monkeypatch, capsys, "-P", d, "--backend", "f_timed", "--verify")
    assert code == 0 and s["low_match"][0]["id"] == "a"


def test_tts_none_stops_for_a_human(tmp_path, monkeypatch, capsys):
    d = str(tmp_path / "p")
    proj.new(d, "generated-short")
    script(d, [{"id": "a", "text": "Hola"}])
    code, s = run_tts(monkeypatch, capsys, "-P", d, "--backend", "none")
    assert code == 3 and "record" in s["needs_human"]
    assert proj.state_of(proj.load(d), "tts") == "needs-human"


def test_voice_over_places_at_times_and_reports_overruns(tmp_path, fakes, monkeypatch, capsys):
    d = str(tmp_path / "vo")
    proj.new(d, "voice-over")
    script(d, [{"id": "a", "text": "uno dos tres cuatro cinco seis siete ocho", "at": 0.5},
               {"id": "b", "text": "nueve", "at": 2.0}])
    code, s = run_tts(monkeypatch, capsys, "-P", d, "--backend", "f_timed", "--place")
    assert code == 0
    assert [x["at"] for x in proj.load(d)["timeline"]["audio"]] == [0.5, 2.0]
    assert s["overruns"][0]["id"] == "a" and s["overruns"][0]["too_long_by"] > 1
