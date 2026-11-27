"""Regression tests for the review findings on capture.py, search.py and the local embed backend."""
import json
import os
import signal
import subprocess
import sys

import pytest

import backends
import capture
import search
from backends import Base, BackendError
from conftest import ff
from fakes import FakeEmbed


# ---------------------------------------------------------------- capture.py
def test_web_base_adds_a_scheme_once():
    assert capture.web_base("localhost:3000") == "http://localhost:3000"
    assert capture.web_base("localhost:3000/") == "http://localhost:3000"
    assert capture.web_base("https://x.dev/app/") == "https://x.dev/app"
    assert capture.web_base("file:///tmp/a.html") == "file:///tmp/a.html"
    assert capture.web_base("http://127.0.0.1:8000") == "http://127.0.0.1:8000"


def test_encode_capture_keeps_the_previous_file_when_ffmpeg_fails(tmp_path, monkeypatch):
    out = tmp_path / "capture.mp4"
    out.write_bytes(b"previous")

    def broken(cmd, **kw):   # writes a partial output, then fails (no space left)
        with open(cmd[-1], "wb") as f:
            f.write(b"partial")
        return subprocess.CompletedProcess(cmd, 1, "", "No space left on device")

    monkeypatch.setattr(capture.subprocess, "run", broken)
    with pytest.raises(capture.CaptureError, match="ffmpeg failed"):
        capture.encode_capture("src.mkv", str(out), 30)
    assert out.read_bytes() == b"previous"
    assert os.listdir(tmp_path) == ["capture.mp4"]   # no partial sibling left behind


def test_encode_capture_keeps_the_previous_file_when_output_is_unreadable(tmp_path, monkeypatch):
    out = tmp_path / "capture.mp4"
    out.write_bytes(b"previous")

    def writes_junk(cmd, **kw):
        with open(cmd[-1], "wb") as f:
            f.write(b"junk")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(capture.subprocess, "run", writes_junk)
    monkeypatch.setattr(capture, "probe_duration", lambda p: 0.0)
    with pytest.raises(capture.CaptureError):
        capture.encode_capture("src.mkv", str(out), 30)
    assert out.read_bytes() == b"previous"


def test_encode_capture_replaces_the_file_on_success(tmp_path):
    src = tmp_path / "in.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=1", str(src))
    out = tmp_path / "capture.mp4"
    out.write_bytes(b"previous")
    total = capture.encode_capture(str(src), str(out), 10)
    assert total > 0.5 and out.read_bytes() != b"previous"
    assert sorted(os.listdir(tmp_path)) == ["capture.mp4", "in.mp4"]


class FakeProc:
    log = []

    def __init__(self, cmd, **kw):
        self.cmd, self.name, self.dead = cmd, cmd[0], False
        FakeProc.log.append(("start", self.name))

    def poll(self):
        return 0 if self.dead else None

    def send_signal(self, sig):
        FakeProc.log.append((sig.name, self.name))
        self.dead = True

    def terminate(self):
        FakeProc.log.append(("terminate", self.name))
        self.dead = True

    def kill(self):
        self.dead = True

    def wait(self, timeout=None):
        return 0


