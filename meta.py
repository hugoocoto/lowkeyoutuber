#!/usr/bin/env python3
"""
meta.py - Write the YouTube metadata for a video from its transcript, with a
LLM (Claude Code/Codex/OpenCode if installed, else local Ollama): chapters,
5 title options, description, tags and thumbnail texts.

OUTPUT (in --out, default: next to the video)
    chapters.txt     0:00 Intro / 1:23 ... (YouTube needs >= 3, each >= 10 s)
    titles.txt       5 options, one per line. The FIRST line is the title
                     upload.py uses, so reorder or rewrite it.
    description.txt  description + chapters + your --footer + hashtags
    tags.txt         comma-separated, within YouTube's 500-character limit
    summary.txt      plain summary of the video
    thumbnail_texts.txt  3 short texts for thumbnail.py, one per line

All files are plain text: edit them before uploading. The transcript is
shared with subtitles.py and clips.py (<video>.words.json).

USAGE
    meta.py video.mp4
    meta.py video.mp4 -o out/ --footer links.txt
    meta.py video.mp4 --llm qwen2.5:14b          # better writing, slower

SETUP: same as clips.py. Defaults (footer, language...) come from
~/.config/lowkeyoutuber/config, see yt_common.py.
"""

import argparse
import json
import os
import re
import urllib.error

from yt_common import (CONFIG, add_llm_args, add_transcript_args, chunk_transcript, clock, die, get_transcript,
                       lang_name, llm, log, make_sentences, num, parse_json, setup_llm, summarize, word_sep)

CHAPTER_PROMPT = """You split a video transcript into YouTube chapters.
Each transcript line starts with its start time in seconds.
This part covers minutes @FROM@ to @TO@ of the video.

Mark only the points where a clearly new topic or step begins, about one every 2-5 minutes
of content (fewer if the topic does not change). Chapter titles: 2-6 words, specific,
no numbering, no emojis. Write the titles in @LANG@.

Return ONLY JSON: {"chapters":[{"start":<seconds>,"title":"<title>"}]}

TRANSCRIPT:
@TRANSCRIPT@
"""

META_PROMPT = """You write YouTube metadata for a video, given its summary and chapters.
Write everything in @LANG@ (titles, description, tags and hashtags), even if parts of
the summary are in another language.

Return ONLY JSON in exactly this shape:
{"titles":["...","...","...","...","..."],"description":"...","tags":["..."],"hashtags":["#...","#...","#..."],
 "thumbnail_texts":["...","...","..."]}

- titles: 5 different options, each under 60 characters. Specific and curiosity-driven,
  honest (no promises the video doesn't keep), no ALL CAPS, no emojis.
- description: 2 short paragraphs. The first 150 characters must work alone in search
  results and say what the viewer gets. No links, no chapter list, no hashtags.
- tags: 10-15 search phrases people would actually type, lowercase.
- hashtags: 3 relevant hashtags.
- thumbnail_texts: 3 different texts for the thumbnail image, 2-4 punchy words each (max 22
  characters), that add curiosity to the title instead of repeating it.

SUMMARY:
@SUMMARY@

CHAPTERS:
@CHAPTERS@
"""


CHAPTERS_HEADING = {"es": "Capítulos", "fr": "Chapitres", "de": "Kapitel", "it": "Capitoli",
                    "pt": "Capítulos", "ca": "Capítols", "gl": "Capítulos"}


def nearest_sentence_start(sentences, t):
    return min((s["start"] for s in sentences), key=lambda s: abs(s - t))


