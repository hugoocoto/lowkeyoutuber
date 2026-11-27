#!/usr/bin/env python3
"""
align.py - Match a script to what was actually said.

TWO USES
    1. Timing a known text (used by tts.py): a TTS backend without timestamps
       makes audio for a line; Whisper transcribes it; the script's own words
       get Whisper's times. Words Whisper got wrong or missed are placed in
       between by their length, so the subtitles always show the script's spelling.
    2. Picking takes (this command): you recorded the script (one long take or
       several, retakes included). Each script line is found in the transcript,
       the LAST complete take of each line is kept, and the result is written
       as a proposal; --apply turns it into timeline items.

    Takes are kept whole: pauses inside a take are never trimmed (NOTES.md).

USAGE
    align.py -P project/ --media rec1                 # proposals/takes.json + report
    align.py -P project/ --media rec1 --apply         # also write timeline.video/audio
    align.py -P project/ --media rec1 --threshold 0.7 --json

    The recording must be in project.json "media" (lky add-media) and the
    script in script.json. The transcript is cached in <recording>.words.json.
"""

import argparse
import difflib
import json
import os
import subprocess
import sys

from yt_common import bare_word, die, log

PAD_IN, PAD_OUT = 0.15, 0.25


def norm_tokens(text_or_words):
    """Comparable tokens: lowercase, no surrounding punctuation."""
    if isinstance(text_or_words, str):
        items = text_or_words.split()
    else:
        items = [w if isinstance(w, str) else w["w"] for w in text_or_words]
    return [b for b in (bare_word(t) for t in items)]


def similarity(a, b):
    ta, tb = norm_tokens(a), norm_tokens(b)
    if not ta or not tb:
        return 0.0
    return difflib.SequenceMatcher(None, ta, tb, autojunk=False).ratio()


def align_text(text, words):
    """Give each word of `text` a time from `words` (a transcript of the same speech).
    Returns (timed words with the script's spelling, match ratio 0-1)."""
    script = text.split()
    if not script:
        return [], 0.0
    if not words:
        return [], 0.0
    a, b = norm_tokens(script), norm_tokens(words)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    times = [None] * len(script)
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            w = words[blk.b + k]
            times[blk.a + k] = (w["s"], w["e"])
    # Unmatched runs: spread them over the gap between their matched neighbours,
    # proportionally to their length in characters.
    start0, end0 = words[0]["s"], words[-1]["e"]
    i = 0
    while i < len(script):
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(script) and times[j] is None:
            j += 1
        lo = times[i - 1][1] if i > 0 else start0
        hi = times[j][0] if j < len(script) else end0
        if hi < lo:
            hi = lo
        lens = [max(len(script[k]), 1) for k in range(i, j)]
        total, t = sum(lens), lo
        for k, n in zip(range(i, j), lens):
            d = (hi - lo) * n / total
            times[k] = (t, t + d)
            t += d
        i = j
    out = [{"w": w, "s": round(s, 3), "e": round(max(e, s + 0.01), 3)} for w, (s, e) in zip(script, times)]
    return out, sm.ratio()


def sentence_spans(words, gap=0.8):
    """Split a transcript into runs of speech separated by a pause or a sentence end."""
    spans, cur = [], []
    for k, w in enumerate(words):
        if cur and w["s"] - words[cur[-1]]["e"] > gap:
            spans.append(cur)
            cur = []
        cur.append(k)
        if w["w"].endswith((".", "?", "!", "…")):
            spans.append(cur)
            cur = []
    if cur:
        spans.append(cur)
    return [(s[0], s[-1]) for s in spans]


def find_takes(text, words, spans, threshold):
    """Every place `text` was said. Candidate windows are 1-4 consecutive speech spans; the
    best-matching windows win and windows overlapping them are dropped, so two attempts said
    one after the other stay two takes instead of merging into one."""
    n_tok = len(norm_tokens(text))
    cands = []
    for i in range(len(spans)):
        for j in range(i, min(i + 4, len(spans))):
            a, b = spans[i][0], spans[j][1]
            if b - a + 1 > n_tok * 2 + 4:
                break
            r = similarity(text, words[a:b + 1])
            if r >= threshold:
                cands.append((r, -(b - a), a, b))   # ties: the shorter window
    taken = []
    for r, _, a, b in sorted(cands, reverse=True):
        if all(b < t["first"] or a > t["last"] for t in taken):
            taken.append({"first": a, "last": b, "ratio": round(r, 3),
                          "start": words[a]["s"], "end": words[b]["e"]})
    return sorted(taken, key=lambda t: t["start"])


