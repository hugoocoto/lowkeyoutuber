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
