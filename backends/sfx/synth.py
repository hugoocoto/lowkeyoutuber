"""Zero-dependency procedural SFX generator (NumPy/stdlib + FFmpeg fallback)."""

import math
import os
import random
import shutil
import struct
import subprocess
import tempfile
import wave

import backends
from backends import Base, BackendError
from backends.sfx._cache import cache_dir, record_entry, slug

SR = 48000


def _write_wav(path, samples):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        pcm = bytearray()
        for s in samples:
            v = max(-1.0, min(1.0, float(s)))
            pcm += struct.pack("<h", int(v * 32767))
        w.writeframes(bytes(pcm))


def _whoosh(dur, seed=0):
    n = int(SR * dur)
    rng = random.Random(seed)
    lp = 0.0
    out = []
    for i in range(n):
        t = i / n
        env = math.sin(math.pi * t) ** 1.7
        white = rng.uniform(-1.0, 1.0)
        cutoff = 0.03 + 0.25 * math.sin(math.pi * t)
        lp = lp + cutoff * (white - lp)
        out.append(lp * env * 0.85)
    return out


def _click(dur, seed=0):
    n = int(SR * min(dur, 0.14))
    out = []
    for i in range(n):
        t = i / SR
        env = math.exp(-t * 65.0)
        sig = 0.55 * math.sin(2 * math.pi * 1650 * t) + 0.35 * math.sin(2 * math.pi * 3200 * t)
        out.append(sig * env * 0.7)
    return out


def _pop(dur, seed=0):
    n = int(SR * min(dur, 0.22))
    phase = 0.0
    out = []
    for i in range(n):
        t = i / SR
        freq = 280 + 480 * math.exp(-t * 35.0)
        phase += 2 * math.pi * freq / SR
        env = math.exp(-t * 28.0) * min(1.0, i / 80.0)
        out.append(math.sin(phase) * env * 0.75)
    return out


def _chime(dur, seed=0):
    n = int(SR * min(max(dur, 0.45), 1.4))
    out = []
    for i in range(n):
        t = i / SR
        e1 = math.exp(-t * 6.5) * min(1.0, i / 120.0)
        e2 = math.exp(-max(t - 0.09, 0.0) * 6.0) * (1.0 if t >= 0.09 else 0.0)
        sig = 0.45 * math.sin(2 * math.pi * 880 * t) * e1 + 0.45 * math.sin(2 * math.pi * 1318.5 * t) * e2
        out.append(sig * 0.75)
    return out


def _error(dur, seed=0):
    n = int(SR * min(max(dur, 0.3), 0.8))
    out = []
    for i in range(n):
        t = i / SR
        env = math.exp(-t * 7.5) * min(1.0, i / 100.0)
        f = 195 if t < 0.14 else 145
        sig = 0.5 * math.sin(2 * math.pi * f * t) + 0.25 * math.sin(2 * math.pi * f * 1.5 * t)
        out.append(sig * env * 0.7)
    return out


def _riser(dur, seed=0):
    n = int(SR * max(dur, 0.6))
    rng = random.Random(seed)
    phase, lp = 0.0, 0.0
    out = []
    for i in range(n):
        u = i / n
        env = (u ** 2.0) * min(1.0, (n - i) / 400.0)
        freq = 140 * (2.5 ** u)
        phase += 2 * math.pi * freq / SR
        lp += (0.04 + 0.3 * u) * (rng.uniform(-1, 1) - lp)
        out.append((0.45 * math.sin(phase) + 0.55 * lp) * env * 0.75)
    return out


def _kind_for(prompt):
    low = (prompt or "").lower()
    if any(w in low for w in ("click", "tap", "key", "type", "tick")):
        return "click", 0.10
    if any(w in low for w in ("pop", "bubble", "bloop")):
        return "pop", 0.16
    if any(w in low for w in ("chime", "ding", "bell", "success", "done", "notify")):
        return "chime", 0.65
    if any(w in low for w in ("error", "fail", "buzz", "warn", "wrong")):
        return "error", 0.45
    if any(w in low for w in ("riser", "swell", "build")):
        return "riser", 1.2
    return "whoosh", 0.65


class Backend(Base):
    caps = {"local": True, "generative": True, "cc0": True}

    def check(self):
        if not shutil.which("ffmpeg"):
            return False, "ffmpeg not installed"
        return True, ""

    def get(self, prompt, max_dur=None):
        if not shutil.which("ffmpeg"):
            raise BackendError("ffmpeg not installed")
        kind, default_dur = _kind_for(prompt)
        dur = min(default_dur, float(max_dur)) if max_dur else default_dur
        dur = max(dur, 0.06)
        h = backends.input_hash("sfx-synth", kind, round(dur, 3))
        dst = os.path.join(cache_dir(), f"synth_{kind}_{slug(prompt)}_{h}.wav")
        if os.path.isfile(dst):
            return dst
        fn = {"whoosh": _whoosh, "click": _click, "pop": _pop,
              "chime": _chime, "error": _error, "riser": _riser}[kind]
        samples = fn(dur, seed=int(h[:8], 16))
        if max_dur:  # some kinds have a minimum length; never go past what was asked
            samples = samples[:int(SR * dur)]
        part = dst[:-4] + ".part.wav"
        with tempfile.TemporaryDirectory() as tmp:
            raw = os.path.join(tmp, "raw.wav")
            _write_wav(raw, samples)
            r = subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", raw,
                 "-af", "aresample=48000,aformat=channel_layouts=stereo", "-c:a", "pcm_s16le", part],
                capture_output=True, text=True,
            )
            if r.returncode:
                if os.path.exists(part):
                    os.remove(part)
                raise BackendError(f"ffmpeg failed for synth sfx: {r.stderr.strip()[-200:]}")
        os.replace(part, dst)
        record_entry(dst, query=prompt, tags=[kind], duration=len(samples) / SR, license_name="CC0", source="synth")
        return dst
