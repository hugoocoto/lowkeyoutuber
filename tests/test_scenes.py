import json
import os
import subprocess
import sys

import pytest

import backends
import project as proj
import scenes
import tts
from conftest import ROOT, probe
from fakes import FakeRender, TimedTTS


def test_highlighter_and_code_lines():
    toks = scenes.tokens('int main(void) { return 0; } // fin', "c")
    kinds = {t: k for k, t in toks if k}
    assert kinds["int"] == "k" and kinds["return"] == "k" and kinds["0"] == "n" and kinds["// fin"] == "c"
    html, n = scenes.code_html("int x;\nx = 1;", "c", typing=True)
    assert html.count('class="line"') == 2 and n == len("int x;") + len("x = 1;")


def test_clock_resolves_words_in_order():
    words = [{"w": "La", "s": 0.1, "e": 0.2}, {"w": "coma,", "s": 0.5, "e": 0.8}, {"w": "otra", "s": 1.0, "e": 1.2},
             {"w": "coma.", "s": 1.5, "e": 1.8}]
    c = scenes.Clock(words, offset=0.3, dur=5)
    assert c.at("coma", 0) == pytest.approx(0.8)      # 0.5 + offset
    assert c.at("coma", 0) == pytest.approx(1.8)      # the second one
    assert c.at("nunca", 2.0) == 2.0 and c.at(1.25, 0) == 1.25


def test_c_code_runs_for_real(tmp_path):
    out = scenes.run_c('#include <stdio.h>\nint main(void){printf("x = %d\\n", (1, 2, 3));}', "program", True, str(tmp_path))
    assert out == "x = 3"
    assert scenes.run_c("int x = (1, 2, 3);", "fragment", False, str(tmp_path)) is None
    with pytest.raises(scenes.SceneError, match="doesn't compile"):
        scenes.run_c("int main(void) { return y; }", "program", False, str(tmp_path))


def test_terminal_runs_for_real_without_secrets(tmp_path, monkeypatch):
    monkeypatch.setitem(scenes.SAFE_ENV, "HOME", os.environ.get("HOME", "/tmp"))
    assert scenes.run_shell("echo hola; echo $ELEVENLABS_API_KEY", str(tmp_path)) == "hola"
    assert not any("KEY" in k for k in scenes.SAFE_ENV)


def test_every_template_builds(tmp_path):
    img = tmp_path / "shot.png"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=64x64", "-frames:v", "1", str(img)], check=True)
    cases = [
        {"type": "title", "title": "Hola", "subtitle": "mundo"},
        {"type": "text", "items": ["uno", "dos"], "title": "Lista"},
        {"type": "code", "code": "int x = (1, 2, 3);", "highlight": [{"lines": [1], "at": 1.0}]},
        {"type": "terminal", "commands": [{"cmd": "echo hola"}]},
        {"type": "diagram", "boxes": [{"id": "a", "x": 10, "y": 30}, {"id": "b", "x": 60, "y": 30}],
         "arrows": [{"from": "a", "to": "b", "label": "apunta"}]},
        {"type": "image", "src": str(img), "caption": "una foto"},
    ]
    for sc in cases:
        d = tmp_path / sc["type"]
        d.mkdir()
        meta = {}
        html = scenes.build(sc, {"text": "x"}, 1080, 1920, 4.0, scenes.Clock([], 0, 4.0), str(d), str(tmp_path), meta,
                            str(tmp_path), "es")
        assert 'data-composition-id="main"' in html and 'src="gsap.min.js"' in html and "window.__timelines" in html
        assert "http" not in html.split("<style>")[0].replace('http-equiv', '')   # nothing from the network
    with pytest.raises(scenes.SceneError):
        scenes.build({"type": "nope"}, {}, 1080, 1920, 4, scenes.Clock([], 0, 4), str(tmp_path), "", {}, "", "es")


def test_diagram_arrows_start_and_end_at_box_edges():
    x, y = scenes.edge_point((0, 0, 100, 50), 300, 25)
    assert (x, y) == (100, 25)


@pytest.fixture
def fake_render(monkeypatch):
    backends.register("render", "f_render", FakeRender)
    backends.register("tts", "f_timed", TimedTTS)
    monkeypatch.setitem(backends.CONFIG, "RENDER", "f_render")
    FakeRender.renders = 0
    yield
    backends.unregister("render", "f_render")
    backends.unregister("tts", "f_timed")


