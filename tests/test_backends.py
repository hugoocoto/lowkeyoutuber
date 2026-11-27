import base64
import inspect
import io
import json
import wave

import pytest

import backends
from backends import BackendError, NeedsHuman
from fakes import BrokenTTS, TimedTTS


@pytest.fixture
def config(monkeypatch):
    cfg = backends.CONFIG
    for k in ("PROFILE", "TTS", "LLM", "TRANSCRIBE", "MUSIC", "MUSIC_DIR"):
        monkeypatch.delitem(cfg, k, raising=False)
    return cfg


def test_every_kind_has_its_backends():
    assert {"claude_cli", "codex_cli", "opencode_cli", "ollama"} <= set(backends.names("llm"))
    assert {"elevenlabs", "chatterbox", "kokoro", "none"} <= set(backends.names("tts"))
    assert "faster_whisper" in backends.names("transcribe")
    assert "library" in backends.names("music")


@pytest.mark.parametrize("kind", list(backends.KINDS))
def test_contract(kind):
    """Every backend loads without its model installed, describes itself and has the kind's method."""
    method = backends.KINDS[kind]
    for name in backends.names(kind):
        b = backends.load(kind, name)
        ok, why = b.check()
        assert isinstance(ok, bool) and isinstance(why, str), name
        assert "local" in b.caps, f"{kind}/{name} must say whether it's local"
        assert callable(getattr(b, method, None)), f"{kind}/{name} has no {method}()"
        info = b.info()
        assert info["name"] == name and info["kind"] == kind
        if kind == "tts":
            params = inspect.signature(b.speak).parameters
            assert {"text", "out_base", "lang", "context", "draft"} <= set(params), name
            assert "timestamps" in b.caps, name


def test_chain_order(config):
    assert backends.chain("tts")[0] == "chatterbox"            # local profile
    config["PROFILE"] = "cloud"
    assert backends.chain("tts")[0] == "elevenlabs"
    config["TTS"] = "kokoro, none"
    assert backends.chain("tts") == ["kokoro", "none"]
    assert backends.chain("tts", "elevenlabs") == ["elevenlabs"]  # --backend wins


def test_fallback_and_needs_human(config):
    backends.register("tts", "t_broken", BrokenTTS)
    backends.register("tts", "t_ok", TimedTTS)
    try:
        res, used = backends.call("tts", "hola", "/tmp/x_fallback", override="t_broken,t_ok,none")
        assert used.name == "t_ok" and res["words"][0]["w"] == "hola"
        with pytest.raises(NeedsHuman):
            backends.call("tts", "hola", "/tmp/x", override="t_broken,none")
        with pytest.raises(BackendError):
            backends.pick("tts", override="no_such_backend")
        # a capability nobody in the chain has
        with pytest.raises(BackendError):
            backends.pick("tts", override="t_ok", need=("pronunciation_dict",))
    finally:
        backends.unregister("tts", "t_broken")
        backends.unregister("tts", "t_ok")


def test_describe_doesnt_crash():
    rows = backends.describe()
    assert {r["kind"] for r in rows} == set(backends.KINDS)


def test_elevenlabs_alignment_to_words():
    from backends.tts.elevenlabs import chars_to_words
    text = "Hola  mundo."
    al = {"characters": list(text),
          "character_start_times_seconds": [i * 0.1 for i in range(len(text))],
          "character_end_times_seconds": [i * 0.1 + 0.1 for i in range(len(text))]}
    assert chars_to_words(al) == [{"w": "Hola", "s": 0.0, "e": 0.4}, {"w": "mundo.", "s": 0.6, "e": 1.2}]


def test_elevenlabs_request(config, monkeypatch, tmp_path):
    from backends.tts import elevenlabs
    config["ELEVEN_VOICE_ID"] = "voice123"
    config["ELEVEN_OUTPUT"] = "pcm_24000"
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    sent = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        sent["url"], sent["body"], sent["key"] = req.full_url, json.loads(req.data), req.headers.get("Xi-api-key")
        text = "Hola"
        return Resp(json.dumps({"audio_base64": base64.b64encode(b"\0\0" * 2400).decode(),
                                "alignment": {"characters": list(text),
                                              "character_start_times_seconds": [0, .1, .2, .3],
                                              "character_end_times_seconds": [.1, .2, .3, .4]}}).encode())

    monkeypatch.setattr(elevenlabs.urllib.request, "urlopen", fake_urlopen)
    b = elevenlabs.Backend()
    assert b.check()[0]
    res = b.speak("Hola", str(tmp_path / "x"), lang="es", context={"previous_text": "Antes"}, draft=True)
    assert "voice123/with-timestamps?output_format=pcm_24000" in sent["url"]
    assert sent["body"]["model_id"] == "eleven_flash_v2_5" and sent["body"]["language_code"] == "es"
    assert sent["body"]["previous_text"] == "Antes" and sent["key"] == "k"
    assert res["words"] == [{"w": "Hola", "s": 0, "e": 0.4}]
    with wave.open(res["audio"]) as w:
        assert w.getframerate() == 24000 and w.getnframes() == 2400


def test_elevenlabs_needs_key(config, monkeypatch):
    from backends.tts import elevenlabs
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    ok, why = elevenlabs.Backend().check()
    assert not ok and "ELEVENLABS_API_KEY" in why


def test_music_library(config, tmp_path):
    for f in ("calm-lofi-piano.mp3", "epic-drums.mp3", "notes.txt"):
        (tmp_path / f).write_bytes(b"x")
    config["MUSIC_DIR"] = str(tmp_path)
    b = backends.load("music", "library").__class__()
    assert b.check()[0]
    assert b.get("calm piano").endswith("calm-lofi-piano.mp3")
    assert b.get("drums").endswith("epic-drums.mp3")
