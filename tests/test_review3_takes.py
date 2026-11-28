"""Regressions for the review of PR #3 (teleprompter, align --from-takes, tts placement).
Fakes only: no OBS, no websocket packages, no models, no network."""

import json
import os
import sys
import threading
import urllib.error
import urllib.request

import pytest

import align
import backends
import project as proj
import teleprompter as tp
from fakes import FakeTranscribe, even_words, tone
from test_tts_flow import fakes, run_tts, script  # noqa: F401  (fixture)


def lines():
    return [{"id": "a", "text": "Hoy vamos a ver punteros."}, {"id": "b", "text": "Un puntero guarda una dirección."}]


class FakeOBSClient:
    """Stands in for teleprompter.OBS: records requests, one file per StopRecord."""

    def __init__(self, active_polls=0):
        self.sent, self.active, self.n, self.active_polls = [], False, 0, active_polls
        self.fail_stop = False

    def request(self, kind, data=None):
        self.sent.append(kind)
        if kind == "StartRecord":
            if self.active:
                raise tp.OBSError("StartRecord failed: 500")
            self.active = True
            return {}
        if kind == "StopRecord":
            if self.fail_stop:
                self.active = False
                raise tp.OBSError("lost the connection to OBS during StopRecord")
            self.n += 1
            self.stopping = self.active_polls
            self.active = False if not self.active_polls else True
            return {"outputPath": f"/tmp/take{self.n}.mkv"}
        if kind == "GetRecordStatus":
            if self.active and getattr(self, "stopping", 0):
                self.stopping -= 1
                if not self.stopping:
                    self.active = False
            return {"outputActive": self.active}
        raise AssertionError(kind)

    def close(self):
        pass


# --- teleprompter: take ledger ------------------------------------------------------------------
def test_reopening_in_obs_mode_keeps_the_takes_and_writes_nothing_until_used(tmp_path):
    out = str(tmp_path / "takes-recorded.json")
    old = {"mode": "obs", "takes": [{"line": "a", "file": "/x/take1.mkv", "seconds": 3, "bad": False}], "marks": []}
    with open(out, "w") as f:
        json.dump(old, f)
    pr = tp.Prompter(lines(), out, FakeOBSClient())
    assert json.load(open(out)) == old                      # starting up (e.g. a port that fails to bind) changes nothing
    assert [t["file"] for t in pr.data["takes"]] == ["/x/take1.mkv"]
    pr.act("next")
    assert json.load(open(out))["takes"][0]["file"] == "/x/take1.mkv"


def test_marks_only_run_does_not_load_or_replace_obs_takes(tmp_path):
    out = str(tmp_path / "takes-recorded.json")
    with open(out, "w") as f:
        json.dump({"mode": "obs", "takes": [{"line": "a", "file": "/x/take1.mkv", "bad": False}], "marks": []}, f)
    assert tp.Prompter(lines(), out).data["takes"] == []   # no OBS: it doesn't resume the OBS ledger


def test_marks_only_main_writes_marks_json_not_the_take_ledger(tmp_path, monkeypatch):
    d = str(tmp_path / "p")
    proj.new(d, "script-then-record")
    script(d, lines())
    ledger = os.path.join(d, "proposals", "takes-recorded.json")
    with open(ledger, "w") as f:
        f.write('{"mode": "obs", "takes": [{"line": "a", "file": "/x", "bad": false}]}')

    class Quick(tp.Prompter):
        def __init__(self, *a):
            super().__init__(*a)
            self.act("state")
            self.done = True

    monkeypatch.setattr(tp, "Prompter", Quick)
    monkeypatch.setattr(sys, "argv", ["teleprompter.py", "-P", d, "--port", "0", "--no-open"])
    with pytest.raises(SystemExit) as e:
        tp.main()
    assert e.value.code == 0
    assert os.path.isfile(os.path.join(d, "proposals", "marks.json"))
    assert json.load(open(ledger))["takes"][0]["file"] == "/x"


