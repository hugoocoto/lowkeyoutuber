#!/usr/bin/env python3
"""
tts.py - Narrate a project's script with whichever TTS backend is configured.

For every line of script.json it writes, in narration/:
    <id>.wav          48 kHz mono, ready for the timeline
    <id>.words.json   word timings with the script's spelling (for captions, scenes and subtitles)
    <id>.json         what made it: backend, model, input hash, characters
A line whose text, voice and settings haven't changed is never generated again
(paid backends are not charged twice). Delete <id>.json, or use -f, to redo it.

TIMING
    Backends with timestamps (ElevenLabs) give the word times directly. For the
    others, a transcribe backend (Whisper) listens to the audio and align.py
    gives the script's words its times. --verify also transcribes backends with
    timestamps, to catch skipped or garbled words.

SCRIPT FIELDS (script.json "lines")
    id       "01-hook"           file names and timeline references
    text     what is shown       (subtitles, captions)
    spoken   what is said        optional, when it differs ("main punto c" for "main.c")

USAGE
    tts.py -P project/                      # every line, configured backend chain
    tts.py -P project/ --backend kokoro     # one backend (or a chain: "elevenlabs,chatterbox")
    tts.py -P project/ --draft              # cheaper/faster model where the backend has one
    tts.py -P project/ --lines 01-hook,03   # only these lines
    tts.py -P project/ --place              # also put the lines one after another on the timeline
    tts.py -P project/ --json               # summary on stdout for agents

    Exit code 3 means a person has to act (TTS is "none": record the lines).
"""

import argparse
import json
import os
import subprocess
import sys

import backends
import project as proj
from align import align_text, similarity
from yt_common import die, log, need

GAP = 0.35   # silence between lines with --place, seconds


def probe_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def to_wav(src, dst):
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", src, "-ac", "1", "-ar", "48000",
                        "-c:a", "pcm_s16le", dst], capture_output=True, text=True)
    if r.returncode:
        raise backends.BackendError(f"could not convert {src}: {r.stderr.strip()[-300:]}")


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def line_key(b, line, lang, draft):
    spoken = line.get("spoken") or line["text"]
    cid = b.cache_id(draft) if hasattr(b, "cache_id") else [b.model]
    return backends.input_hash("tts", b.name, cid, spoken, lang, draft)


def time_words(line, raw, wav, lang, verify):
    """Word timings in the script's spelling, the raw (spoken) timings they came from, and a
    verification ratio when transcribed. raw: the backend's own timings, or None."""
    ratio = None
    if not raw or verify:
        heard, _ = backends.call("transcribe", wav, lang=lang, need=("word_timestamps",))
        ratio = similarity(line.get("spoken") or line["text"], heard)
        raw = raw or heard
    words, _ = align_text(line["text"], raw)
    return words, raw, ratio


def write_words(path, lang, words, raw):
    # "raw" keeps the spoken timings, so an edit to "text" alone re-times the captions for free
    write_json(path, {"language": lang, "words": words, "raw": raw})


def commit(staged, meta):
    """Move staged files over the committed ones. The old metadata (the cache key) is removed
    first and the new one goes in last, so an interrupted commit leaves no key at all and the
    line is generated again, instead of an old key next to new audio."""
    if os.path.exists(meta):
        os.remove(meta)
    for tmp, final in staged:
        os.replace(tmp, final)


def line_spans(lines, durs, with_visuals=True):
    """How long each line's picture lasts when lines play one after another: its narration plus
    the gap after it; the first line also gets "pad_before" (the voice starts that much into its
    picture) and the last "pad_after". Returns (lead, spans)."""
    lead = float(lines[0].get("pad_before", 0.3)) if with_visuals and lines else 0.0
    spans = []
    for i, (line, d) in enumerate(zip(lines, durs)):
        last = i + 1 == len(lines)
        spans.append(d + (float(line.get("pad_after", 0.8)) if last else GAP) + (lead if i == 0 else 0))
    return lead, spans


