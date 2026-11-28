"""Shared SFX cache directory, metadata manifest, and ffprobe duration helpers."""

import json
import os
import re
import subprocess


def cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    d = os.path.join(base, "lowkeyoutuber", "sfx")
    os.makedirs(d, exist_ok=True)
    return d


def manifest_path():
    return os.path.join(cache_dir(), "manifest.json")


def load_manifest():
    try:
        with open(manifest_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_manifest(data):
    p = manifest_path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def record_entry(path, query="", tags=(), duration=None, license_name="CC0", source="", url="", author=""):
    if duration is None:
        duration = probe_duration(path)
    m = load_manifest()
    key = os.path.abspath(path)
    m[key] = {
        "file": key,
        "query": query,
        "tags": list(tags),
        "duration": round(float(duration or 0.0), 3),
        "license": license_name,
        "source": source,
        "url": url,
        "author": author,
    }
    save_manifest(m)
    return m[key]


def probe_duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def words(text):
    return set(re.findall(r"[a-z0-9áéíóúñü]+", (text or "").lower()))


def slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:40] or "sfx"


def find_cached(query, max_dur=None, source=None, min_score=1):
    want = words(query)
    if not want:
        return None
    best = None
    best_key = (-1, 0.0)
    for path, meta in load_manifest().items():
        if not os.path.isfile(path):
            continue
        if source and meta.get("source") != source:
            continue
        dur = float(meta.get("duration") or 0.0)
        if max_dur is not None and dur > float(max_dur) + 0.05:
            continue
        hay = words(" ".join([
            os.path.splitext(os.path.basename(path))[0],
            meta.get("query", ""),
            " ".join(meta.get("tags", [])),
        ]))
        score = len(want & hay)
        if score >= min_score and (score, -dur) > best_key:
            best_key = (score, -dur)
            best = path
    return best