@pytest.fixture
def fake_desktop(monkeypatch):
    FakeProc.log = []
    xdo = []
    monkeypatch.setattr(capture, "need", lambda *t: None)
    monkeypatch.setattr(capture, "free_display", lambda: ":91")
    monkeypatch.setattr(capture.time, "sleep", lambda s: None)
    monkeypatch.setattr(capture.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(capture.subprocess, "run",
                        lambda cmd, **kw: xdo.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    return xdo


def test_desktop_types_option_like_text_after_double_dash(fake_desktop, tmp_path):
    lines = [{"id": "01", "actions": [{"type": "--help"}, {"run": "ls -l"}]}]
    raw, events, _ = capture.capture_desktop(lines, str(tmp_path), "gedit", 1280, 720, 30, str(tmp_path), None, False)
    typed = [c for c in fake_desktop if c[:2] == ["xdotool", "type"]]
    assert typed[0] == ["xdotool", "type", "--delay", "70", "--", "--help"]
    assert typed[1][-2:] == ["--", "ls -l"]


def test_desktop_recorder_is_stopped_before_the_screen_when_an_action_fails(fake_desktop, tmp_path):
    lines = [{"id": "01", "actions": [{"bogus": 1}]}]
    with pytest.raises(capture.CaptureError, match="unknown desktop action"):
        capture.capture_desktop(lines, str(tmp_path), "gedit", 1280, 720, 30, str(tmp_path), None, False)
    log = FakeProc.log
    assert ("SIGINT", "ffmpeg") in log
    assert log.index(("SIGINT", "ffmpeg")) < log.index(("terminate", "Xvfb"))


def test_desktop_recorder_is_stopped_once_on_success(fake_desktop, tmp_path):
    lines = [{"id": "01", "actions": [{"wait": 0.1}]}]
    capture.capture_desktop(lines, str(tmp_path), "gedit", 1280, 720, 30, str(tmp_path), None, False)
    assert FakeProc.log.count(("SIGINT", "ffmpeg")) == 1


# ---------------------------------------------------------------- search.py
def test_window_must_be_positive_and_finite(monkeypatch, tmp_path):
    for bad in ("0", "-5", "nan", "inf", "abc"):
        monkeypatch.setattr(sys, "argv", ["search", "index", str(tmp_path / "x.mp4"), "--window", bad])
        with pytest.raises(SystemExit) as e:
            search.main()
        assert e.value.code == 2
    assert search.positive_seconds("2.5") == 2.5


class OtherEmbed(FakeEmbed):
    model = "other-embed"


class DownEmbed(FakeEmbed):
    model = "down-embed"

    def embed(self, items, task="document"):
        raise BackendError("quota exhausted")


@pytest.fixture
def embeds(tmp_path, monkeypatch):
    monkeypatch.setitem(backends.CONFIG, "SEARCH_INDEX", str(tmp_path / "idx.json"))
    monkeypatch.setitem(backends.CONFIG, "TRANSCRIBE", "none_at_all")
    for n, c in (("r_a", FakeEmbed), ("r_b", OtherEmbed), ("r_down", DownEmbed)):
        backends.register("embed", n, c)
    yield
    for n in ("r_a", "r_b", "r_down"):
        backends.unregister("embed", n)


def make_video(tmp_path, name, secs=12):
    v = tmp_path / name
    ff("-f", "lavfi", "-i", f"testsrc2=s=160x90:d={secs}", str(v))
    return v


def run_search(monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, "argv", ["search", *argv])
    code = 0
    try:
        search.main()
    except SystemExit as e:
        code = e.code
    out = capsys.readouterr()
    return code, out.out, out.err


def test_model_change_reembeds_the_union_and_keeps_old_recordings(tmp_path, embeds, monkeypatch, capsys):
    a, b = make_video(tmp_path, "a.mp4"), make_video(tmp_path, "b.mp4")
    assert run_search(monkeypatch, capsys, "index", str(a), "--backend", "r_a")[0] == 0
    before = search.load_index()
    run_search(monkeypatch, capsys, "index", str(b), "--backend", "r_b")
    idx = search.load_index()
    assert idx["backend"] == "r_b" and idx["key"] != before["key"]
    assert set(idx["files"]) == {str(a), str(b)}   # A was not discarded
    assert all(w["vec"] for e in idx["files"].values() for w in e["windows"])


def test_failed_rebuild_leaves_the_old_index_on_disk(tmp_path, embeds, monkeypatch, capsys):
    a, b = make_video(tmp_path, "a.mp4"), make_video(tmp_path, "b.mp4")
    run_search(monkeypatch, capsys, "index", str(a), "--backend", "r_a")
    before = search.load_index()
    code, _, _ = run_search(monkeypatch, capsys, "index", str(b), "--backend", "r_down")
    assert code != 0 and search.load_index() == before


def test_index_falls_back_to_the_next_embed_backend(tmp_path, embeds, monkeypatch, capsys):
    a, b = make_video(tmp_path, "a.mp4"), make_video(tmp_path, "b.mp4")
    run_search(monkeypatch, capsys, "index", str(a), "--backend", "r_a")
    before = search.load_index()
    code, _, err = run_search(monkeypatch, capsys, "index", str(b), "--backend", "r_down,r_b")
    assert code == 0, err
    idx = search.load_index()
    assert idx["backend"] == "r_b" and idx["key"] != before["key"]
    assert set(idx["files"]) == {str(a), str(b)}   # the whole index was redone with the fallback's model


def test_query_is_pinned_to_the_index_model(tmp_path, embeds, monkeypatch, capsys):
    a = make_video(tmp_path, "a.mp4")
    run_search(monkeypatch, capsys, "index", str(a), "--backend", "r_a")
    monkeypatch.setitem(backends.CONFIG, "EMBED", "r_b")
    code, out, _ = run_search(monkeypatch, capsys, "query", "hola", "--json")
    assert code == 0 and json.loads(out)   # still answered by r_a, not r_b


# ---------------------------------------------------------------- embed/local.py
def test_local_embed_cache_id_includes_the_pretrained_weights(monkeypatch):
    from backends.embed import local
    monkeypatch.delitem(local.CONFIG, "OPENCLIP_PRETRAINED", raising=False)   # the user's config doesn't leak in
    one = local.Backend().cache_id()
    monkeypatch.setitem(local.CONFIG, "OPENCLIP_PRETRAINED", "other_weights")
    two = local.Backend().cache_id()
    assert one != two and "other_weights" in two
    monkeypatch.delitem(local.CONFIG, "OPENCLIP_PRETRAINED")
    assert local.Backend().cache_id() == one   # same config, same identity


# ---------------------------------------------------------------- flows/app-demo.md
def test_app_demo_flow_does_not_overpromise():
    text = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "flows", "app-demo.md"),
                encoding="utf-8").read()
    assert "initial demo state" in text
    assert "does NOT see a terminal command fail" in text
    assert "get no\n   proposed zoom" in text
    assert "throwaway copy" in text


# ---------------------------------------------------------------- second review round
def test_corrected_transcript_reindexes(tmp_path, embeds, monkeypatch, capsys):
    a = make_video(tmp_path, "a.mp4")
    words = tmp_path / "a.words.json"
    words.write_text(json.dumps({"words": [{"w": "hola", "s": 1.0, "e": 1.5}]}))
    run_search(monkeypatch, capsys, "index", str(a), "--backend", "r_a")
    _, _, err = run_search(monkeypatch, capsys, "index", str(a), "--backend", "r_a")
    assert "unchanged" in err
    words.write_text(json.dumps({"words": [{"w": "adiós", "s": 1.0, "e": 1.5}]}))
    _, _, err = run_search(monkeypatch, capsys, "index", str(a), "--backend", "r_a")
    assert "unchanged" not in err
    texts = " ".join(w["text"] for w in search.load_index()["files"][str(a)]["windows"])
    assert "adiós" in texts and "hola" not in texts


def test_window_without_frame_or_speech_is_not_embedded(tmp_path, embeds, monkeypatch, capsys):
    a = make_video(tmp_path, "a.mp4")
    monkeypatch.setattr(search, "frames", lambda *a, **kw: [])
    run_search(monkeypatch, capsys, "index", str(a), "--backend", "r_a")
    assert search.load_index()["files"][str(a)]["windows"] == []
