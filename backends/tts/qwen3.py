"""Qwen3-TTS (Alibaba, Apache 2.0): local, CUDA (6-8 GB VRAM), clones a voice from ~3 s of
audio, Spanish among its ten languages. No timestamps: tts.py times the words with Whisper.

    pip install qwen-tts
CONFIG
    VOICE_REF="~/.config/lowkeyoutuber/voice_ref.wav"   your voice (shared by every cloning backend)
    VOICE_REF_TEXT="what you say in voice_ref.wav"      better clones (in-context mode); without it
                                                        only the voice's timbre is used
    QWEN3_TTS_MODEL="Qwen/Qwen3-TTS-12Hz-1.7B-Base"     or a local folder
"""

import importlib.util

from backends import Base, BackendError
from backends.tts._common import cuda_device, voice_ref, write_wav
from yt_common import CONFIG, LANG_NAMES, default_language, free_ollama_vram


class Backend(Base):
    caps = {"local": True, "clone": True, "timestamps": False, "pronunciation_dict": False,
            "watermark": False, "context": False, "cost": "free"}

    def __init__(self):
        self.model = CONFIG.get("QWEN3_TTS_MODEL") or "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
        self._tts = None

    def check(self):
        if importlib.util.find_spec("qwen_tts") is None:
            return False, "qwen-tts is not installed (pip install qwen-tts)"
        if not voice_ref():
            return False, "VOICE_REF is not set: Qwen3-TTS (Base) needs a clip of the voice to clone"
        if cuda_device() != "cuda":
            return False, "needs an NVIDIA GPU (CUDA)"
        return True, "" if CONFIG.get("VOICE_REF_TEXT") else "VOICE_REF_TEXT not set: timbre-only cloning"

    def cache_id(self, draft=False):
        from backends import file_hash
        return [self.model, file_hash(voice_ref()), CONFIG.get("VOICE_REF_TEXT") or ""]

    def _load(self):
        if self._tts is None:
            import torch
            from qwen_tts import Qwen3TTSModel
            free_ollama_vram()
            self._tts = Qwen3TTSModel.from_pretrained(self.model, device_map="cuda:0", dtype=torch.bfloat16)
        return self._tts

    def speak(self, text, out_base, lang=None, context=None, draft=False):
        lang = lang or default_language()
        if not lang:
            raise BackendError('qwen3-tts needs the language: set the language: lky new --lang xx, or script.json "lang"')
        ref_text = CONFIG.get("VOICE_REF_TEXT") or None
        try:
            wavs, sr = self._load().generate_voice_clone(
                text=text, language=LANG_NAMES.get(lang, lang), ref_audio=voice_ref(),
                ref_text=ref_text, x_vector_only_mode=ref_text is None)
        except (RuntimeError, ValueError, OSError, TypeError) as e:
            raise BackendError(f"qwen3-tts failed: {e}")
        return {"audio": write_wav(out_base + ".wav", wavs[0], sr), "words": None, "model": self.model}
