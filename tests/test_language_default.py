"""The language comes from the person: argument, then config LANGUAGE, then env LKY_LANG, else unknown."""
import importlib
import json
import os
import subprocess
import sys

import pytest

import backends
import yt_common

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_default_language_order(monkeypatch):
    monkeypatch.setattr(yt_common, "CONFIG", {})
    monkeypatch.delenv("LKY_LANG", raising=False)
    assert yt_common.default_language() is None
    monkeypatch.setenv("LKY_LANG", "fr")
    assert yt_common.default_language() == "fr"
    monkeypatch.setattr(yt_common, "CONFIG", {"LANGUAGE": "es"})
    assert yt_common.default_language() == "es"   # config wins over env


def run_new(tmp_path, *args, lang_env=None, as_json=True):
    env = {k: v for k, v in os.environ.items() if k != "LKY_LANG"}
    env["XDG_CONFIG_HOME"] = str(tmp_path / "cfg")
    if lang_env:
        env["LKY_LANG"] = lang_env
    d = tmp_path / "p"
    cmd = [sys.executable, os.path.join(ROOT, "lky.py"), "new", str(d), "--script", *args]
    r = subprocess.run(cmd + (["--json"] if as_json else []), capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return d, (json.loads(r.stdout) if as_json else r.stdout)


def test_lky_new_without_language_stores_none(tmp_path):
    d, out = run_new(tmp_path)
    assert out["lang"] is None
    assert json.load(open(d / "project.json"))["lang"] is None
    assert json.load(open(d / "script.json"))["lang"] is None


def test_lky_new_says_language_is_unset(tmp_path):
    _, text = run_new(tmp_path, as_json=False)
    assert "Language is NOT set" in text


def test_lky_new_uses_env_and_argument(tmp_path):
    assert run_new(tmp_path, lang_env="de")[1]["lang"] == "de"
    d2 = tmp_path / "x"
    d2.mkdir()
    assert run_new(d2, "--lang", "en", lang_env="de")[1]["lang"] == "en"


@pytest.mark.parametrize("name", ["chatterbox", "qwen3", "kokoro"])
def test_tts_backends_refuse_unknown_language(name, monkeypatch):
    mod = importlib.import_module(f"backends.tts.{name}")
    monkeypatch.setattr(mod, "default_language", lambda: None)
    with pytest.raises(backends.BackendError, match="lky new --lang"):
        mod.Backend().speak("hola", "/tmp/never", lang=None)


def test_scenes_page_without_language_omits_attribute():
    import scenes
    assert "<html><head>" in scenes.page(100, 100, 1, "", "")
    assert '<html lang="es">' in scenes.page(100, 100, 1, "", "", "es")
