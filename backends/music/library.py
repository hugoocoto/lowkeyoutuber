"""Music from a local folder of tracks you're allowed to use (MUSIC_DIR).

Walks MUSIC_DIR recursively (excluding any sfx/ subfolder), reads embedded audio tags and
durations via ffprobe (cached by mtime/size), and matches prompts by tag/filename words or
semantic similarity when an `embed` backend is configured."""

import json
import math
import os
import re
import subprocess

import backends
from backends import Base, BackendError
from yt_common import CONFIG

AUDIO = (".mp3", ".wav", ".flac", ".ogg", ".m4a", ".opus")


def words(text):
    return set(re.findall(r"[a-z0-9áéíóúñü]+", (text or "").lower()))


def _cache_path():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    d = os.path.join(base, "lowkeyoutuber")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "music_index.json")


def _probe_meta(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:format_tags", "-of", "json", path],
        capture_output=True, text=True,
    )
    if r.returncode:
        return {"duration": 0.0, "tags": ""}
    try:
        fmt = json.loads(r.stdout).get("format", {})
        dur = float(fmt.get("duration") or 0.0)
        tags = fmt.get("tags") or {}
        tag_text = " ".join(f"{v}" for k, v in tags.items()
                            if k.lower() in ("title", "genre", "mood", "artist", "album", "comment", "description"))
        return {"duration": dur, "tags": tag_text}
    except (ValueError, TypeError):
        return {"duration": 0.0, "tags": ""}


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


class Backend(Base):
    caps = {"local": True, "generative": False, "sections": False}

    def __init__(self):
        self.dir = os.path.expanduser(CONFIG.get("MUSIC_DIR") or "~/Music/lowkeyoutuber")

    def tracks(self):
        if not os.path.isdir(self.dir):
            return []
        found = []
        for root, dirs, files in os.walk(self.dir):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d.lower() != "sfx")
            for f in sorted(files):
                if f.lower().endswith(AUDIO):
                    rel = os.path.relpath(os.path.join(root, f), self.dir)
                    found.append(rel)
        return sorted(found)

    def _index(self, tracks):
        cache_f = _cache_path()
        try:
            with open(cache_f, encoding="utf-8") as f:
                cache = json.load(f)
        except (OSError, ValueError):
            cache = {}
        dirty = False
        entries = []
        for rel in tracks:
            full = os.path.join(self.dir, rel)
            try:
                st = os.stat(full)
                sig = [st.st_size, st.st_mtime_ns]
            except OSError:
                continue
            cached = cache.get(full)
            if not cached or cached.get("sig") != sig:
                meta = _probe_meta(full)
                cached = {"sig": sig, **meta}
                cache[full] = cached
                dirty = True
            desc = f"{os.path.splitext(rel)[0]} {cached.get('tags', '')}".strip()
            entries.append({"rel": rel, "path": full, "duration": cached.get("duration", 0.0), "desc": desc})
        if dirty:
            try:
                with open(cache_f, "w", encoding="utf-8") as f:
                    json.dump(cache, f, ensure_ascii=False, indent=2)
            except OSError:
                pass
        return entries

    def check(self):
        if not self.tracks():
            return False, f"no audio files in {self.dir} (set MUSIC_DIR)"
        return True, ""

    def search(self, prompt, duration=None, limit=5):
        tracks = self.tracks()
        if not tracks:
            return []
        entries = self._index(tracks)
        want = words(prompt or "")
        # Try semantic similarity via embed backend when available and useful
        sem_scores = {}
        if prompt and len(entries) > 1:
            try:
                emb, _ = backends.call("embed", [{"text": prompt}] + [{"text": e["desc"]} for e in entries],
                                       task="query", need=("text",))
                qv, dvs = emb[0], emb[1:]
                for e, dv in zip(entries, dvs):
                    sem_scores[e["rel"]] = _cosine(qv, dv)
            except Exception:
                pass
        scored = []
        for idx, e in enumerate(entries):
            overlap = len(want & words(e["desc"]))
            dur_fit = 1 if (duration and e["duration"] >= float(duration) * 0.85) else 0
            sem = sem_scores.get(e["rel"], 0.0)
            scored.append(((overlap, dur_fit if overlap else 0, round(sem, 4), -idx), overlap, e))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [(overlap, e) for _, overlap, e in scored[:limit]]

    def get(self, prompt, duration=None, strict=False):
        hits = self.search(prompt, duration=duration, limit=1)
        if not hits:
            raise BackendError(f"no audio files in {self.dir}")
        overlap, best = hits[0]
        if strict and overlap == 0:
            raise BackendError(f"no matching track for {prompt!r} in {self.dir}")
        return best["path"]
