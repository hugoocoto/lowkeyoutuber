"""Helpers shared by the TTS backends."""

import os
import wave

from yt_common import CONFIG


def voice_ref():
    """The one clip of your voice every cloning backend uses (VOICE_REF), or None."""
    p = CONFIG.get("VOICE_REF") or ""
    p = os.path.expanduser(p)
    return p if p and os.path.isfile(p) else None


def write_wav(path, samples, sr):
    """Float samples in [-1, 1] (numpy array or tensor) -> 16-bit mono WAV."""
    import numpy as np
    a = samples.detach().cpu().numpy() if hasattr(samples, "detach") else np.asarray(samples)
    a = np.clip(a.reshape(-1).astype(np.float32), -1, 1)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes((a * 32767).astype("<i2").tobytes())
    return path


def cuda_device():
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"
