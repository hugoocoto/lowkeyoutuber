"""
backends - Swappable models: one interface per kind, several providers each.

Tools never name a provider. They ask the registry for a kind ("tts") and get
the first backend of the configured chain that is installed, configured and
can do what they need. A new provider is one new file: backends/<kind>/<name>.py
with a class Backend(Base). Nothing else changes.

KINDS (method every backend of that kind has)
    llm         complete(prompt, json_mode=False) -> str
    transcribe  words(media, lang=None, prompt=None, track=0) -> [{"w", "s", "e"}]
    tts         speak(text, out_base, lang, context=None, draft=False) -> {"audio", "words" | None}
    music       get(prompt, duration) -> path
    Planned (issue #1): judge, embed, render.

CAPABILITIES
    Each backend has a caps dict ({"local": True, "timestamps": False, ...}), so
    tools ask what a backend can do instead of checking its name. When a
    capability is missing the tool falls back to a generic path: a TTS backend
    without timestamps gets its word timings from a transcribe backend.

CONFIG (~/.config/lowkeyoutuber/config)
    PROFILE="local"                  local (default) or cloud: a chain per kind
    TTS="elevenlabs,chatterbox,none" a chain for one kind; the first backend
                                     that works is used, the next ones are
                                     fallbacks (missing key, quota, failure)
    Keys per kind: LLM, TRANSCRIBE, TTS, MUSIC. API keys come from the
    environment only, never from the config file.

    `lky backends` lists every backend, whether it can be used here, and what it can do.
"""

import hashlib
import importlib
import json
import os
import pkgutil

from yt_common import CONFIG, log

KINDS = {
    "llm": "complete",
    "transcribe": "words",
    "tts": "speak",
    "music": "get",
}
PLANNED = {
    "judge": "ask(media, questions) -> [{answer, score, confidence}]  (ollama_vlm, gemini_decisions)",
    "embed": "embed(items) -> vectors  (local OpenCLIP + text, gemini_embedding)",
    "render": "render_scene(file, out, fps, w, h)  (hyperframes, fframes, remotion)",
}

# The default chain per kind. "local" needs no API keys: the LLM chain is the
# one the classic scripts already use (AI CLIs use their own sign-in, Ollama is free).
PROFILES = {
    "local": {
        "llm": "claude_cli,codex_cli,opencode_cli,ollama",
        "transcribe": "faster_whisper",
        "tts": "chatterbox,kokoro,none",
        "music": "library",
    },
    "cloud": {
        "llm": "claude_cli,codex_cli,opencode_cli,ollama",
        "transcribe": "faster_whisper",
        "tts": "elevenlabs,chatterbox,kokoro,none",
        "music": "library",
    },
}


class BackendError(RuntimeError):
    """The call failed in a way the next backend of the chain may not (missing key, quota, crash)."""


class NeedsHuman(BackendError):
    """Nothing automatic can do this: a person has to (e.g. record the narration). Stops the chain."""


class Base:
    kind = ""
    name = ""
    caps: dict = {}
    model = None

    def check(self):
        """(usable, reason). Cheap: no model loading, no paid calls."""
        return True, ""

    def info(self):
        ok, why = self.check()
        return {"kind": self.kind, "name": self.name, "model": self.model, "usable": ok,
                "why": why, "caps": dict(self.caps)}


_extra = {}      # (kind, name) -> class, registered at runtime (tests, plugins)
_loaded = {}     # (kind, name) -> instance


def register(kind, name, cls):
    _extra[(kind, name)] = cls
    _loaded.pop((kind, name), None)


def unregister(kind, name):
    _extra.pop((kind, name), None)
    _loaded.pop((kind, name), None)


def _check_kind(kind):
    if kind not in KINDS:
        raise ValueError(f"unknown backend kind {kind!r} (known: {', '.join(KINDS)})")


def names(kind):
    _check_kind(kind)
    pkg = importlib.import_module(f"backends.{kind}")
    found = sorted(m.name for m in pkgutil.iter_modules(pkg.__path__) if not m.name.startswith("_"))
    return found + sorted(n for k, n in _extra if k == kind and n not in found)


def load(kind, name):
    _check_kind(kind)
    key = (kind, name)
    if key not in _loaded:
        if key in _extra:
            cls = _extra[key]
        else:
            try:
                cls = importlib.import_module(f"backends.{kind}.{name}").Backend
            except ModuleNotFoundError as e:
                if e.name == f"backends.{kind}.{name}":
                    raise ValueError(f"no {kind} backend named {name!r} (known: {', '.join(names(kind))})")
                raise
        inst = cls()
        inst.kind, inst.name = kind, name
        _loaded[key] = inst
    return _loaded[key]


def profile():
    p = (CONFIG.get("PROFILE") or "local").strip()
    if p not in PROFILES:
        log(f"Unknown PROFILE {p!r} in the config, using 'local'.")
        p = "local"
    return p


def chain(kind, override=None):
    """Backend names to try, in order: --backend, then the config key, then the profile."""
    _check_kind(kind)
    spec = override or CONFIG.get(kind.upper()) or PROFILES[profile()][kind]
    return [s.strip() for s in spec.split(",") if s.strip()]


def _has(backend, need):
    return all(backend.caps.get(c) for c in need)


def candidates(kind, override=None, need=()):
    """Usable backends of the chain, in order, each with why the others were skipped."""
    out, skipped = [], []
    for name in chain(kind, override):
        try:
            b = load(kind, name)
        except ValueError as e:
            skipped.append((name, str(e)))
            continue
        ok, why = b.check()
        if not ok:
            skipped.append((name, why))
        elif not _has(b, need):
            skipped.append((name, "can't " + ", ".join(c for c in need if not b.caps.get(c))))
        else:
            out.append(b)
    return out, skipped


def pick(kind, override=None, need=()):
    found, skipped = candidates(kind, override, need)
    for name, why in skipped:
        log(f"{kind}: skipping {name} ({why})")
    if not found:
        raise BackendError(f"no usable {kind} backend in {chain(kind, override)}. Run `lky backends` to see why.")
    return found[0]


def call(kind, *args, override=None, need=(), **kw):
    """Call the kind's method on the first backend that works; on BackendError try the next.
    Returns (result, backend). NeedsHuman stops the chain."""
    found, skipped = candidates(kind, override, need)
    for name, why in skipped:
        log(f"{kind}: skipping {name} ({why})")
    errors = []
    for b in found:
        try:
            return getattr(b, KINDS[kind])(*args, **kw), b
        except NeedsHuman:
            raise
        except BackendError as e:
            log(f"{kind}: {b.name} failed ({e}), trying the next one.")
            errors.append(f"{b.name}: {e}")
    raise BackendError(f"every {kind} backend failed: " + ("; ".join(errors) or "none usable"))


def describe(kinds=None):
    """Everything `lky backends` shows."""
    out = []
    for kind in kinds or KINDS:
        order = chain(kind)
        for name in names(kind):
            try:
                d = load(kind, name).info()
            except Exception as e:   # a broken backend must not break the listing
                d = {"kind": kind, "name": name, "usable": False, "why": f"failed to load: {e}", "caps": {}}
            d["in_chain"] = order.index(name) + 1 if name in order else None
            out.append(d)
    # the chain first, in the order it's tried, then the rest
    return sorted(out, key=lambda d: (list(kinds or KINDS).index(d["kind"]), d["in_chain"] or 99, d["name"]))


def input_hash(*parts):
    """Stable hash of a call's inputs, for caching paid or slow results."""
    blob = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def file_hash(path):
    if not path or not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:16]