def make_chapters(args, sentences, duration):
    cands = []
    for n, chunk in enumerate(chunk_transcript(sentences, args.chunk_chars), 1):
        times = [float(m) for m in re.findall(r"^\[([\d.]+)\]", chunk, re.M)]
        log(f"Finding chapters, part {n}...")
        prompt = (CHAPTER_PROMPT.replace("@LANG@", args.lang).replace("@FROM@", str(int(times[0] // 60))).replace("@TO@", str(int(times[-1] // 60) + 1))
                  .replace("@TRANSCRIPT@", chunk))
        try:
            items = parse_json(llm(args, prompt, json_mode=True)).get("chapters", [])
        except (json.JSONDecodeError, urllib.error.URLError, OSError, AttributeError):
            log("  unusable answer, skipping this part")
            continue
        for it in items if isinstance(items, list) else []:
            title = re.sub(r"[<>]", "", str(it.get("title", "")) if isinstance(it, dict) else "").strip()
            t = num(it.get("start"), -1) if title else -1
            if 0 <= t < duration:
                cands.append({"start": nearest_sentence_start(sentences, t), "title": title[:60]})
    cands.sort(key=lambda c: c["start"])
    if not cands:
        return []

    # YouTube rules: first chapter at 0:00, every chapter at least 10 s long
    if cands[0]["start"] < args.min_chapter:
        cands[0]["start"] = 0
    else:
        cands.insert(0, {"start": 0, "title": "Intro"})
    chapters = []
    for c in cands:
        if not chapters or c["start"] - chapters[-1]["start"] >= args.min_chapter:
            chapters.append(c)
    while len(chapters) > 1 and duration - chapters[-1]["start"] < args.min_chapter:
        chapters.pop()
    return chapters


def fit_tags(tags, limit=500):
    """YouTube counts tag characters plus commas, and quotes around tags with spaces."""
    out, used = [], 0
    for t in tags:
        t = re.sub(r"[<>,]", "", str(t)).strip()
        if not t or t.lower() in (o.lower() for o in out):
            continue
        cost = len(t) + (2 if " " in t else 0) + (1 if out else 0)
        if used + cost > limit:
            break
        out.append(t)
        used += cost
    return out


def main():
    p = argparse.ArgumentParser(description="Chapters, titles, description and tags from a video (free, local).")
    p.add_argument("video")
    p.add_argument("-o", "--out", help="output folder (default: next to the video)")
    p.add_argument("--footer", default=CONFIG.get("FOOTER") or None,
                   help="text file appended to the description: links, socials, gear (default: config FOOTER)")
    p.add_argument("--min-chapter", type=float, default=30, help="minimum chapter length, seconds (default 30)")
    add_transcript_args(p)
    add_llm_args(p)
    args = p.parse_args()

    if not os.path.isfile(args.video):
        die(f"File not found: {args.video}")
    if args.min_chapter < 10:
        die("--min-chapter must be at least 10 (YouTube's minimum)")
    footer = ""
    if args.footer and not os.path.isfile(args.footer):
        log(f"Footer file not found, skipping it: {args.footer}")
    elif args.footer:
        with open(args.footer, encoding="utf-8") as f:
            footer = f.read().strip()
    outdir = args.out or os.path.dirname(os.path.abspath(args.video))
    os.makedirs(outdir, exist_ok=True)

    data = get_transcript(args, args.video)
    if not data["words"]:
        die("No speech detected.")
    sentences = make_sentences(data["words"], word_sep(data))
    duration = data["duration"]
    setup_llm(args)

    args.lang = lang_name(data.get("language"))
    summary = summarize(args, sentences, args.lang)
    chapters = make_chapters(args, sentences, duration)
    hours = duration >= 3600
    chapter_text = "\n".join(f"{clock(c['start'], hours)} {c['title']}" for c in chapters)
    if len(chapters) < 3:
        log(f"Only {len(chapters)} chapter(s) found: YouTube needs at least 3, leaving them out of the description.")

    log("Writing titles, description and tags...")
    prompt = (META_PROMPT.replace("@LANG@", args.lang).replace("@SUMMARY@", summary)
              .replace("@CHAPTERS@", chapter_text or "(none)"))
    try:
        meta = parse_json(llm(args, prompt, json_mode=True))
    except (json.JSONDecodeError, urllib.error.URLError, OSError) as e:
        die(f"the LLM gave no usable metadata ({e}). Try again or use a bigger --llm.")

    clean = lambda s: re.sub(r"[<>]", "", str(s)).strip()   # YouTube rejects < and >
    titles = [clean(t)[:100] for t in meta.get("titles", []) if clean(t)]
    tags = fit_tags(meta.get("tags", []))
    hashtags = [h if h.startswith("#") else "#" + h
                for h in (re.sub(r"[\s<>]", "", str(h)) for h in meta.get("hashtags", [])) if h.strip("#")][:3]

    parts = [clean(meta.get("description", "")) or summary.split("\n")[0]]
    if len(chapters) >= 3:
        parts.append(CHAPTERS_HEADING.get(data.get("language"), "Chapters") + ":\n" + chapter_text)
    if footer:
        parts.append(footer)
    if hashtags:
        parts.append(" ".join(hashtags))
    description = "\n\n".join(parts)
    if len(description.encode("utf-8")) > 5000:
        log("Warning: description is over YouTube's 5000-byte limit, shorten it before uploading.")

    thumb_texts = [clean(t)[:30] for t in meta.get("thumbnail_texts", []) if clean(t)]
    files = {"summary.txt": summary, "chapters.txt": chapter_text, "titles.txt": "\n".join(titles),
             "description.txt": description, "tags.txt": ", ".join(tags),
             "thumbnail_texts.txt": "\n".join(thumb_texts)}
    for name, text in files.items():
        with open(os.path.join(outdir, name), "w", encoding="utf-8") as f:
            f.write(text + "\n")

    log(f"\nTitle options:\n  " + "\n  ".join(titles or ["(none, write titles.txt yourself)"]))
    log(f"\nChapters:\n{chapter_text or '(none)'}")
    log(f"\nWritten to {outdir}: " + ", ".join(files))


if __name__ == "__main__":
    main()
