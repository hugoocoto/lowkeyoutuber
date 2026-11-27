"""Music from a local folder of tracks you're allowed to use (MUSIC_DIR). Picks the file whose
name shares the most words with the prompt ("calm-lofi-piano.mp3" for "calm piano"); ties go
to the first in alphabetical order, so the choice is the same every time."""

import os
import re

from backends import Base, BackendError
from yt_common import CONFIG

AUDIO = (".mp3", ".wav", ".flac", ".ogg", ".m4a", ".opus")


def words(text):
    return set(re.findall(r"[a-z0-9áéíóúñü]+", text.lower()))


class Backend(Base):
    caps = {"local": True, "generative": False}

    def __init__(self):
        self.dir = os.path.expanduser(CONFIG.get("MUSIC_DIR") or "~/Music/lowkeyoutuber")

    def tracks(self):
        try:
            return sorted(f for f in os.listdir(self.dir) if f.lower().endswith(AUDIO))
        except OSError:
            return []

    def check(self):
        if not self.tracks():
            return False, f"no audio files in {self.dir} (set MUSIC_DIR)"
        return True, ""

    def get(self, prompt, duration=None):
        tracks = self.tracks()
        if not tracks:
            raise BackendError(f"no audio files in {self.dir}")
        want = words(prompt or "")
        best = max(tracks, key=lambda f: (len(want & words(os.path.splitext(f)[0])), -tracks.index(f)))
        return os.path.join(self.dir, best)
