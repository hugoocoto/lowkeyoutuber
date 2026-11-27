"""faster-whisper on the GPU (CPU fallback), through the same code and cache as the classic
scripts (<media>.words.json, fixes.txt applied). Model: WHISPER_MODEL, else large-v3-turbo."""

import argparse
import importlib.util

from backends import Base
from yt_common import CONFIG, add_transcript_args, get_transcript


class Backend(Base):
    caps = {"local": True, "word_timestamps": True}

    def __init__(self):
        self.model = CONFIG.get("WHISPER_MODEL") or "large-v3-turbo"

    def check(self):
        if importlib.util.find_spec("faster_whisper") is None:
            return False, "faster-whisper is not installed (see README: Install)"
        return True, ""

    def words(self, media, lang=None, prompt=None, track=0):
        p = argparse.ArgumentParser()
        add_transcript_args(p)
        args = p.parse_args([])
        args.model, args.track = self.model, track
        args.language = lang or args.language
        args.prompt = prompt if prompt is not None else args.prompt
        return get_transcript(args, media)["words"]
