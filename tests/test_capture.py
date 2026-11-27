import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import threading

import pytest

import capture
import project as proj
from conftest import ROOT, probe

APP = """<!doctype html><html><body style="font-family:sans-serif;padding:40px">
<h1>Tareas</h1><input id="new" placeholder="Nueva"><button id="add">Añadir</button>
<button id="dark" style="position:absolute;right:40px;top:40px">Modo oscuro</button><ul id="list"></ul>
<script>add.onclick=()=>{const li=document.createElement('li');li.textContent=document.getElementById('new').value;list.appendChild(li)};
dark.onclick=()=>{document.body.style.background='#111'}</script></body></html>"""


def test_zooms_merge_close_actions_and_fit_the_frame():
    ev = [{"t": 1.0, "type": "type", "line": "a", "rect": [0.1, 0.2, 0.3, 0.05]},
          {"t": 2.0, "type": "click", "line": "a", "rect": [0.42, 0.2, 0.08, 0.05]},
          {"t": 6.0, "type": "click", "line": "b", "rect": [0.85, 0.05, 0.1, 0.05]}]
    z = capture.propose_zooms(ev, 9.0)
    assert len(z) == 2 and "type (a), click (a)" == z[0]["why"]
    for zm in z:
        x, y, w, h = zm["rect"]
        assert 0 <= x and 0 <= y and x + w <= 1.0001 and y + h <= 1.0001 and 0.4 <= w <= 0.9
        assert zm["at"] + zm["dur"] <= 9.0


def test_refuses_to_type_secrets():
    with pytest.raises(capture.CaptureError):
        capture.check_text("export KEY=sk-abcdefghijklmnopqrstuvwxyz")
    capture.check_text("hola mundo")


@pytest.fixture
def app_server(tmp_path):
    (tmp_path / "index.html").write_text(APP)
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(tmp_path), **k)   # noqa: E731
    handler.log_message = lambda *a: None
    srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def have_playwright():
    try:
        import playwright.sync_api  # noqa: F401
        return bool(capture.chrome_path())
    except ImportError:
        return False


def make(tmp_path, lines):
    d = str(tmp_path / "p")
    proj.new(d, "app-demo")
    with open(os.path.join(d, "script.json"), "w") as f:
        json.dump({"lang": "es", "lines": lines}, f)
    return d


def run(*args):
    r = subprocess.run([sys.executable, os.path.join(ROOT, "capture.py"), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r


@pytest.mark.skipif(not have_playwright(), reason="needs Playwright and Chrome/Chromium")
def test_web_capture(tmp_path, app_server):
    d = make(tmp_path, [{"id": "01", "text": "La app.", "actions": [{"wait": 0.3}]},
                        {"id": "02", "text": "Añado una tarea.", "actions": [{"type": ["#new", "Grabar"]}, {"click": "#add"}]},
                        {"id": "03", "text": "Modo oscuro.", "actions": [{"click": "text=Modo oscuro"}]}])
    run("-P", d, "--kind", "web", "--url", app_server, "--size", "1280x720")
    p = proj.load(d)
    v = next(x for x in probe(os.path.join(d, "media", "capture.mp4"))["streams"] if x["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1280, 720)
    ev = json.load(open(os.path.join(d, "media", "capture.events.json")))
    assert [e["type"] for e in ev["events"]] == ["wait", "type", "click", "click"]
    assert all(e["rect"] for e in ev["events"][1:])
    lines = json.load(open(os.path.join(d, "script.json")))["lines"]
    assert lines[0]["at"] < lines[1]["at"] < lines[2]["at"]
    assert p["timeline"]["video"][0]["src"] == "cap" and p["state"]["zooms"] == "proposed"
    assert json.load(open(os.path.join(d, "proposals", "zooms.json")))
    run("-P", d, "--apply")
    assert proj.load(d)["timeline"]["video"][0]["zooms"]


@pytest.mark.skipif(not all(shutil.which(t) for t in ("Xvfb", "xdotool", "xterm")), reason="needs Xvfb, xdotool, xterm")
def test_terminal_capture_runs_commands_for_real(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    d = make(tmp_path, [{"id": "01", "text": "Creo un fichero.", "actions": [{"run": "echo hola > f.txt"}]}])
    run("-P", d, "--kind", "terminal", "--cwd", str(work), "--size", "1280x720")
    assert (work / "f.txt").read_text().strip() == "hola"
    assert float(probe(os.path.join(d, "media", "capture.mp4"))["format"]["duration"]) > 1
