import json
import os
import subprocess
import sys

import backends
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


def test_skill_is_in_sync():
    path = os.path.join(ROOT, "skills", lky.SKILL_NAME, "SKILL.md")   # the plugin's skill
    assert os.path.isfile(path), "run ./lky skills"
    assert open(path, encoding="utf-8").read() == lky.skill_text(lky.flows()), "run ./lky skills"


def test_no_stale_generated_skills():
    d = os.path.join(ROOT, ".claude", "skills")   # where older versions wrote them
    for name in [f["name"] for f in lky.flows()] + [lky.SKILL_NAME]:
        assert not os.path.isfile(os.path.join(d, name, "SKILL.md")), "run ./lky skills (it removes the old ones)"


def test_old_skills_are_removed_but_hand_written_files_stay(tmp_path, monkeypatch):
    old = tmp_path / ".claude" / "skills"
    (old / "voice-over").mkdir(parents=True)
    (old / "voice-over" / "SKILL.md").write_text("lowkeyoutuber flow: x. Follow it in this repository exactly, "
                                                "step by step")
    (old / "voice-over" / "notes.md").write_text("mine")
    (old / lky.SKILL_NAME).mkdir()
    (old / lky.SKILL_NAME / "SKILL.md").write_text(lky.SKILL_MARK)
    (old / "my-skill").mkdir()
    (old / "my-skill" / "SKILL.md").write_text("written by hand")
    monkeypatch.setattr(lky, "OLD_SKILLS_DIR", str(old))
    lky.remove_old_skills()
    assert not (old / "voice-over" / "SKILL.md").exists() and (old / "voice-over" / "notes.md").exists()
    assert not (old / lky.SKILL_NAME).exists()
    assert (old / "my-skill" / "SKILL.md").read_text() == "written by hand"


def test_launcher_runs_tools_and_finds_the_plugin_venv(tmp_path):
    env = dict(os.environ, PYTHON=sys.executable)   # no venv is created with $PYTHON set
    r = subprocess.run([os.path.join(ROOT, "bin", "lky"), "run", "lky.py", "flows", "--json"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)
    # installed as a plugin: cache/<marketplace>/<plugin>/<version>/ -> data/<plugin>-<marketplace>/venv
    ver = tmp_path / "plugins" / "cache" / "mkt" / "lowkeyoutuber" / "1.0"
    ver.mkdir(parents=True)
    (tmp_path / "plugins" / "data").mkdir()
    import shutil
    shutil.copy2(os.path.join(ROOT, "lky"), ver / "lky")
    env = {k: v for k, v in os.environ.items() if k not in ("LKY_VENV", "PYTHON")}
    venv = tmp_path / "plugins" / "data" / "lowkeyoutuber-mkt" / "venv"
    venv.mkdir(parents=True)
    (venv / "bin").mkdir()
    (venv / "bin" / "python").symlink_to(sys.executable)   # an existing venv: nothing is created
    r = subprocess.run([str(ver / "lky"), "venv"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and r.stdout.strip() == str(venv)
    r = subprocess.run([str(ver / "lky"), "venv"], capture_output=True, text=True,
                       env=dict(env, LKY_VENV=str(tmp_path / "mine")))
    assert r.stdout.strip() == str(tmp_path / "mine")   # $LKY_VENV wins (created there)


def test_plugin_manifests_point_at_the_skill():
    plugin = json.load(open(os.path.join(ROOT, ".claude-plugin", "plugin.json")))
    market = json.load(open(os.path.join(ROOT, ".claude-plugin", "marketplace.json")))
    assert plugin["name"] == lky.SKILL_NAME == market["plugins"][0]["name"]
    assert market["plugins"][0]["source"] == "./"
    assert "${CLAUDE_PLUGIN_ROOT}/AGENTS.md" in lky.skill_text(lky.flows())
    assert os.access(os.path.join(ROOT, "bin", "lky"), os.X_OK)


def test_skill_frontmatter_is_valid_yaml():
    import pytest
    yaml = pytest.importorskip("yaml")
    meta = yaml.safe_load(lky.skill_text(lky.flows()).split("---")[1])
    assert meta["name"] == lky.SKILL_NAME and "video" in meta["description"]


def test_new_without_a_flow(tmp_path):
    d = str(tmp_path / "free")
    run("new", d, "--script")
    pj = json.load(open(os.path.join(d, "project.json")))
    assert "flow" not in pj and os.path.isfile(os.path.join(d, "script.json"))
    st = json.loads(run("status", "-P", d, "--json").stdout)
    assert st["flow"] is None and st["problems"] == []
    d2 = str(tmp_path / "noted")
    run("new", d2, "--flow", "voice-over")
    assert json.load(open(os.path.join(d2, "project.json")))["flow"] == "voice-over"
    assert run("new", str(tmp_path / "x"), "--flow", "no-such-flow", ok=False).returncode != 0


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
    assert set(data["chains"]) == set(backends.KINDS)
    assert any(b["name"] == "elevenlabs" for b in data["backends"])


def test_new_old_form_accepts_options_between_flow_and_dir(tmp_path):
    d = tmp_path / "demo"
    r = run("new", "voice-over", "--lang", "en", str(d))
    assert (d / "project.json").is_file(), r.stdout
    assert run("new", "voice-over", "--bogus", str(tmp_path / "z"), ok=False).returncode != 0