# --- teleprompter: OBS state ---------------------------------------------------------------------
def test_main_refuses_when_obs_is_already_recording(tmp_path, monkeypatch):
    d = str(tmp_path / "p")
    proj.new(d, "script-then-record")
    script(d, lines())
    busy = FakeOBSClient()
    busy.active = True
    monkeypatch.setattr(tp, "OBS", lambda url, pw: busy)
    monkeypatch.setattr(sys, "argv", ["teleprompter.py", "-P", d, "--obs", "--no-open"])
    with pytest.raises(SystemExit) as e:
        tp.main()
    assert "already recording" in str(e.value)
    assert busy.sent == ["GetRecordStatus"]      # and it did not stop (or start) anything


def test_retake_waits_until_obs_really_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(tp.time, "sleep", lambda s: None)
    obs = FakeOBSClient(active_polls=2)
    pr = tp.Prompter(lines(), str(tmp_path / "t.json"), obs)
    pr.act("toggle")
    st = pr.act("retake")
    assert st["error"] == "" and st["recording"]      # StartRecord was accepted
    assert [t["bad"] for t in pr.data["takes"]] == [True]


def test_retake_reports_when_obs_never_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(tp.time, "sleep", lambda s: None)
    monkeypatch.setattr(tp, "IDLE_WAIT", -1)
    obs = FakeOBSClient(active_polls=10 ** 6)
    pr = tp.Prompter(lines(), str(tmp_path / "t.json"), obs)
    pr.act("toggle")
    st = pr.act("retake")
    assert "still recording" in st["error"] and not st["recording"]


def test_lost_connection_is_an_error_not_a_crash_and_keeps_the_take(tmp_path):
    class DeadSocket:
        def send(self, m):
            pass

        def recv(self):
            raise ConnectionResetError("reset")

    c = object.__new__(tp.OBS)
    c.ws, c.lock = DeadSocket(), threading.Lock()
    with pytest.raises(tp.OBSError, match="unknown"):
        c.request("StopRecord")

    obs = FakeOBSClient()
    pr = tp.Prompter(lines(), str(tmp_path / "t.json"), obs)
    pr.act("toggle")
    obs.fail_stop = True                      # OBS did stop, but the reply got lost
    st = pr.act("toggle")
    assert "file is unknown" in st["error"] and not st["recording"]
    assert pr.data["takes"][-1]["file"] is None and pr.data["takes"][-1]["lost"]


# --- teleprompter: page and server ---------------------------------------------------------------
def test_page_sends_requests_one_at_a_time():
    assert "queue=queue.then" in tp.PAGE


