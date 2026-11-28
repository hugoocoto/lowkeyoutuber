"""
project.py - The project folder the tools share. Not meant to be run directly
(use `lky new`, `lky status`, `lky check`). The format is documented in docs/project.md.

    myproject/
      project.json   media, timeline, formats, state (+ inputs, and the flow it started from, if any)
      script.json    lines of narration/scenes, when the video has a script (edit me)
      media/         inputs: recordings, captures, images, music
      narration/     TTS output or my takes (+ *.words.json)
      scenes/        generated scenes
      proposals/     cuts, takes, plans waiting for a human's OK
      checks/        reports, frames, contact sheets
      out/           video.mp4, video_9x16.mp4, video.srt, ...
"""

import json
import os

VERSION = 1
DIRS = ("media", "narration", "scenes", "proposals", "checks", "out")
SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350)}
STATES = ("pending", "proposed", "approved", "done", "skipped", "needs-human", "failed")


class ProjectError(ValueError):
    pass


def new(path, flow=None, lang="es", formats=("16:9",), inputs=None, fps=30):
    """flow: only a note of which example in flows/ the project started from (optional)."""
    if os.path.exists(os.path.join(path, "project.json")):
        raise ProjectError(f"{path} already has a project.json")
    for f in formats:
        if f not in SIZES:
            raise ProjectError(f"unknown format {f!r} (known: {', '.join(SIZES)})")
    os.makedirs(path, exist_ok=True)
    for d in DIRS:
        os.makedirs(os.path.join(path, d), exist_ok=True)
    p = {"version": VERSION, "lang": lang, "fps": fps,
         "formats": {f: list(SIZES[f]) for f in formats},
         "inputs": dict(inputs or {}), "state": {}, "backends_used": {}, "media": {},
         "timeline": {"video": [], "audio": [], "captions": {"burn": False, "style": "word-highlight"}}}
    if flow:
        p["flow"] = flow
    save(path, p)
    return p


def root(path):
    """Accept the project folder or its project.json."""
    return os.path.dirname(path) if path.endswith(".json") else path


def load(path):
    path = root(path)
    f = os.path.join(path, "project.json")
    try:
        with open(f, encoding="utf-8") as fh:
            p = json.load(fh)
    except FileNotFoundError:
        raise ProjectError(f"no project.json in {path} (create one with: lky new {path})")
    except ValueError as e:
        raise ProjectError(f"{f} is not valid JSON: {e}")
    if p.get("version") != VERSION:
        raise ProjectError(f"{f}: version {p.get('version')!r}, this toolkit reads version {VERSION}")
    return p


