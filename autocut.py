#!/usr/bin/env python3
"""
autocut.py - Automatic first cut of a raw recording: removes silences, long
pauses and retakes using the Whisper transcript. Free and local.

HOW IT WORKS
    1. Transcribes the voice track on the GPU (cached in <video>.words.json).
    2. Keeps only the time where words are spoken, plus a little padding, so
       every pause longer than --pause becomes a jump cut.
    3. Retakes: when you repeat or restart a sentence, the earlier attempt is
       dropped and the last one is kept. It compares each sentence with the
       next two, so it only catches retakes said right after the mistake.
       Turn it off with --keep-retakes.
    4. Cuts ALL audio tracks the same way, so voice and desktop/music stay in
       sync and separate for process-voice.sh (-t voice -m music). Video is
       re-encoded (NVENC on the NVIDIA GPU), audio stays lossless (FLAC).

    A list of every removed retake and kept span is written next to the
    output (<output>.cuts.txt).

USAGE
    autocut.py raw.mkv -t 1                 # voice on track 1 -> raw_cut.mkv
    autocut.py raw.mkv -t 1 --dry-run       # only show what would be removed
    autocut.py raw.mkv -t 1 --pause 0.8     # keep pauses up to 0.8 s
    autocut.py raw.mkv -t 1 --encoder libx264

THEN
    process-voice.sh -t 1 -m 0 raw_cut.mkv  # or: publish.sh -t 1 -m 0 raw_cut.mkv

Always watch the result: Whisper can miss quiet words, and the retake
detector can be wrong. Use --dry-run first on a new kind of recording.
"""

import argparse
import difflib
import json
import math
import os
import re
import subprocess
import tempfile
from fractions import Fraction

from yt_common import (add_transcript_args, clock, die, get_transcript, log, make_sentences, need,
                       video_encoder, word_sep)


def probe(video):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_type,avg_frame_rate,r_frame_rate:format=duration",
                        "-of", "json", video], capture_output=True, text=True)
    if r.returncode:
        die("ffprobe failed: " + r.stderr.strip())
    d = json.loads(r.stdout)
    streams = d.get("streams", [])
    video_streams = [s for s in streams if s["codec_type"] == "video"]
    if not video_streams:
        die("no video stream")
    v = video_streams[0]
    rate = v.get("avg_frame_rate", "0/0")
    fps = Fraction(rate) if not rate.startswith("0") else Fraction(v["r_frame_rate"])
    n_audio = sum(s["codec_type"] == "audio" for s in streams)
    return float(fps), n_audio, float(d["format"]["duration"])


# ---------------------------------------------------------------------------
# What to keep
# ---------------------------------------------------------------------------
def tokens(text, sep):
    text = text.lower()
    return re.findall(r"\w+", text) if sep else list(re.sub(r"\W", "", text))


def find_retakes(sentences, sep, threshold):
    """Indexes of sentences that are failed attempts of a following sentence."""
    drop = set()
    for i, a in enumerate(sentences):
        ta = tokens(a["text"], sep)
        if len(ta) < 3:
            continue
        for j in (i + 1, i + 2):
            if j >= len(sentences):
                break
            tb = tokens(sentences[j]["text"], sep)[:len(ta) + 2]
            if difflib.SequenceMatcher(None, ta, tb).ratio() >= threshold:
                drop.update(range(i, j))   # the attempt and anything said before the retry
                break
    return drop


