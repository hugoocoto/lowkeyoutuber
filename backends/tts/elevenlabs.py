"""ElevenLabs text to speech, with character-level timestamps (so no Whisper pass is needed
for timing). Cloud, paid per character.

CONFIG
    ELEVEN_VOICE_ID="..."          your cloned voice (Instant or Professional voice clone)
    ELEVEN_MODEL="eleven_v4"       final renders; --draft uses ELEVEN_DRAFT_MODEL (eleven_flash_v2_5)
    ELEVEN_PRONUNCIATION_DICT="id:version_id"   optional pronunciation dictionary for jargon
    ELEVEN_OUTPUT="mp3_44100_128"  output_format: mp3_*, opus_*, wav_*, pcm_* (higher qualities need a higher plan)
ENVIRONMENT
    ELEVENLABS_API_KEY             never put it in the config file
API: POST /v1/text-to-speech/{voice_id}/with-timestamps -> audio_base64 + alignment
(characters, character_start_times_seconds, character_end_times_seconds).
"""

import base64
import json
import os
import urllib.error
import urllib.request
import wave

from backends import Base, BackendError
from yt_common import CONFIG

API = "https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps?output_format={fmt}"
NO_LANGUAGE_CODE = {"eleven_multilingual_v2"}   # rejects language_code


def chars_to_words(al):
    """ElevenLabs alignment (one entry per character) -> [{"w", "s", "e"}], split on whitespace."""
    chars = al.get("characters") or []
    starts = al.get("character_start_times_seconds") or []
    ends = al.get("character_end_times_seconds") or []
    words, cur, s, e = [], "", None, None
    for c, cs, ce in zip(chars, starts, ends):
        if c.isspace():
            if cur:
                words.append({"w": cur, "s": round(s, 3), "e": round(e, 3)})
            cur, s = "", None
            continue
        if not cur:
            s = cs
        cur += c
        e = ce
    if cur:
        words.append({"w": cur, "s": round(s, 3), "e": round(e, 3)})
    return words


class Backend(Base):
    caps = {"local": False, "clone": True, "timestamps": True, "pronunciation_dict": True,
            "watermark": False, "context": True, "cost": "per character"}

    def __init__(self):
        self.model = CONFIG.get("ELEVEN_MODEL") or "eleven_v4"
        self.draft_model = CONFIG.get("ELEVEN_DRAFT_MODEL") or "eleven_flash_v2_5"
        self.voice = CONFIG.get("ELEVEN_VOICE_ID") or ""
        self.fmt = CONFIG.get("ELEVEN_OUTPUT") or "mp3_44100_128"

    def check(self):
        if not os.environ.get("ELEVENLABS_API_KEY"):
            return False, "ELEVENLABS_API_KEY is not set in the environment"
        if not self.voice:
            return False, "ELEVEN_VOICE_ID is not set in the config"
        return True, ""

    def cache_id(self, draft=False):
        return [self.draft_model if draft else self.model, self.voice, self.fmt,
                CONFIG.get("ELEVEN_PRONUNCIATION_DICT") or ""]

    def speak(self, text, out_base, lang=None, context=None, draft=False):
        model = self.draft_model if draft else self.model
        body = {"text": text, "model_id": model}
        if lang and model not in NO_LANGUAGE_CODE:
            body["language_code"] = lang
        context = context or {}
        if context.get("previous_text"):
            body["previous_text"] = context["previous_text"]
        if context.get("next_text"):
            body["next_text"] = context["next_text"]
        pd = CONFIG.get("ELEVEN_PRONUNCIATION_DICT")
        if pd:
            pid, _, ver = pd.partition(":")
            body["pronunciation_dictionary_locators"] = [{"pronunciation_dictionary_id": pid, "version_id": ver}]
        req = urllib.request.Request(
            API.format(voice=self.voice, fmt=self.fmt), json.dumps(body).encode("utf-8"),
            {"Content-Type": "application/json", "xi-api-key": os.environ.get("ELEVENLABS_API_KEY", "")})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise BackendError(f"HTTP {e.code}: {detail}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise BackendError(str(e))
        audio = base64.b64decode(data["audio_base64"])
        kind = self.fmt.split("_")[0]
        if kind == "pcm":   # raw 16-bit mono samples: give them a WAV header
            path = f"{out_base}.wav"
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(int(self.fmt.split("_")[1]))
                w.writeframes(audio)
        else:
            ext = "ogg" if kind == "opus" else kind
            path = f"{out_base}.{ext}"
            with open(path, "wb") as f:
                f.write(audio)
        words = chars_to_words(data.get("alignment") or {}) or None
        return {"audio": path, "words": words, "model": model}
