"""faster-whisper on the GPU (CPU fallback), through the same code and cache as the classic
scripts (<media>.words.json, fixes.txt applied). Model: WHISPER_MODEL, else large-v3-turbo."""

import argparse
import importlib.util

from backends import Base, BackendError
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
        # the shared transcript code ends with die() (SystemExit) or raises on a bad model load;
        # turn both into BackendError so the next transcribe backend gets its turn, while the
        # classic scripts keep exiting as before
        try:
            return get_transcript(args, media)["words"]
        except SystemExit as e:
            raise BackendError(str(e.code).removeprefix("Error: ")) from None
        except (RuntimeError, ValueError, OSError, ImportError) as e:
            raise BackendError(f"faster-whisper failed: {e}") from e
