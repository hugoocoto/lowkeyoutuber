"""Fake backends for tests: no models, no network."""

import subprocess

from backends import Base, BackendError


def tone(path, seconds, freq=440):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=f={freq}:d={seconds:.3f}", "-ac", "1", path], check=True)
    return path


def even_words(text, start=0.1, per_word=0.3):
    out, t = [], start
    for w in text.split():
        out.append({"w": w, "s": round(t, 3), "e": round(t + per_word - 0.05, 3)})
        t += per_word
    return out


class TimedTTS(Base):
    """Says every word in 0.3 s and returns timestamps, like ElevenLabs."""
    caps = {"local": True, "clone": True, "timestamps": True}
    model = "fake-timed"
    calls = 0

    def cache_id(self, draft=False):
        return [self.model]

    def speak(self, text, out_base, lang=None, context=None, draft=False):
        type(self).calls += 1
        n = len(text.split())
        return {"audio": tone(out_base + ".wav", 0.2 + 0.3 * n), "words": even_words(text), "model": self.model}


class PlainTTS(TimedTTS):
    """No timestamps, like Chatterbox/Kokoro: tts.py must transcribe."""
    caps = {"local": True, "clone": True, "timestamps": False}
    model = "fake-plain"

    def speak(self, text, out_base, lang=None, context=None, draft=False):
        r = super().speak(text, out_base, lang, context, draft)
        return dict(r, words=None)


class BrokenTTS(TimedTTS):
    model = "fake-broken"

    def speak(self, *a, **kw):
        raise BackendError("quota exceeded")


class FakeTranscribe(Base):
    """'Hears' a fixed transcript (set .heard), with one word misheard."""
    caps = {"local": True, "word_timestamps": True}
    model = "fake-whisper"
    heard = None

    def words(self, media, lang=None, prompt=None, track=0):
        return list(type(self).heard or [])


class FakeRender(Base):
    """Renders any scene folder as a solid-colour clip of the scene's data-duration."""
    caps = {"local": True, "deterministic": True}
    model = "fake-render"
    renders = 0

    def lint(self, scene_dir):
        html = open(f"{scene_dir}/index.html", encoding="utf-8").read()
        return [] if 'data-composition-id="main"' in html else ["error: no root composition"]

    def render_scene(self, scene_dir, out, fps, w, h, draft=False):
        import re
        type(self).renders += 1
        html = open(f"{scene_dir}/index.html", encoding="utf-8").read()
        dur = float(re.search(r'data-duration="([\d.]+)"', html).group(1))
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"color=c=#203040:s={w}x{h}:r={fps}:d={dur:.3f}", "-pix_fmt", "yuv420p", out], check=True)
        return out


class FakeJudge(Base):
    """Says yes to everything except scenes whose narration mentions 'roto' (broken)."""
    caps = {"local": True, "probabilities": True, "video": False}
    model = "fake-judge"
    seen = []

    def ask(self, inputs, questions):
        type(self).seen.append((inputs, questions))
        text = " ".join(q["ask"] for q in questions.values())
        broken = "roto" in text
        out = {}
        for n, q in questions.items():
            if q["type"] == "noul":
                yes = not broken if n != "broken" else broken
                out[n] = {"answer": yes, "score": 0.9 if yes else 0.1, "confidence": 0.9}
            elif q["type"] == "score":
                out[n] = {"answer": 1 if broken else 3, "score": 0.25 if broken else 0.75, "confidence": 0.9}
            else:
                out[n] = {"answer": next(iter(q["options"])), "score": 0.9, "confidence": 0.9}
        return out


class FakeEmbed(Base):
    """Bag of words over a tiny vocabulary: queries match windows that say the same words."""
    caps = {"local": True, "text": True, "image": True}
    model = "fake-embed"
    VOCAB = ["gdb", "segfault", "puntero", "malloc", "compilar", "hola", "lista", "make"]

    def cache_id(self):
        return [self.model]

    def embed(self, items, task="document"):
        out = []
        for it in items:
            words = (it.get("text") or "").lower().split()
            out.append([float(sum(w.strip(".,") == v for w in words)) + 1e-3 for v in self.VOCAB])
        return out