def place(pj, lines, ndir, summary, args):
    """Put the narration on the timeline.
    - Lines with "at" (seconds) start there: voice-over on an existing video. A line longer
      than the gap to the next one is reported in summary["overruns"]: rewrite it shorter.
    - Otherwise lines follow each other with a short gap.
    - When every line has "visual" (a media id or a file: image, scene render, clip), the
      video track is rebuilt too: each visual lasts as long as its line (+ gap), and the
      first line's "pad_before" / last line's "pad_after" seconds (default 0.3 / 0.8) frame it."""
    tl = pj.setdefault("timeline", {})
    keep = [a for a in tl.get("audio", []) if not str(a.get("src", "")).startswith("vo:")]
    durs = [probe_duration(os.path.join(ndir, line["id"] + ".wav")) for line in lines]
    timed = [line for line in lines if "at" in line]
    if timed and len(timed) != len(lines):
        die("either every line of script.json has 'at' (voice-over) or none does")
    media = pj.get("media", {})
    for line in lines:   # a rendered scene (scenes.py) is the line's picture unless one is given
        if not line.get("visual") and f"scene:{line['id']}" in media:
            line["visual"] = f"scene:{line['id']}"
    visuals = [line for line in lines if line.get("visual")]
    if visuals and len(visuals) != len(lines):
        die("either every line of script.json has 'visual' or none does (lines without: "
            + ", ".join(line["id"] for line in lines if not line.get("visual")) + ")")
    if timed and visuals:
        die("script.json lines have both 'at' (voice-over on one video) and 'visual' (a picture per line): "
            "use one or the other")
    lead, spans = line_spans(lines, durs, bool(visuals))
    placed, video, overruns, at = [], [], [], lead
    for i, (line, d) in enumerate(zip(lines, durs)):
        if timed:
            at = float(line["at"])
            nxt = float(lines[i + 1]["at"]) if i + 1 < len(lines) else None
            if nxt is not None and at + d > nxt - 0.1:
                overruns.append({"id": line["id"], "too_long_by": round(at + d - nxt + 0.1, 2)})
        placed.append({"src": f"vo:{line['id']}", "at": round(at, 3), "line": line["id"]})
        if visuals:
            span = spans[i]
            v = line["visual"]
            item = {"src": v, "line": line["id"]}
            if isinstance(v, dict):   # {"src": ..., "in": ...} for a clip, zooms, fit...
                item = dict(v, line=line["id"])
                item["out"] = round(float(v.get("in", 0)) + span, 3) if "in" in v else None
            if "out" not in item or item["out"] is None:
                item.pop("out", None)
                item["dur"] = round(span, 3)
            video.append(item)
        if not timed:
            at += d + GAP
    tl["audio"] = placed + keep
    if visuals:
        tl["video"] = video
    tl.setdefault("captions", {})["from"] = "media"
    summary["placed"] = len(placed)
    summary["narration_end"] = round(max(p["at"] + d for p, d in zip(placed, durs)), 3)
    if overruns:
        summary["overruns"] = overruns
        for o in overruns:
            log(f"  {o['id']} runs {o['too_long_by']}s into the next line: rewrite it shorter, then "
                f"tts.py --lines {o['id']} --place")
    log(f"Placed {len(placed)} lines on the timeline" + (" and their visuals" if visuals else "")
        + f" (narration ends at {summary['narration_end']:.1f}s).")


