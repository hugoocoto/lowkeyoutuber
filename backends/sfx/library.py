"""Local SFX library: searches SFX_DIR (or MUSIC_DIR/sfx) and the local downloaded SFX cache."""

import os

from backends import Base, BackendError
from backends.sfx._cache import find_cached, load_manifest, probe_duration, words
from yt_common import CONFIG

AUDIO = (".mp3", ".wav", ".flac", ".ogg", ".m4a", ".opus")


class Backend(Base):
    caps = {"local": True, "generative": False, "cc0": True}

    def __init__(self):
        sfx_dir = CONFIG.get("SFX_DIR")
        if sfx_dir:
            self.dir = os.path.expanduser(sfx_dir)
        else:
            music_dir = os.path.expanduser(CONFIG.get("MUSIC_DIR") or "~/Music/lowkeyoutuber")
            self.dir = os.path.join(music_dir, "sfx")

    def files(self):
        out = []
        if os.path.isdir(self.dir):
            for root, dirs, files in os.walk(self.dir):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for f in sorted(files):
                    if f.lower().endswith(AUDIO):
                        out.append(os.path.join(root, f))
        return out

    def check(self):
        has_cache = any(os.path.isfile(p) for p in load_manifest())
        if not self.files() and not has_cache:
            return False, f"no SFX in {self.dir} or local cache (set SFX_DIR or use freesound/cc0/synth)"
        return True, ""

    def get(self, prompt, max_dur=None):
        want = words(prompt)
        best, best_score = None, (0, 0.0)
        for path in self.files():
            rel = os.path.relpath(path, self.dir)
            score = len(want & words(os.path.splitext(rel)[0]))
            if score == 0:
                continue
            dur = probe_duration(path) if max_dur is not None else 0.0
            if max_dur is not None and (dur <= 0 or dur > float(max_dur) + 0.05):
                continue
            key = (score, -dur)
            if key > best_score:
                best_score = key
                best = path
        if best:
            return best
        cached = find_cached(prompt, max_dur=max_dur, min_score=1)
        if cached:
            return cached
        raise BackendError(f"no local SFX matching {prompt!r}")
