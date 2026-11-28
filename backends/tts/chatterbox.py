"""Chatterbox Multilingual (Resemble AI, MIT): local, CUDA, clones VOICE_REF, Spanish among
23 languages. Every output carries a PerTh watermark. No timestamps: tts.py times the words
with Whisper.

    pip install chatterbox-tts
CONFIG
    VOICE_REF="~/.config/lowkeyoutuber/voice_ref.wav"   10-30 s of your clean voice
    CHATTERBOX_CKPT="~/models/chatterbox-es"  optional local checkpoint folder (e.g. a
                                              Spain-Spanish fine-tune), loaded with from_local
    CHATTERBOX_EXAGGERATION="0.5"             0.25-1.0, more = more expressive
"""

import importlib.util
import os

from backends import Base, BackendError
from backends.tts._common import cuda_device, voice_ref, write_wav
from yt_common import CONFIG, default_language, free_ollama_vram, log


class Backend(Base):
    caps = {"local": True, "clone": True, "timestamps": False, "pronunciation_dict": False,
            "watermark": True, "context": False, "cost": "free"}

    def __init__(self):
        self.ckpt = os.path.expanduser(CONFIG.get("CHATTERBOX_CKPT") or "")
        self.model = "chatterbox-multilingual" + (f" ({self.ckpt})" if self.ckpt else "")
        self._tts = None

    def check(self):
        if importlib.util.find_spec("chatterbox") is None:
            return False, "chatterbox-tts is not installed (pip install chatterbox-tts)"
        if self.ckpt and not os.path.isdir(self.ckpt):
            return False, f"CHATTERBOX_CKPT folder not found: {self.ckpt}"
        return True, "" if voice_ref() else "VOICE_REF not set: uses the default voice, not yours"

    def cache_id(self, draft=False):
        from backends import file_hash
        return [self.model, file_hash(voice_ref()), CONFIG.get("CHATTERBOX_EXAGGERATION") or "0.5"]

    def _load(self):
        if self._tts is None:
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS
            dev = cuda_device()
            if dev == "cuda":
                free_ollama_vram()
            else:
                log("chatterbox: no CUDA GPU, running on the CPU (slow).")
            self._tts = (ChatterboxMultilingualTTS.from_local(self.ckpt, dev) if self.ckpt
                         else ChatterboxMultilingualTTS.from_pretrained(device=dev))
        return self._tts

    def speak(self, text, out_base, lang=None, context=None, draft=False):
        lang = lang or default_language()
        if not lang:
            raise BackendError('chatterbox needs the language: set the language: lky new --lang xx, or script.json "lang"')
        try:
            tts = self._load()
            wav = tts.generate(text, language_id=lang, audio_prompt_path=voice_ref(),
                               exaggeration=float(CONFIG.get("CHATTERBOX_EXAGGERATION") or 0.5))
        except (RuntimeError, ValueError, OSError, AssertionError) as e:
            raise BackendError(f"chatterbox failed: {e}")
        return {"audio": write_wav(out_base + ".wav", wav, tts.sr), "words": None, "model": self.model}
