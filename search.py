#!/usr/bin/env python3
"""
search.py - Find moments in your past recordings by describing them ("where I run gdb on the
segfault"), using an `embed` backend over ~10 s windows of picture + transcript.

    search.py index ~/Videos/*.mp4          # add or update recordings in the index
    search.py query "gdb segfault" -n 5     # best matches: file, time, what was said
    search.py list                          # what's indexed

    The transcript comes from <video>.words.json (the cache every tool shares), made with the
    transcribe backend when missing. A recording that hasn't changed is never embedded again.
    Index: SEARCH_INDEX in the config (default ~/.local/share/lowkeyoutuber/search.json).
    To use a hit in a project: lky add-media -P <project> <file>, then a timeline item with
    "in"/"out" at the times shown.
"""

import argparse
import json
import math
import os
import sys

import backends
from backends._media import duration, frames
from yt_common import CONFIG, die, log


def index_path():
    p = CONFIG.get("SEARCH_INDEX") or os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
                                                    "lowkeyoutuber", "search.json")
    return os.path.expanduser(p)


def load_index():
    try:
        with open(index_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"backend": None, "files": {}}


def save_index(idx):
    p = index_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(idx, f)
    os.replace(p + ".tmp", p)


def words_of(video, tr):
    cache = os.path.splitext(video)[0] + ".words.json"
    if os.path.isfile(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                return json.load(f).get("words", [])
        except (OSError, ValueError):
            pass
    if not tr:
        return []
    try:   # call(): on a failure the next configured transcriber gets its turn
        return backends.call("transcribe", video, need=("word_timestamps",))[0]
    except backends.BackendError as e:
        log(f"could not transcribe {video} ({e}): indexing its pictures only")
        return []


def windows(video, words, size):
    total = duration(video)
    out, t = [], 0.0
    while t < total - 0.5:
        end = min(t + size, total)
        text = " ".join(w["w"] for w in words if t <= w["s"] < end)
        out.append({"start": round(t, 2), "end": round(end, 2), "text": text})
        t = end
    return out


def cosine(a, b):
    n = (sum(x * x for x in a) ** 0.5) * (sum(x * x for x in b) ** 0.5)
    return sum(x * y for x, y in zip(a, b)) / n if n else 0.0


def clock(t):
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def positive_seconds(text):
    try:
        v = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number")
    if not math.isfinite(v) or v <= 0:
        raise argparse.ArgumentTypeError("the window must be a positive number of seconds")
    return v


def index_key(emb):
    return backends.input_hash(emb.name, emb.cache_id() if hasattr(emb, "cache_id") else emb.model)


def index_with(emb, idx, videos, window, tr):
    """Index `videos` with this backend and return the new index. If the index was made with
    another model, every recording already in it is embedded again with this one (same window
    as before), so the index never mixes two embedding spaces and never loses what it had."""
    key = index_key(emb)
    rebuilt = bool(idx.get("key")) and idx["key"] != key
    todo = [(v, window) for v in videos]
    files = dict(idx["files"])
    if rebuilt:
        log(f"Embedding model changed ({idx.get('backend')} -> {emb.name}): embedding the indexed recordings again.")
        again = []
        for v, e in idx["files"].items():
            if v in videos:
                continue
            if os.path.isfile(v):
                again.append((v, e["stamp"][2]))
            else:
                log(f"dropping from the index (file is gone): {v}")
        todo = again + todo
        files = {}
    new = {"backend": emb.name, "key": key, "files": files}
    for v, win in todo:
        st = os.stat(v)
        stamp = [st.st_size, int(st.st_mtime), win]
        if files.get(v, {}).get("stamp") == stamp:
            log(f"unchanged: {v}")
            continue
        wins = windows(v, words_of(v, tr), win)
        log(f"{os.path.basename(v)}: {len(wins)} windows...")
        items = []
        for w in wins:
            jpg = frames(v, [(w["start"] + w["end"]) / 2], width=512)
            items.append({"text": w["text"], **({"jpeg": jpg[0]} if jpg else {})})
        vecs = emb.embed(items, task="document")
        for w, vec in zip(wins, vecs):
            w["vec"] = [round(x, 5) for x in vec]
        files[v] = {"stamp": stamp, "windows": wins}
        if not rebuilt:
            save_index(new)
    if rebuilt:   # the old index stays on disk until the whole new one exists
        save_index(new)
    return new


def main():
    p = argparse.ArgumentParser(description="Search past recordings by description.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("index")
    s.add_argument("videos", nargs="+")
    s.add_argument("--window", type=positive_seconds, default=10.0, help="seconds per window (default 10)")
    s.add_argument("--backend")
    s = sub.add_parser("query")
    s.add_argument("text")
    s.add_argument("-n", type=int, default=5)
    s.add_argument("--json", action="store_true")
    sub.add_parser("list")
    args = p.parse_args()
    idx = load_index()

    if args.cmd == "list":
        for f, e in idx["files"].items():
            print(f"{f}  ({len(e['windows'])} windows)")
        print(f"backend: {idx.get('backend')}  index: {index_path()}")
        return

    if args.cmd == "index":
        # --backend, or else the index's own backend, first; the rest of the chain are fallbacks
        first = args.backend or idx.get("backend")
        order = [first] if args.backend else ([first] if first else []) + [n for n in backends.chain("embed") if n != first]
        found, skipped = backends.candidates("embed", ",".join(order))
        for name, why in skipped:
            log(f"embed: skipping {name} ({why})")
        if not found:
            die(f"no usable embed backend in {order}. Run `lky backends` to see why.")
        try:
            tr = backends.pick("transcribe", need=("word_timestamps",))
        except backends.BackendError:
            tr = None
            log("No transcribe backend: indexing pictures only (and existing .words.json files).")
        videos = []
        for v in args.videos:
            v = os.path.abspath(os.path.expanduser(v))
            if os.path.isfile(v):
                videos.append(v)
            else:
                log(f"skipping (not found): {v}")
        errors = []
        for emb in found:
            try:
                index_with(emb, idx, videos, args.window, tr)
            except backends.BackendError as e:   # the next backend redoes the whole index with its own model
                log(f"embed: {emb.name} failed ({e}), trying the next one.")
                errors.append(f"{emb.name}: {e}")
                continue
            log(f"Index: {index_path()}")
            return
        die("every embed backend failed: " + "; ".join(errors))

    # query: always the model that made the index (another one would be another vector space)
    try:
        emb = backends.pick("embed", idx.get("backend"))
    except backends.BackendError as e:
        die(str(e))
    if idx.get("key") and idx["key"] != index_key(emb):
        die(f"the index was made with another embedding model ({idx.get('backend')}); index again to "
            "re-embed it with the one in use")
    try:
        q = emb.embed([{"text": args.text}], task="query")[0]
    except backends.BackendError as e:
        die(str(e))
    hits = []
    for f, e in idx["files"].items():
        for w in e["windows"]:
            hits.append({"file": f, "start": w["start"], "end": w["end"], "text": w["text"],
                         "score": round(cosine(q, w["vec"]), 4)})
    hits.sort(key=lambda h: -h["score"])
    hits = hits[:args.n]
    if args.json:
        json.dump(hits, sys.stdout, ensure_ascii=False)
        print()
        return
    if not hits:
        print("Nothing indexed yet: search.py index <videos>")
    for h in hits:
        print(f"{h['score']:.3f}  {os.path.basename(h['file'])}  {clock(h['start'])}-{clock(h['end'])}  {h['text'][:90]}")


if __name__ == "__main__":
    main()