def save(path, p):
    path = root(path)
    tmp = os.path.join(path, "project.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, os.path.join(path, "project.json"))


def resolve(path, rel):
    """Project-relative paths stay inside the project; absolute paths and ~ are allowed for inputs."""
    if rel is None:
        return None
    rel = os.path.expanduser(rel)
    return rel if os.path.isabs(rel) else os.path.join(root(path), rel)


def rel(path, file):
    """Store files inside the project as relative paths."""
    a, r = os.path.abspath(file), os.path.abspath(root(path))
    return os.path.relpath(a, r) if a.startswith(r + os.sep) else a


def set_state(p, step, value, note=None):
    if value not in STATES:
        raise ProjectError(f"unknown state {value!r} (known: {', '.join(STATES)})")
    p.setdefault("state", {})[step] = value if note is None else {"state": value, "note": note}


def state_of(p, step):
    v = p.get("state", {}).get(step, "pending")
    return v["state"] if isinstance(v, dict) else v


def load_script(path):
    f = os.path.join(root(path), "script.json")
    if not os.path.isfile(f):
        raise ProjectError(f"no script.json in {root(path)}")
    with open(f, encoding="utf-8") as fh:
        s = json.load(fh)
    errs = validate_script(s)
    if errs:
        raise ProjectError("script.json: " + "; ".join(errs))
    return s


def validate_script(s):
    errs = []
    lines = s.get("lines") if isinstance(s, dict) else None
    if not isinstance(lines, list):
        return ["needs a 'lines' list"]
    seen = set()
    for n, line in enumerate(lines, 1):
        if not isinstance(line, dict):
            errs.append(f"line {n} is not an object")
            continue
        lid = line.get("id")
        if not isinstance(lid, str) or not lid or "/" in lid or lid.startswith("."):
            errs.append(f"line {n}: 'id' must be a short name like '01-hook'")
        elif lid in seen:
            errs.append(f"line {n}: duplicate id {lid!r}")
        seen.add(lid)
        if not isinstance(line.get("text"), str) or not line["text"].strip():
            errs.append(f"line {n} ({lid}): empty 'text'")
    return errs


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def validate(path, p, check_files=True):
    """List of problems; empty means the project can be rendered."""
    errs = []
    if not isinstance(p.get("formats"), dict) or not p["formats"]:
        errs.append("'formats' must map a name to [width, height], e.g. {\"16:9\": [1920, 1080]}")
    else:
        for name, wh in p["formats"].items():
            if not (isinstance(wh, list) and len(wh) == 2 and all(isinstance(v, int) and v > 0 and v % 2 == 0
                                                                  for v in wh)):
                errs.append(f"format {name}: size must be [even width, even height]")
    if not (_num(p.get("fps")) and 1 <= p["fps"] <= 120):
        errs.append("'fps' must be a number between 1 and 120")
    media = p.get("media", {})
    for mid, m in media.items():
        if not isinstance(m, dict) or not m.get("file"):
            errs.append(f"media {mid}: needs a 'file'")
        elif check_files and not os.path.isfile(resolve(path, m["file"])):
            errs.append(f"media {mid}: file not found: {m['file']}")
        elif m.get("words") and check_files and not os.path.isfile(resolve(path, m["words"])):
            errs.append(f"media {mid}: words file not found: {m['words']}")
        variants = m.get("variants") if isinstance(m, dict) else None
        if variants is not None and not isinstance(variants, dict):
            errs.append(f"media {mid}: 'variants' must map a format to a file path")
            variants = None
        for fmt, f in (variants or {}).items():
            if not isinstance(f, str) or not f:
                errs.append(f"media {mid}: {fmt} variant must be a file path")
            elif check_files and not os.path.isfile(resolve(path, f)):
                errs.append(f"media {mid}: {fmt} variant not found: {f}")

    def src_ok(where, item):
        src = item.get("src")
        if src is None:
            return
        if src in media:
            return
        if check_files and not os.path.isfile(resolve(path, src)):
            errs.append(f"{where}: src {src!r} is neither a media id nor a file")

    tl = p.get("timeline", {})
    for n, it in enumerate(tl.get("video", []), 1):
        where = f"timeline.video[{n}]"
        if not isinstance(it, dict):
            errs.append(f"{where}: not an object")
            continue
        if "src" not in it and "color" not in it:
            errs.append(f"{where}: needs 'src' (media id or file) or 'color'")
        src_ok(where, it)
        has_range = _num(it.get("in")) and _num(it.get("out"))
        if has_range and it["out"] <= it["in"]:
            errs.append(f"{where}: 'out' must be after 'in'")
        if not has_range and not (_num(it.get("dur")) and it["dur"] > 0):
            errs.append(f"{where}: needs 'in' and 'out' (video) or 'dur' (image, color)")
        for z, zm in enumerate(it.get("zooms", []), 1):
            r = zm.get("rect")
            if not (isinstance(r, list) and len(r) == 4 and all(_num(v) for v in r)
                    and 0 <= r[0] < 1 and 0 <= r[1] < 1 and 0 < r[2] <= 1 and 0 < r[3] <= 1):
                errs.append(f"{where}.zooms[{z}]: 'rect' must be [x, y, w, h] as fractions of the frame (0-1)")
            if not (_num(zm.get("at")) and _num(zm.get("dur")) and zm["dur"] > 0):
                errs.append(f"{where}.zooms[{z}]: needs 'at' and 'dur' in seconds (from the item's start)")
        if it.get("fit") not in (None, "pad", "blur", "fill"):
            errs.append(f"{where}: 'fit' must be pad, blur or fill")
    for n, it in enumerate(tl.get("audio", []), 1):
        where = f"timeline.audio[{n}]"
        if not isinstance(it, dict) or "src" not in it:
            errs.append(f"{where}: needs 'src'")
            continue
        src_ok(where, it)
        if not (_num(it.get("at")) and it["at"] >= 0):
            errs.append(f"{where}: needs 'at' (seconds on the timeline)")
        if _num(it.get("in")) and _num(it.get("out")) and it["out"] <= it["in"]:
            errs.append(f"{where}: 'out' must be after 'in'")
        if "dur" in it and not (_num(it["dur"]) and it["dur"] > 0):
            errs.append(f"{where}: 'dur' must be > 0")
        for fk in ("fade_in", "fade_out"):
            if it.get(fk) is not None and not (_num(it.get(fk)) and it[fk] >= 0):
                errs.append(f"{where}: '{fk}' must be >= 0")
        if it.get("loop") is not None and not isinstance(it.get("loop"), bool):
            errs.append(f"{where}: 'loop' must be true or false")
        if it.get("bus") not in (None, "voice", "music", "sfx"):
            errs.append(f"{where}: 'bus' must be voice, music or sfx")
    cap = tl.get("captions", {})
    if cap.get("style") not in (None, "word-highlight", "plain"):
        errs.append("timeline.captions.style must be word-highlight or plain")
    return errs
