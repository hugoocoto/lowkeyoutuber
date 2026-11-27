"""Kokoro (82M, Apache 2.0): fast local TTS with preset voices, no cloning. Runs on the CPU,
so it's the fallback for drafts on a machine without CUDA. No timestamps.

    pip install kokoro soundfile   (and espeak-ng for non-English: sudo pacman -S espeak-ng)
CONFIG
    KOKORO_VOICE="ef_dora"   default: ef_dora for Spanish, af_heart for English
"""

import importlib.util

from backends import Base, BackendError
from backends.tts._common import cuda_device, write_wav
from yt_common import CONFIG

LANG_CODES = {"en": "a", "es": "e", "fr": "f", "it": "i", "pt": "p", "ja": "j", "zh": "z", "hi": "h"}
VOICES = {"a": "af_heart", "e": "ef_dora", "f": "ff_siwis", "i": "if_sara", "p": "pf_dora",
          "j": "jf_alpha", "z": "zf_xiaobei", "h": "hf_alpha"}
SR = 24000


class Backend(Base):
    caps = {"local": True, "clone": False, "timestamps": False, "pronunciation_dict": False,
            "watermark": False, "context": False, "cost": "free"}

    def __init__(self):
        self.model = "kokoro-82m"
        self._pipes = {}

    def check(self):
        if importlib.util.find_spec("kokoro") is None:
            return False, "kokoro is not installed (pip install kokoro soundfile)"
        return True, "preset voice, not yours"

    def voice(self, lang):
        return CONFIG.get("KOKORO_VOICE") or VOICES[LANG_CODES.get(lang or "en", "a")]

    def cache_id(self, draft=False):
        return [self.model, CONFIG.get("KOKORO_VOICE") or ""]

    def speak(self, text, out_base, lang=None, context=None, draft=False):
        code = LANG_CODES.get(lang or "en")
        if not code:
            raise BackendError(f"kokoro has no voices for language {lang!r}")
        try:
            import numpy as np
            from kokoro import KPipeline
            if code not in self._pipes:
                self._pipes[code] = KPipeline(lang_code=code, device=cuda_device())
            parts = [r.audio for r in self._pipes[code](text, voice=self.voice(lang), split_pattern=None)
                     if r.audio is not None]
        except (RuntimeError, ValueError, OSError, AssertionError) as e:
            raise BackendError(f"kokoro failed: {e}")
        if not parts:
            raise BackendError("kokoro produced no audio")
        audio = np.concatenate([p.detach().cpu().numpy() if hasattr(p, "detach") else p for p in parts])
        return {"audio": write_wav(out_base + ".wav", audio, SR), "words": None, "model": self.model}
