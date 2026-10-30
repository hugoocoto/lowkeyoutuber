#!/usr/bin/env python3
"""
subtitles.py - Generate YouTube-ready subtitles from a video, free and offline.

Uses faster-whisper (an optimized, open-source build of OpenAI's Whisper) and
runs entirely on your machine. No API key, no cost. The subtitles are printed
to stdout; progress and messages go to stderr, so redirecting is safe.

The transcript is cached in <video>.words.json and shared with clips.py,
meta.py and autocut.py, so Whisper only runs once per video.

SETUP (Arch Linux)
    sudo pacman -S ffmpeg
    python -m venv ~/.venvs/subs
    source ~/.venvs/subs/bin/activate
    pip install faster-whisper

USAGE
    subtitles.py video.mp4 > video.srt
    subtitles.py video.mp4 -l es > video.es.srt
    subtitles.py video.mp4 -m medium -f vtt > video.vtt
    subtitles.py raw.mkv -t 1 > raw.srt            # voice on the 2nd audio track
    subtitles.py video.mp4 --prompt "Arch Linux, PipeWire, OBS, Blue Yeti" > video.srt

Then upload the file in YouTube Studio: Subtitles > Add language > Upload file.
YouTube accepts both .srt and .vtt.

MODELS (bigger = more accurate and slower; downloaded once, then cached)
    small (~500 MB), medium (~1.5 GB), large-v3-turbo (default, ~1.6 GB),
    large-v3 (~3 GB). Runs on the NVIDIA GPU (CUDA) when available, in
    int8_float16 so it fits in 4 GB of VRAM; falls back to the CPU otherwise.
    For GPU support, in the venv: pip install nvidia-cublas-cu12 'nvidia-cudnn-cu12==9.*'
    On CPU only, use -m small or -m medium.

Always skim the result before publishing: automatic transcription still makes
mistakes with names, jargon and accents.
"""

import argparse
import os
import sys
from dataclasses import dataclass

from yt_common import SENTENCE_END, add_transcript_args, die, get_transcript, word_sep

MIN_CUE_SECONDS = 0.4
MAX_CUE_SECONDS = 6.0
MAX_GAP_SECONDS = 1.0       # a pause longer than this starts a new cue
MIN_CHARS_AT_SENTENCE = 20  # don't cut a cue at a period if it's still tiny


@dataclass
class Cue:
    start: float
    end: float
    text: str


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
def fmt_time(seconds: float, sep: str) -> str:
    ms = round(max(seconds, 0) * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02}{sep}{ms:03}"


def wrap_two_lines(text: str, line_width: int) -> str:
    """Break text into at most two balanced lines (YouTube convention)."""
    if len(text) <= line_width:
        return text
    mid = len(text) // 2
    candidates = [i for i in (text.rfind(" ", 0, mid + 1), text.find(" ", mid)) if i != -1]
    if not candidates:
        return text
    i = min(candidates, key=lambda k: abs(k - mid))
    return text[:i] + "\n" + text[i + 1:]


def render(cues: list[Cue], fmt: str, line_width: int) -> str:
    sep = "," if fmt == "srt" else "."
    out = ["WEBVTT\n"] if fmt == "vtt" else []
    for n, c in enumerate(cues, 1):
        header = f"{fmt_time(c.start, sep)} --> {fmt_time(c.end, sep)}"
        body = wrap_two_lines(c.text, line_width)
        out.append(f"{n}\n{header}\n{body}\n" if fmt == "srt" else f"{header}\n{body}\n")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Cue building: split the words into short, readable subtitle blocks
# ---------------------------------------------------------------------------
def build_cues(words, max_chars: int, sep: str) -> list[Cue]:
    cues: list[Cue] = []
    cur: list[dict] = []

    def length() -> int:
        return len(sep.join(w["w"] for w in cur))

    def flush():
        if cur:
            cues.append(Cue(cur[0]["s"], cur[-1]["e"], sep.join(w["w"] for w in cur)))
            cur.clear()

    for w in words:
        if cur and (
            length() + len(sep) + len(w["w"]) > max_chars
            or w["e"] - cur[0]["s"] > MAX_CUE_SECONDS
            or w["s"] - cur[-1]["e"] > MAX_GAP_SECONDS
        ):
            flush()
        cur.append(w)
        if w["w"].endswith(SENTENCE_END) and length() >= MIN_CHARS_AT_SENTENCE:
            flush()
    flush()

    # Clean timing: no overlaps, no flash-cues
    for i, c in enumerate(cues):
        if c.end - c.start < MIN_CUE_SECONDS:
            c.end = c.start + MIN_CUE_SECONDS
        if i + 1 < len(cues) and c.end > cues[i + 1].start:
            c.end = max(cues[i + 1].start, c.start + 0.1)
    return cues


def main():
    p = argparse.ArgumentParser(description="Generate SRT/VTT subtitles from a video using Whisper (free, local).")
    p.add_argument("video", help="video or audio file (mp4, mkv, mov, wav, ...)")
    p.add_argument("-f", "--format", choices=["srt", "vtt"], default="srt", help="output format (default: srt)")
    p.add_argument("--line-width", type=int, default=42, help="max characters per subtitle line (default: 42)")
    add_transcript_args(p)
    args = p.parse_args()

    if not os.path.isfile(args.video):
        die(f"File not found: {args.video}")

    data = get_transcript(args, args.video)
    cues = build_cues(data["words"], max_chars=args.line_width * 2, sep=word_sep(data))
    if not cues:
        die("No speech detected.")

    sys.stdout.buffer.write(render(cues, args.format, args.line_width).encode("utf-8"))


if __name__ == "__main__":
    main()
