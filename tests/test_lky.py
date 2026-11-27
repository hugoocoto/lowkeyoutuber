import json
import os
import subprocess
import sys

import lky
from conftest import ROOT

LKY = [sys.executable, os.path.join(ROOT, "lky.py")]


def run(*args, ok=True):
    r = subprocess.run(LKY + list(args), capture_output=True, text=True)
    if ok:
        assert r.returncode == 0, r.stderr
    return r


def test_flows_parse_and_have_the_sections():
    fl = lky.flows()
    assert {f["name"] for f in fl} >= {"edit-recording", "long-to-shorts", "generated-short", "voice-over",
                                        "script-then-record", "app-demo"}
    for f in fl:
        assert f["status"] in ("ready", "partial", "planned"), f["name"]
        assert f["inputs"], f["name"]
        assert "## Steps" in f["body"], f["name"]
        if f["status"] != "ready":
            assert "## Not built yet" in f["body"], f"{f['name']} must say what's missing"


def test_skills_are_in_sync_with_flows():
    for f in lky.flows():
        path = os.path.join(ROOT, ".claude", "skills", f["name"], "SKILL.md")
        assert os.path.isfile(path), f"run ./lky skills ({f['name']} has no skill)"
        assert open(path, encoding="utf-8").read() == lky.skill_text(f), f"run ./lky skills ({f['name']} changed)"


def test_project_commands(tmp_path, media):
    d = str(tmp_path / "p")
    run("new", "voice-over", d, "--format", "16:9", "--input", "topic=demo")
    assert os.path.isfile(os.path.join(d, "script.json"))
    added = json.loads(run("add-media", "-P", d, str(media / "rec.mp4"), "--id", "src", "--json").stdout)
    assert added["kind"] == "video" and added["file"].endswith("rec.mp4")
    assert run("add-media", "-P", d, str(media / "rec.mp4"), "--id", "src", ok=False).returncode != 0
    st = json.loads(run("status", "-P", d, "--json").stdout)
    assert st["flow"] == "voice-over" and st["media"] == ["src"] and st["problems"] == []
    assert run("new", "no-such-flow", str(tmp_path / "q"), ok=False).returncode != 0


def test_check_passes_on_a_good_render_and_fails_on_a_bad_one(tmp_path, media):
    from test_render import make_project, render
    d = make_project(tmp_path, media, formats=("16:9",))
    render(d)
    rep = json.loads(run("check", "-P", d, "--json").stdout)
    assert rep["ok"] and abs(rep["outputs"]["16:9"]["lufs"] + 14) < 1
    with open(os.path.join(d, "out", "chapters.txt"), "w") as f:
        f.write("0:00 Intro\n0:03 Too short\n")
    r = run("check", "-P", d, "--json", ok=False)
    assert r.returncode == 1 and any("chapters" in e for e in json.loads(r.stdout)["errors"])


def test_backends_listing():
    data = json.loads(run("backends", "--json").stdout)
    assert set(data["chains"]) == {"llm", "transcribe", "tts", "music"}
    assert any(b["name"] == "elevenlabs" for b in data["backends"])