def keep_spans(words, pause, pad_in, pad_out, duration, fps):
    spans = []
    for w in words:
        if spans and w["s"] - spans[-1][1] <= pause:
            spans[-1][1] = max(spans[-1][1], w["e"])
        else:
            spans.append([w["s"], w["e"]])
    out = []
    for s, e in spans:
        # pad, then snap to whole frames so audio and video cut at the same instants
        s = math.floor(max(s - pad_in, 0) * fps) / fps
        e = math.ceil(min(e + pad_out, duration) * fps) / fps
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        elif e > s:
            out.append([s, e])
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render(args, spans, fps, n_audio, out):
    # Frame k is kept when k/fps is inside [s, e). Shifting the bounds by half a
    # frame avoids float ties; audio is split into tiny frames (256 samples) so
    # it is cut at nearly the same instants and stays in sync after many cuts.
    half = 0.5 / fps
    expr = "+".join(f"between(t,{s - half:.5f},{e - half:.5f})" for s, e in spans)
    graph = [f"[0:v]select='{expr}',setpts=N/FRAME_RATE/TB[v]"]
    for i in range(n_audio):
        graph.append(f"[0:a:{i}]asetnsamples=n=256:p=0,aselect='{expr}',asetpts=N/SR/TB[a{i}]")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(";\n".join(graph))
        script = f.name
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", "-nostdin", "-y",
           "-i", args.video, "-/filter_complex", script, "-map", "[v]"]
    for i in range(n_audio):
        cmd += ["-map", f"[a{i}]"]
    cmd += video_encoder(args.encoder, args.quality) + ["-pix_fmt", "yuv420p", "-c:a", "flac", out]
    try:
        if subprocess.run(cmd).returncode:
            if os.path.exists(out):
                os.remove(out)
            die("ffmpeg failed")
    finally:
        os.remove(script)


def main():
    p = argparse.ArgumentParser(description="Remove pauses and retakes from a raw recording (free, local).")
    p.add_argument("video")
    p.add_argument("-o", "--out", help="output file (default: <video>_cut.mkv)")
    p.add_argument("--pause", type=float, default=0.5, help="cut pauses longer than this, seconds (default 0.5)")
    p.add_argument("--pad-in", type=float, default=0.12, help="kept before each spoken part, seconds (default 0.12)")
    p.add_argument("--pad-out", type=float, default=0.2, help="kept after each spoken part, seconds (default 0.2)")
    p.add_argument("--keep-retakes", action="store_true", help="don't remove repeated sentences")
    p.add_argument("--retake-similarity", type=float, default=0.7,
                   help="0-1, how similar a sentence must be to count as a retake (default 0.7)")
    p.add_argument("--dry-run", action="store_true", help="show the cuts, don't render")
    p.add_argument("--encoder", default="auto", help="auto (NVENC if available, else libx264), libx264, ...")
    p.add_argument("--quality", type=int, default=18, help="CRF/CQ, lower = better (default 18)")
    add_transcript_args(p)
    args = p.parse_args()

    if not os.path.isfile(args.video):
        die(f"File not found: {args.video}")
    need("ffmpeg", "ffprobe")
    fps, n_audio, duration = probe(args.video)
    if args.track >= n_audio:
        die(f"no audio track {args.track} (the file has {n_audio})")

    data = get_transcript(args, args.video)
    words = data["words"]
    if not words:
        die("No speech detected.")
    sep = word_sep(data)
    sentences = make_sentences(words, sep)

    dropped = [] if args.keep_retakes else \
        [sentences[i] for i in sorted(find_retakes(sentences, sep, args.retake_similarity))]
    kept_words = [w for w in words
                  if not any(d["start"] <= w["s"] and w["e"] <= d["end"] for d in dropped)]
    spans = keep_spans(kept_words, args.pause, args.pad_in, args.pad_out, duration, fps)
    new_len = sum(e - s for s, e in spans)

    hours = duration >= 3600
    report = [f"Source: {args.video}  ({clock(duration, hours)})",
              f"Result: {clock(new_len, hours)}  ({len(spans) - 1} cuts, "
              f"{duration - new_len:.0f}s removed, {len(dropped)} retakes)", ""]
    if dropped:
        report.append("Removed retakes:")
        report += [f"  [{clock(d['start'], hours)}] {d['text']}" for d in dropped]
        report.append("")
    report.append("Kept spans (source time, seconds):")
    report += [f"  {s:9.2f} - {e:9.2f}" for s, e in spans]
    for line in report[:3 + (len(dropped) + 2 if dropped else 0)]:
        log(line)

    if args.dry_run:
        return
    out = args.out or os.path.splitext(args.video)[0] + "_cut.mkv"
    if os.path.abspath(out) == os.path.abspath(args.video):
        die("output would overwrite the input")
    with open(os.path.splitext(out)[0] + ".cuts.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(report) + "\n")
    log(f"Rendering {len(spans)} parts...")
    render(args, spans, fps, n_audio, out)
    log(f"-> {out}")


if __name__ == "__main__":
    main()
