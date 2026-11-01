#!/usr/bin/env python3
"""
cut.py - Remove the parts of a recording listed in a text file, and carry its
transcript over to the result. For content cuts you decide (rambling, false
starts); pauses are left alone. Free and local.

CUTS FILE
    One part to remove per line, "start - end", in seconds or m:ss / h:mm:ss,
    with decimals. Anything after # is a comment.
        17:14.4 - 17:22.7      # config library false start
        1096.6 - 1184.35       # browsing the source

HOW IT WORKS
    1. Reads the transcript of the recording (cached in <video>.words.json,
       made now if missing) and prints, for every cut, the words just before
       and after it, so you can read how it will sound. A cut that splits a
       word is flagged.
    2. Renders the rest in one pass, cut at whole frames (NVENC on the NVIDIA
       GPU), all audio tracks kept and in sync, audio as FLAC.
    3. Writes <output>.words.json: the transcript moved to the new timeline.
       Whisper did better on the raw audio than it does after
       process-voice.sh, and this way publish.sh reuses it instead of
       transcribing again.

    Always listen to every cut: Whisper's word times are loose on fast speech,
    and a cut can leave half a syllable or a sentence that refers to
    something you removed.

USAGE
    cut.py raw.mkv cuts.txt --check      # only print the text around each cut
    cut.py raw.mkv cuts.txt              # -> raw_cut.mkv (+ raw_cut.words.json)
    cut.py raw.mkv cuts.txt -t 1         # voice on audio track 1
"""

import argparse
import math
import os
import re

from autocut import probe, render
from yt_common import add_transcript_args, clock, die, file_src, log, need, raw_transcript, save_transcript, word_sep


def parse_time(text):
    parts = text.strip().split(":")
    try:
        if not 1 <= len(parts) <= 3:
            raise ValueError
        return sum(float(p) * 60 ** i for i, p in enumerate(reversed(parts)))
    except ValueError:
        die(f"bad time: {text!r} (use seconds, m:ss or h:mm:ss)")


def read_cuts(path, duration):
    cuts = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            m = re.fullmatch(r"(\S+)\s*-\s*(\S+)", line)
            if not m:
                die(f"{path}:{n}: expected 'start - end'")
            s, e = parse_time(m.group(1)), min(parse_time(m.group(2)), duration)
            if not 0 <= s < e:
                die(f"{path}:{n}: start must be before end")
            cuts.append((s, e))
    cuts.sort()
    for (_, e1), (s2, _) in zip(cuts, cuts[1:]):
        if s2 < e1:
            die(f"cuts overlap around {clock(s2)}")
    return cuts


def keep_spans(cuts, duration, fps):
    """What stays, snapped to whole frames."""
    spans, t = [], 0.0
    for s, e in cuts + [(duration, duration)]:
        a, b = math.floor(t * fps + 1e-6) / fps, math.floor(s * fps + 1e-6) / fps
        if b > a:
            spans.append((a, b))
        t = e
    return spans


def stamp(t, hours):
    return clock(t, hours) + f"{t % 1:.1f}"[1:]   # m:ss.d


def report(words, cuts, sep, hours):
    for s, e in cuts:
        before = [w["w"] for w in words if w["e"] <= s + 0.05][-8:]
        after = [w["w"] for w in words if w["s"] >= e - 0.05][:8]
        log(f"[{stamp(s, hours)} - {stamp(e, hours)}] removes {e - s:.1f}s")
        log(f"   ...{sep.join(before)}  ||  {sep.join(after)}...")
        for t in (s, e):
            split = next((w for w in words if w["s"] + 0.03 < t < w["e"] - 0.03), None)
            if split:
                log(f"   ! the cut at {t:.2f}s splits {split['w']!r} ({split['s']:.2f}-{split['e']:.2f})")


def remap(words, spans):
    """Words inside a kept span, moved to the output timeline."""
    out, offset = [], 0.0
    for a, b in spans:
        for w in words:
            mid = (w["s"] + w["e"]) / 2
            if a <= mid < b:
                out.append({"w": w["w"], "s": round(offset + max(w["s"], a) - a, 3),
                            "e": round(offset + min(w["e"], b) - a, 3)})
        offset += b - a
    return out


def main():
    p = argparse.ArgumentParser(description="Remove the parts listed in a file from a recording (free, local).")
    p.add_argument("video")
    p.add_argument("cuts", help="text file, one 'start - end' per line (see -h above)")
    p.add_argument("-o", "--out", help="output file (default: <video>_cut.mkv)")
    p.add_argument("--check", action="store_true", help="only print the text around each cut")
    p.add_argument("--encoder", default="auto", help="auto (NVENC if available, else libx264), libx264, ...")
    p.add_argument("--quality", type=int, default=18, help="CRF/CQ, lower = better (default 18)")
    add_transcript_args(p)
    args = p.parse_args()

    for f in (args.video, args.cuts):
        if not os.path.isfile(f):
            die(f"File not found: {f}")
    need("ffmpeg", "ffprobe")
    fps, n_audio, duration = probe(args.video)
    if args.track >= n_audio:
        die(f"no audio track {args.track} (the file has {n_audio})")
    cuts = read_cuts(args.cuts, duration)
    if not cuts:
        die("the cuts file lists nothing to remove")

    data = raw_transcript(args, args.video)
    sep = word_sep(data)
    hours = duration >= 3600
    report(data["words"], cuts, sep, hours)
    spans = keep_spans(cuts, duration, fps)
    kept = sum(b - a for a, b in spans)
    log(f"\nResult: {clock(kept, hours)} (from {clock(duration, hours)}, {duration - kept:.0f}s removed)")
    if args.check:
        return

    out = args.out or os.path.splitext(args.video)[0] + "_cut.mkv"
    if os.path.abspath(out) == os.path.abspath(args.video):
        die("output would overwrite the input")
    log(f"Rendering {len(spans)} parts...")
    render(args, [list(s) for s in spans], fps, n_audio, out)
    words = remap(data["words"], spans)
    src = dict(file_src(out, args.track, argparse.Namespace(model=data["src"]["model"], prompt=data["src"]["prompt"])),
               **{"from": os.path.abspath(args.video)})
    save_transcript({"language": data["language"], "duration": kept, "src": src, "words": words}, out)
    log(f"-> {out}\n   transcript: {os.path.splitext(out)[0]}.words.json")


if __name__ == "__main__":
    main()