def main():
    p = argparse.ArgumentParser(description="Narrate script.json with the configured TTS backend.")
    p.add_argument("-P", "--project", required=True)
    p.add_argument("--backend", help="backend name or chain, e.g. 'kokoro' or 'elevenlabs,chatterbox'")
    p.add_argument("--draft", action="store_true", help="cheaper/faster model (ElevenLabs: ELEVEN_DRAFT_MODEL)")
    p.add_argument("--lines", help="only these line ids, comma-separated")
    p.add_argument("--verify", action="store_true", help="transcribe every line and compare it with the script")
    p.add_argument("--min-match", type=float, default=0.75, help="--verify threshold, 0-1 (default 0.75)")
    p.add_argument("--place", action="store_true", help="put the lines one after another on the timeline")
    p.add_argument("-f", "--force", action="store_true", help="regenerate even if cached")
    p.add_argument("--json", action="store_true", help="print a summary as JSON on stdout")
    args = p.parse_args()
    need("ffmpeg", "ffprobe")

    try:
        pj = proj.load(args.project)
        script = proj.load_script(args.project)
    except proj.ProjectError as e:
        die(str(e))
    lang = script.get("lang") or pj.get("lang")
    lines = script["lines"]
    if args.lines:
        want = {x.strip() for x in args.lines.split(",")}
        unknown = want - {line["id"] for line in lines}
        if unknown:
            die(f"no such lines in script.json: {', '.join(sorted(unknown))}")
    else:
        want = {line["id"] for line in lines}
    ndir = os.path.join(proj.root(args.project), "narration")
    os.makedirs(ndir, exist_ok=True)

    summary = {"generated": [], "cached": [], "failed": [], "needs_human": None, "chars": 0,
               "backends": {}, "low_match": []}
    found, skipped = backends.candidates("tts", args.backend)
    for name, why in skipped:
        log(f"tts: skipping {name} ({why})")
    if not found:
        die(f"no usable TTS backend in {backends.chain('tts', args.backend)}. Run `lky backends`.")

    for i, line in enumerate(lines):
        lid = line["id"]
        if lid not in want:
            continue
        base = os.path.join(ndir, lid)
        meta = read_json(base + ".json")
        if (not args.force and meta and os.path.isfile(base + ".wav") and os.path.isfile(base + ".words.json")
                and meta.get("key") == line_key(found[0], line, lang, args.draft)):
            # only a line made by the backend that would be used now counts: a line made by a
            # fallback (quota out, first backend down) is redone once the first one works again
            if args.verify or meta.get("text") != line["text"]:
                # the audio is still good: only the captions (text edited) or the check (--verify)
                # need redoing, never a new paid generation
                try:
                    old = read_json(base + ".words.json") or {}
                    words, raw, ratio = time_words(line, old.get("raw"), base + ".wav", lang, args.verify)
                except backends.BackendError as e:
                    summary["failed"].append({"id": lid, "errors": [str(e)]})
                    continue
                write_words(base + ".words.json.tmp", lang, words, raw)
                meta["text"] = line["text"]
                if ratio is not None:
                    meta["match"] = round(ratio, 3)
                write_json(base + ".json.tmp", meta)
                # the audio stays: words first, metadata last; an interruption leaves the old
                # metadata (old "text"), so the next run simply re-times again
                for tmp, final in ((base + ".words.json.tmp", base + ".words.json"),
                                   (base + ".json.tmp", base + ".json")):
                    os.replace(tmp, final)
                if ratio is not None and ratio < args.min_match:
                    summary["low_match"].append({"id": lid, "match": round(ratio, 3)})
                    log(f"  {lid}: what was heard matches the script only {ratio:.0%}. "
                        "Listen to it, or regenerate (-f).")
            summary["cached"].append(lid)
            continue
        context = {"previous_text": (lines[i - 1].get("spoken") or lines[i - 1]["text"]) if i else None,
                   "next_text": (lines[i + 1].get("spoken") or lines[i + 1]["text"]) if i + 1 < len(lines) else None}
        spoken = line.get("spoken") or line["text"]
        res = used = None
        errors = []
        for b in found:
            try:
                log(f"{lid}: {b.name}...")
                res = b.speak(spoken, base + ".raw", lang=lang, context=context, draft=args.draft)
                used = b
                break
            except backends.NeedsHuman as e:
                summary["needs_human"] = str(e)
                break
            except backends.BackendError as e:
                log(f"  {b.name} failed: {e}")
                errors.append(f"{b.name}: {e}")
        if summary["needs_human"]:
            break
        if not res:
            summary["failed"].append({"id": lid, "errors": errors})
            continue
        # stage everything next to the old files and replace them only once timing worked: a
        # failure leaves the previous generation as it was (audio, timings and key together)
        staged = [(base + ".new.wav", base + ".wav"), (base + ".words.json.tmp", base + ".words.json"),
                  (base + ".json.tmp", base + ".json")]   # metadata last
        try:
            to_wav(res["audio"], base + ".new.wav")
            if res["audio"] != base + ".new.wav":
                os.remove(res["audio"])
            words, raw, ratio = time_words(line, res.get("words"), base + ".new.wav", lang, args.verify)
        except backends.BackendError as e:
            for tmp, _ in staged:
                if os.path.exists(tmp):
                    os.remove(tmp)
            summary["failed"].append({"id": lid, "errors": [str(e)]})
            continue
        write_words(base + ".words.json.tmp", lang, words, raw)
        dur = probe_duration(base + ".new.wav")
        write_json(base + ".json.tmp", {"key": line_key(used, line, lang, args.draft), "backend": used.name,
                                        "model": res.get("model") or used.model, "draft": args.draft,
                                        "text": line["text"], "chars": len(spoken), "duration": round(dur, 3),
                                        "timestamps": "backend" if res.get("words") else "transcribed",
                                        "match": None if ratio is None else round(ratio, 3)})
        commit(staged, base + ".json")
        summary["generated"].append(lid)
        summary["chars"] += len(spoken)
        summary["backends"][lid] = used.name
        if ratio is not None and ratio < args.min_match:
            summary["low_match"].append({"id": lid, "match": round(ratio, 3)})
            log(f"  {lid}: what was heard matches the script only {ratio:.0%}. Listen to it, or regenerate (-f).")

    # register every narrated line as media (also the cached ones)
    for line in lines:
        base = os.path.join(ndir, line["id"])
        if os.path.isfile(base + ".wav"):
            pj.setdefault("media", {})[f"vo:{line['id']}"] = {
                "file": proj.rel(args.project, base + ".wav"),
                "words": proj.rel(args.project, base + ".words.json"), "kind": "audio"}
            meta = read_json(base + ".json") or {}
            pj.setdefault("backends_used", {}).setdefault("tts", {})[line["id"]] = meta.get("backend")

    complete = all(os.path.isfile(os.path.join(ndir, line["id"] + ".wav")) for line in lines)
    if summary["needs_human"]:
        proj.set_state(pj, "tts", "needs-human", summary["needs_human"])
    else:
        proj.set_state(pj, "tts", "done" if complete and not summary["failed"] else "failed")

    if args.place and complete:
        place(pj, lines, ndir, summary, args)
    proj.save(args.project, pj)

    log(f"TTS: {len(summary['generated'])} generated, {len(summary['cached'])} cached, "
        f"{len(summary['failed'])} failed, {summary['chars']} characters sent.")
    if args.json:
        json.dump(summary, sys.stdout, ensure_ascii=False)
        print()
    if summary["needs_human"]:
        log(summary["needs_human"])
        sys.exit(3)
    if summary["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