def test_cross_site_post_is_refused_but_own_page_and_scripts_work(tmp_path):
    pr = tp.Prompter(lines(), str(tmp_path / "t.json"))
    srv = tp.make_server(pr, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def post(origin, token=True, host=None):
        h = {"Origin": origin} if origin else {}
        if token:
            h["X-Teleprompter-Token"] = pr.token
        if host:
            h["Host"] = host
        return json.load(urllib.request.urlopen(urllib.request.Request(base + "/api/next", b"", headers=h, method="POST")))
    try:
        for bad in (dict(origin="https://evil.example"), dict(origin=None, token=False),
                    dict(origin=None, host="evil.example:80")):   # other site, no token, rebinding
            with pytest.raises(urllib.error.HTTPError) as e:
                post(**bad)
            assert e.value.code == 403 and pr.i == 0       # nothing happened
        assert post(base)["index"] == 1                 # the page itself
        assert post(None)["index"] == 1                 # a script with the token (no Origin: not a browser form)
        assert "__TOKEN__" not in urllib.request.urlopen(base + "/").read().decode()
    finally:
        srv.shutdown()


# --- align.py --from-takes -----------------------------------------------------------------------
def takes_project(tmp_path, files):
    d = str(tmp_path / "p")
    proj.new(d, "script-then-record")
    script(d, [{"id": "a", "text": "Hoy vamos a ver punteros."}])
    with open(os.path.join(d, "proposals", "takes-recorded.json"), "w") as f:
        json.dump({"mode": "obs", "takes": [{"line": "a", "file": p, "bad": False} for p in files]}, f)
    return d


class PerFileTranscribe(FakeTranscribe):
    unreadable = ()

    def words(self, media, lang=None, prompt=None, track=0):
        if os.path.basename(media) in type(self).unreadable:
            raise backends.BackendError("can't decode " + media)
        return even_words("Hoy vamos a ver punteros.", start=0.4)


def run_align(monkeypatch, d, *extra):
    monkeypatch.setitem(backends.CONFIG, "TRANSCRIBE", "f_perfile")
    monkeypatch.setattr(sys, "argv", ["align.py", "-P", d, "--from-takes", *extra])
    backends.register("transcribe", "f_perfile", PerFileTranscribe)
    try:
        align.main()
    finally:
        backends.unregister("transcribe", "f_perfile")


def test_an_unreadable_newest_take_falls_back_to_an_older_one(tmp_path, monkeypatch):
    old, new = tone(str(tmp_path / "old.wav"), 3), tone(str(tmp_path / "new.wav"), 3)
    d = takes_project(tmp_path, [old, new])
    PerFileTranscribe.unreadable = ("new.wav",)
    run_align(monkeypatch, d, "--apply")
    pj = proj.load(d)
    assert pj["media"]["take:a"]["file"] == old and pj["timeline"]["video"][0]["line"] == "a"
    assert "new.wav" in json.load(open(os.path.join(d, "proposals", "takes.json")))["errors"][0]


def test_when_no_take_can_be_transcribed_it_says_so(tmp_path, monkeypatch):
    f = tone(str(tmp_path / "only.wav"), 3)
    d = takes_project(tmp_path, [f])
    PerFileTranscribe.unreadable = ("only.wav",)
    with pytest.raises(SystemExit) as e:
        run_align(monkeypatch, d)
    assert "couldn't transcribe" in str(e.value)


def test_a_proposal_run_does_not_rebind_media_the_timeline_uses(tmp_path, monkeypatch):
    old, new = tone(str(tmp_path / "old.wav"), 3), tone(str(tmp_path / "new.wav"), 3)
    d = takes_project(tmp_path, [new])
    pj = proj.load(d)
    pj["media"]["take:a"] = {"file": old, "kind": "video", "track": 0}
    pj["timeline"]["video"] = [{"src": "take:a", "in": 0.25, "out": 2.0, "line": "a"}]
    proj.save(d, pj)
    PerFileTranscribe.unreadable = ()
    run_align(monkeypatch, d)                                  # proposal only
    pj = proj.load(d)
    assert pj["media"]["take:a"]["file"] == old                # in/out still describe this file
    assert json.load(open(os.path.join(d, "proposals", "takes.json")))["lines"][0]["file"] == new
    run_align(monkeypatch, d, "--apply")
    assert proj.load(d)["media"]["take:a"]["file"] == new      # rebound together with the new trims


# --- tts.py --------------------------------------------------------------------------------------
def test_timed_narration_is_not_given_a_scene_as_visual(tmp_path, media, fakes, monkeypatch, capsys):  # noqa: F811
    d = str(tmp_path / "vo")
    proj.new(d, "voice-over")
    script(d, [{"id": "a", "text": "uno dos", "at": 0.5}, {"id": "b", "text": "tres", "at": 3.0}])
    pj = proj.load(d)
    pj["media"]["scene:a"] = {"file": str(media / "rec.mp4"), "kind": "video"}
    pj["timeline"]["video"] = [{"src": "scene:a", "in": 0, "out": 5}]
    proj.save(d, pj)
    code, s = run_tts(monkeypatch, capsys, "-P", d, "--backend", "f_timed", "--place")
    assert code == 0 and s["placed"] == 2
    assert [x["at"] for x in proj.load(d)["timeline"]["audio"]] == [0.5, 3.0]
    assert proj.load(d)["timeline"]["video"] == [{"src": "scene:a", "in": 0, "out": 5}]   # the video is left alone