def run_main(mod, monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, "argv", [mod.__name__, *argv, "--json"])
    code = 0
    try:
        mod.main()
    except SystemExit as e:
        code = e.code
    out = capsys.readouterr().out.strip().splitlines()
    return code, json.loads(out[-1]) if out else None


def test_generated_short_with_scenes(tmp_path, fake_render, monkeypatch, capsys):
    d = str(tmp_path / "p")
    proj.new(d, "generated-short", formats=("9:16", "16:9"))
    with open(os.path.join(d, "script.json"), "w") as f:
        json.dump({"lang": "es", "lines": [
            {"id": "01", "text": "Hola a todos", "scene": {"type": "title", "title": "Hola"}},
            {"id": "02", "text": "Esto compila", "scene": {"type": "code", "code": "int x = (1, 2, 3);"}}]}, f)
    assert run_main(tts, monkeypatch, capsys, "-P", d, "--backend", "f_timed")[0] == 0
    code, s = run_main(scenes, monkeypatch, capsys, "-P", d)
    assert code == 0 and len(s["rendered"]) == 4 and FakeRender.renders == 4
    m = proj.load(d)["media"]["scene:01"]
    assert set(m["variants"]) == {"9:16", "16:9"} and m["variants"]["16:9"].endswith("01_16x9.mp4")
    # scene length = narration + gaps, as tts --place lays it out
    lead, spans = tts.line_spans([{"id": "01"}, {"id": "02"}], [tts.probe_duration(os.path.join(d, "narration", f"{i}.wav"))
                                                                for i in ("01", "02")])
    assert abs(float(probe(os.path.join(d, "scenes", "01.mp4"))["format"]["duration"]) - spans[0]) < 0.1
    # unchanged: nothing rendered again
    code, s = run_main(scenes, monkeypatch, capsys, "-P", d)
    assert s["unchanged"] and not s["rendered"]
    # a hand edit is kept and rendered
    idx = os.path.join(d, "scenes", "01", "index.html")
    with open(idx, "a") as f:
        f.write("<!-- edited -->")
    code, s = run_main(scenes, monkeypatch, capsys, "-P", d, "--lines", "01")
    assert s["edited_by_hand"] == ["01 9:16"] and "edited" in open(idx).read()
    # --place uses the scenes as pictures; render.py uses each format's variant
    assert run_main(tts, monkeypatch, capsys, "-P", d, "--backend", "f_timed", "--place")[0] == 0
    p = proj.load(d)
    assert [v["src"] for v in p["timeline"]["video"]] == ["scene:01", "scene:02"]
    r = subprocess.run([sys.executable, os.path.join(ROOT, "render.py"), "-P", d], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for f, wh in (("video.mp4", (1080, 1920)), ("video_16x9.mp4", (1920, 1080))):
        v = next(x for x in probe(os.path.join(d, "out", f))["streams"] if x["codec_type"] == "video")
        assert (v["width"], v["height"]) == wh


def test_compile_error_fails_the_scene(tmp_path, fake_render, monkeypatch, capsys):
    d = str(tmp_path / "p")
    proj.new(d, "generated-short")
    with open(os.path.join(d, "script.json"), "w") as f:
        json.dump({"lines": [{"id": "01", "text": "x", "scene": {"type": "code", "code": "int main(void){return y;}"}}]}, f)
    code, s = run_main(scenes, monkeypatch, capsys, "-P", d)
    assert code == 1 and "compile" in s["failed"][0]["error"]


HF = os.path.expanduser("~/.cache/lowkeyoutuber/hyperframes-0.9.104/node_modules/.bin/hyperframes")


@pytest.mark.skipif(not (os.environ.get("LKY_SLOW_TESTS") and os.path.isfile(HF)),
                    reason="real HyperFrames render: set LKY_SLOW_TESTS=1 (takes about a minute)")
def test_real_hyperframes_render(tmp_path):
    from backends.render.hyperframes import Backend
    d = tmp_path / "s"
    d.mkdir()
    (d / "index.html").write_text(scenes.build({"type": "title", "title": "Hola"}, {}, 640, 360, 1.0,
                                               scenes.Clock([], 0, 1.0), str(d), str(tmp_path), {}, str(tmp_path), "es"))
    b = Backend()
    assert b.lint(str(d)) == []
    out = b.render_scene(str(d), str(tmp_path / "s.mp4"), 30, 640, 360, draft=True)
    assert abs(float(probe(out)["format"]["duration"]) - 1.0) < 0.1
