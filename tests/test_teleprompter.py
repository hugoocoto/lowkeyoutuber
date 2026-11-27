import asyncio
import base64
import hashlib
import json
import os
import sys
import threading
import urllib.request

import pytest

import backends
import project as proj
import teleprompter as tp
from conftest import ff
from fakes import FakeTranscribe, even_words

websockets = pytest.importorskip("websockets")
pytest.importorskip("websocket")


class FakeOBS:
    """obs-websocket v5 with a password; StartRecord/StopRecord make one file per take."""

    def __init__(self, tmp, password="secret"):
        self.tmp, self.password, self.n, self.recording = tmp, password, 0, False
        self.loop = asyncio.new_event_loop()
        started = threading.Event()

        async def handler(ws):
            salt, challenge = "c2FsdA==", "Y2hhbA=="
            await ws.send(json.dumps({"op": 0, "d": {"rpcVersion": 1, "authentication": {"salt": salt, "challenge": challenge}}}))
            ident = json.loads(await ws.recv())
            secret = base64.b64encode(hashlib.sha256((self.password + salt).encode()).digest()).decode()
            want = base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()
            if ident["d"].get("authentication") != want:
                await ws.close(4009, "auth failed")
                return
            await ws.send(json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}))
            async for raw in ws:
                d = json.loads(raw)["d"]
                kind, data = d["requestType"], {}
                ok = True
                if kind == "StartRecord":
                    ok, self.recording = not self.recording, True
                elif kind == "StopRecord":
                    ok, self.recording = self.recording, False
                    self.n += 1
                    f = os.path.join(self.tmp, f"take{self.n}.wav")
                    ff("-f", "lavfi", "-i", "sine=d=1", f)
                    data = {"outputPath": f}
                await ws.send(json.dumps({"op": 7, "d": {"requestType": kind, "requestId": d["requestId"],
                                                         "requestStatus": {"result": ok, "code": 100 if ok else 501},
                                                         "responseData": data}}))

        async def serve():
            self.server = await websockets.serve(handler, "127.0.0.1", 0)
            self.port = self.server.sockets[0].getsockname()[1]
            started.set()
            await asyncio.Future()

        threading.Thread(target=lambda: self.loop.run_until_complete(serve()), daemon=True).start()
        started.wait(5)
        self.url = f"ws://127.0.0.1:{self.port}"


def test_obs_auth(tmp_path):
    obs = FakeOBS(str(tmp_path))
    with pytest.raises(tp.OBSError):
        tp.OBS(obs.url, None)
    with pytest.raises(tp.OBSError):
        tp.OBS(obs.url, "wrong")
    c = tp.OBS(obs.url, "secret")
    assert c.request("StartRecord") == {}
    with pytest.raises(tp.OBSError):
        c.request("StartRecord")   # already recording
    assert c.request("StopRecord")["outputPath"].endswith("take1.wav")


def lines():
    return [{"id": "a", "text": "Hoy vamos a ver punteros."}, {"id": "b", "text": "Un puntero guarda una dirección."}]


def test_takes_with_obs_then_align(tmp_path, monkeypatch, capsys):
    obs = FakeOBS(str(tmp_path))
    d = str(tmp_path / "p")
    proj.new(d, "script-then-record")
    with open(os.path.join(d, "script.json"), "w") as f:
        json.dump({"lang": "es", "lines": lines()}, f)
    out = os.path.join(d, "proposals", "takes-recorded.json")
    pr = tp.Prompter(lines(), out, tp.OBS(obs.url, "secret"))
    for a in ("toggle", "toggle", "toggle", "toggle", "bad", "retake", "toggle", "next", "toggle", "finish"):
        st = pr.act(a)
    # R while recording: the running take is marked bad and a new one starts
    pr2 = tp.Prompter(lines(), out + ".2", tp.OBS(obs.url, "secret"))
    for a in ("toggle", "retake", "toggle", "finish"):
        pr2.act(a)
    assert [t["bad"] for t in pr2.data["takes"]] == [True, False]
    assert st["done"]
    takes = json.load(open(out))["takes"]
    # a: take1 ok, take2 marked bad (B), R with nothing recording starts take3, Space stops it; b: take4
    assert [(t["line"], t["bad"]) for t in takes] == [("a", False), ("a", True), ("a", False), ("b", False)]
    files = {t["file"]: t for t in takes}
    heard = {}
    for f, t in files.items():
        good = not t["bad"]
        text = lines()[0]["text"] if t["line"] == "a" else lines()[1]["text"]
        heard[f] = even_words(text if good else "esto no vale", start=0.4)

    class PerFile(FakeTranscribe):
        def words(self, media, lang=None, prompt=None, track=0):
            return heard[media]

    backends.register("transcribe", "f_perfile", PerFile)
    monkeypatch.setitem(backends.CONFIG, "TRANSCRIBE", "f_perfile")
    import align
    monkeypatch.setattr(sys, "argv", ["align.py", "-P", d, "--from-takes", "--apply", "--json"])
    try:
        align.main()
    finally:
        backends.unregister("transcribe", "f_perfile")
    rep = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rep["missing"] == []
    p = proj.load(d)
    v = p["timeline"]["video"]
    assert [x["line"] for x in v] == ["a", "b"]
    assert p["media"]["take:a"]["file"].endswith("take3.wav")   # the last good take of a
    assert v[0]["in"] == pytest.approx(0.25) and v[0]["out"] > v[0]["in"]


def test_http_page_and_api(tmp_path):
    out = str(tmp_path / "t.json")
    pr = tp.Prompter(lines(), out)
    srv = tp.make_server(pr, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert "Teleprompter" in urllib.request.urlopen(base + "/").read().decode()
        st = json.load(urllib.request.urlopen(urllib.request.Request(base + "/api/next", b"", method="POST")))
        assert st["index"] == 1 and st["current"].startswith("Un puntero")
        st = json.load(urllib.request.urlopen(urllib.request.Request(base + "/api/toggle", b"", method="POST")))
        assert "No OBS" in st["error"]   # without OBS, Space only explains
        assert [m["line"] for m in json.load(open(out))["marks"]] == ["a", "b"]
    finally:
        srv.shutdown()


def test_page_keys_in_a_real_browser(tmp_path):
    pw = pytest.importorskip("playwright.sync_api")
    import capture
    exe = capture.chrome_path()
    if not exe:
        pytest.skip("no Chrome/Chromium")
    pr = tp.Prompter(lines(), str(tmp_path / "t.json"))
    srv = tp.make_server(pr, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pw.sync_playwright() as p:
            b = p.chromium.launch(executable_path=exe)
            page = b.new_page()
            page.goto(f"http://127.0.0.1:{srv.server_address[1]}/")
            page.wait_for_selector("#cur:has-text('Hoy vamos')")
            page.keyboard.press("ArrowRight")
            page.wait_for_selector("#cur:has-text('Un puntero')")
            page.keyboard.press("q")
            page.wait_for_selector("#cur:has-text('Done')")
            b.close()
        assert pr.done
    finally:
        srv.shutdown()