def pick_takes(lines, words, threshold=0.6):
    """For each line, the last good take before the next line starts. Returns (chosen, report)."""
    spans = sentence_spans(words)
    all_takes = [find_takes(line.get("spoken") or line["text"], words, spans, threshold) for line in lines]
    chosen, report, cursor = [], [], -1.0
    for i, line in enumerate(lines):
        after = [t for t in all_takes[i] if t["start"] >= cursor]
        # a retake of this line comes before the next line is first said
        first = after[0]["start"] if after else cursor
        nxt = min((t["start"] for t in all_takes[i + 1] if t["start"] > first), default=None) \
            if i + 1 < len(lines) else None
        usable = [t for t in after if nxt is None or t["start"] < nxt]
        if not usable:
            chosen.append(None)
            report.append({"id": line["id"], "status": "missing", "takes": 0})
            continue
        t = usable[-1]
        cursor = t["end"]
        said = " ".join(w["w"] for w in words[t["first"]:t["last"] + 1])
        chosen.append(t)
        report.append({"id": line["id"], "status": "ok" if t["ratio"] >= 0.85 else "changed",
                       "takes": len(usable), "discarded": len(usable) - 1, "ratio": t["ratio"],
                       "start": t["start"], "end": t["end"], "said": said})
    return chosen, report


def media_duration(path):
    """Length of the file in seconds (as render.py measures it), or None if unknown."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def main():
    p = argparse.ArgumentParser(description="Match script lines to a recording and pick the takes.")
    p.add_argument("-P", "--project", required=True)
    p.add_argument("--media", required=True, help="media id of the recording in project.json")
    p.add_argument("--threshold", type=float, default=0.6, help="0-1, how close a take must be (default 0.6)")
    p.add_argument("--track", type=int, help="audio track with the voice (default: the media's track, else 0)")
    p.add_argument("--apply", action="store_true", help="write the chosen takes into the timeline")
    p.add_argument("--json", action="store_true", help="print the result as JSON on stdout")
    args = p.parse_args()

    import backends
    import project as proj
    try:
        pj = proj.load(args.project)
        script = proj.load_script(args.project)
    except proj.ProjectError as e:
        die(str(e))
    m = pj.get("media", {}).get(args.media)
    if not m:
        die(f"no media {args.media!r} in project.json")
    media_file = proj.resolve(args.project, m["file"])
    track = args.track if args.track is not None else int(m.get("track", 0))
    try:
        tr = backends.pick("transcribe", need=("word_timestamps",))
        words = tr.words(media_file, lang=pj.get("lang"), track=track)
    except backends.BackendError as e:
        die(str(e))
    if not words:
        die("no speech found in the recording")
    m["words"] = proj.rel(args.project, os.path.splitext(media_file)[0] + ".words.json")

    lines = script["lines"]
    chosen, report = pick_takes(lines, words, args.threshold)
    out = {"media": args.media, "lines": report}
    with open(os.path.join(proj.root(args.project), "proposals", "takes.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    missing = [r["id"] for r in report if r["status"] == "missing"]
    changed = [r["id"] for r in report if r["status"] == "changed"]
    for r in report:
        if r["status"] == "missing":
            log(f"  {r['id']}: NOT FOUND")
        else:
            log(f"  {r['id']}: {r['start']:.1f}-{r['end']:.1f}s, {r['takes']} take(s), match {r['ratio']:.0%}"
                + ("  <- said differently: " + r["said"] if r["status"] == "changed" else ""))
    proj.set_state(pj, "align", "proposed")

    if args.apply:
        if missing:
            die(f"lines not found in the recording: {', '.join(missing)}. Record them or edit script.json, "
                "then run align.py again (nothing written to the timeline).")
        vid, cursor = [], 0.0
        end = media_duration(media_file)
        for line, t in zip(lines, chosen):
            a, b = max(t["start"] - PAD_IN, 0), t["end"] + PAD_OUT
            if end:
                b = min(b, end)   # the last word can end right at the end of the recording
            vid.append({"src": args.media, "in": round(a, 3), "out": round(b, 3), "line": line["id"]})
            cursor += b - a
        m["track"] = track   # render the same audio the takes were chosen from
        tl = pj.setdefault("timeline", {})
        tl["video"] = vid
        tl.setdefault("captions", {})["from"] = "media"
        proj.set_state(pj, "align", "done")
        log(f"Timeline: {len(vid)} takes, {cursor:.1f}s.")
    proj.save(args.project, pj)
    if changed:
        log(f"Said differently from the script: {', '.join(changed)} (check proposals/takes.json)")
    if args.json:
        json.dump(dict(out, missing=missing, changed=changed, applied=args.apply), sys.stdout, ensure_ascii=False)
        print()


if __name__ == "__main__":
    main()
