#!/usr/bin/env python3
"""
clips.py - Turn a long video into vertical (9:16) short clips with big
word-by-word captions. Free and local, unless you use an AI CLI you already pay for.

PIPELINE
    1. Transcribe with faster-whisper on the GPU (word timestamps, cached in
       <video>.words.json and shared with subtitles.py and meta.py).
    2. An LLM (Claude Code/Codex/OpenCode if installed, else local Ollama)
       reads the transcript and proposes the best
       self-contained moments. Times are snapped to sentence boundaries so
       clips never start or end mid-sentence.
    3. You review the proposals interactively (pick, drop, adjust times).
       They are saved in clips.json; --pick renders some of them later
       without asking the LLM again (it never proposes the same clips twice).
    4. ffmpeg cuts each clip, rearranges it to 1080x1920 and burns in
       TikTok-style captions (the current word is highlighted).
    Optionally it also writes a short summary of the whole video.

SETUP (Arch Linux)
    sudo pacman -S ffmpeg ttf-dejavu ollama-cuda
    sudo systemctl enable --now ollama
    ollama pull qwen2.5:7b                       # only used without claude/codex/opencode
    python -m venv ~/.venvs/subs && source ~/.venvs/subs/bin/activate
    pip install faster-whisper nvidia-cublas-cu12 'nvidia-cudnn-cu12==9.*'
    Clips are encoded with NVENC on the NVIDIA GPU (--encoder libx264 for CPU).

USAGE
    clips.py video.mp4                           # 5 clips of 30-60 s, interactive
    clips.py video.mp4 --count 8 --min 20 --max 45
    clips.py video.mp4 -l es --summary           # Spanish video + summary
    clips.py video.mp4 --layout screen           # screen only, no facecam
    clips.py video.mp4 --preview                 # one test frame, to check the layout
    clips.py video.mp4 --yes                     # no questions, render everything
    clips.py video.mp4 --pick 1,3                # render clips 1 and 3 of the last run's clips.json

LAYOUTS (1080x1920)
    stack   blurred background, screen recording on top, facecam below,
            captions in the lower part   (default, for screen + small facecam)
    screen  blurred background, screen recording centered
    cam     only the facecam region, zoomed to fill (low quality if the
            facecam is small)

FACECAM LOCATION
    Default: bottom-right corner, 25% of the width/height of the video.
    --cam-corner tl|tr|bl|br and --cam-size 0.2 adjust it, or give exact
    source pixels with --cam x:y:w:h. Use --preview to check it.

The summary is printed to stdout; progress and questions go to stderr.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error

from yt_common import (CONFIG, SENTENCE_END, add_llm_args, add_transcript_args, check_cam, chunk_transcript, die,
                       get_transcript, lang_name, llm, log, make_sentences, need, num, parse_json, setup_llm,
                       summarize, video_encoder, word_sep)

W, H = 1080, 1920
PAD_START, PAD_END = 0.2, 0.3   # breathing room around each clip, in seconds


def probe(video):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration", "-of", "json", video],
        capture_output=True, text=True)
    if r.returncode:
        die("ffprobe failed: " + r.stderr.strip())
    d = json.loads(r.stdout)
    return d["streams"][0]["width"], d["streams"][0]["height"], float(d["format"]["duration"])


# ---------------------------------------------------------------------------
# LLM: clip picking
# ---------------------------------------------------------------------------
CLIP_PROMPT = """You select short-form video clips (YouTube Shorts / TikTok / Reels) from a video transcript.
Each transcript line starts with its start time in seconds.

Pick up to @K@ self-contained moments, each between @MIN@ and @MAX@ seconds long, that work on their own:
a strong hook in the first sentence, one clear idea, a satisfying ending.
Prefer concrete tips, surprising claims, demos and funny moments.
Avoid intros, outros, sponsor reads and moments that depend on earlier context.

Return ONLY JSON in exactly this shape:
{"clips":[{"start":<seconds>,"end":<seconds>,"title":"<max 60 characters>","summary":"<one sentence>","score":<1-10>}]}
Write title and summary in @LANG@.

TRANSCRIPT:
@TRANSCRIPT@
"""


def fit_clip(sentences, start, end, lo, hi):
    """Snap a proposed range to sentence boundaries and enforce min/max length."""
    if not sentences or end - start < lo * 0.5:   # far too short: extending would change its meaning
        return None
    i = min(range(len(sentences)), key=lambda k: abs(sentences[k]["start"] - start))
    j = min(range(len(sentences)), key=lambda k: abs(sentences[k]["end"] - end))
    j = max(j, i)
    length = lambda: sentences[j]["end"] - sentences[i]["start"]
    while j > i and length() > hi:
        j -= 1
    while j + 1 < len(sentences) and length() < lo:
        j += 1
    if not (lo * 0.8 <= length() <= hi):
        return None
    return sentences[i]["start"], sentences[j]["end"]


def overlap_ratio(a, b):
    inter = max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    return inter / max(min(a["end"] - a["start"], b["end"] - b["start"]), 0.001)


def pick_clips(args, sentences):
    chunks = chunk_transcript(sentences, args.chunk_chars)
    cands = []
    for n, chunk in enumerate(chunks, 1):
        log(f"Asking {args.backend} about part {n}/{len(chunks)}...")
        prompt = (CLIP_PROMPT.replace("@LANG@", args.lang).replace("@K@", str(args.count)).replace("@MIN@", str(int(args.min)))
                  .replace("@MAX@", str(int(args.max))).replace("@TRANSCRIPT@", chunk))
        try:
            items = parse_json(llm(args, prompt, json_mode=True)).get("clips", [])
        except (json.JSONDecodeError, KeyError, urllib.error.URLError, OSError, AttributeError):
            log("  unusable answer, skipping this part")
            continue
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict):
                continue
            fit = fit_clip(sentences, num(it.get("start"), 0), num(it.get("end"), 0), args.min, args.max)
            if fit:
                cands.append({"start": fit[0], "end": fit[1],
                              "title": str(it.get("title", "")).strip()[:80] or "clip",
                              "summary": str(it.get("summary", "")).strip(),
                              "score": num(it.get("score"), 5)})
    cands.sort(key=lambda c: -c["score"])
    chosen = []
    for c in cands:
        if all(overlap_ratio(c, o) < 0.3 for o in chosen):
            chosen.append(c)
        if len(chosen) >= args.count:
            break
    chosen.sort(key=lambda c: c["start"])
    return chosen


# ---------------------------------------------------------------------------
# Interactive review
# ---------------------------------------------------------------------------
def mmss(t):
    return f"{int(t) // 60}:{int(t) % 60:02d}"


def review(clips, duration):
    while True:
        log("\nProposed clips:")
        for n, c in enumerate(clips, 1):
            log(f"  {n}. [{mmss(c['start'])}-{mmss(c['end'])}, {c['end'] - c['start']:.0f}s, "
                f"score {c['score']:g}] {c['title']}")
            if c["summary"]:
                log(f"       {c['summary']}")
        ans = input("\nRender which? [Enter = all | 1,3 = pick | e N = edit times | q = quit]: ").strip().lower()
        if ans in ("", "all", "a"):
            return clips
        if ans in ("q", "quit"):
            return []
        m = re.fullmatch(r"e\s*(\d+)", ans)
        if m and 1 <= int(m.group(1)) <= len(clips):
            c = clips[int(m.group(1)) - 1]
            s = input(f"  start seconds [{c['start']:.1f}]: ").strip()
            e = input(f"  end seconds   [{c['end']:.1f}]: ").strip()
            start, end = num(s, c["start"]), num(e, c["end"])
            if not (0 <= start < end <= duration) or end - start < 1:
                log(f"  Invalid times: need 0 <= start < end <= {duration:.1f} and at least 1 s long.")
                continue
            c["start"], c["end"] = start, end
            continue
        try:
            picks = [int(x) for x in re.split(r"[,\s]+", ans) if x]
            if picks and all(1 <= p <= len(clips) for p in picks):
                return [clips[p - 1] for p in picks]
        except ValueError:
            pass
        log("  Didn't understand that.")


# ---------------------------------------------------------------------------
# Captions (ASS) and layout
# ---------------------------------------------------------------------------
def ass_time(t):
    cs = round(max(t, 0) * 100)
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def ass_color(rgb):
    rgb = rgb.lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", rgb):
        die("--highlight must be a hex color like FFD700")
    r, g, b = rgb[0:2], rgb[2:4], rgb[4:6]
    return f"&H00{b}{g}{r}&"


def caption_groups(words, per_group, sep):
    groups, cur = [], []
    for w in words:
        if cur and (len(cur) >= per_group or w["s"] - cur[-1]["e"] > 0.6
                    or len(sep.join(x["w"] for x in cur)) + len(w["w"]) > 24):
            groups.append(cur)
            cur = []
        cur.append(w)
        if w["w"].endswith(SENTENCE_END):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def build_ass(args, words, start, end, sep):
    hl = ass_color(args.highlight)
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{args.font},{args.font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,8,3,{args.caption_align},60,60,{args.margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    inside = [w for w in words if w["s"] >= start - 0.05 and w["e"] <= end + 0.05]
    lines = []
    for grp in caption_groups(inside, args.words_per_caption, sep):
        texts = [re.sub(r"[{}\\]", "", x["w"]).upper() for x in grp]
        for k, w in enumerate(grp):
            t0 = w["s"] - start
            t1 = (grp[k + 1]["s"] if k + 1 < len(grp) else w["e"] + 0.15) - start
            t1 = max(t1, t0 + 0.08)
            shown = sep.join(
                (r"{\c" + hl + r"\fscx112\fscy112}" + t + r"{\r}") if i == k else t
                for i, t in enumerate(texts))
            lines.append(f"Dialogue: 0,{ass_time(t0)},{ass_time(t1)},Default,,0,0,0,,{shown}")
    return head + "\n".join(lines) + "\n"


def cam_bottom(args, src_w, src_h):
    """Lowest pixel of the facecam in the stack layout. Captions start below it, growing
    downwards, so a two-line caption never covers the face."""
    m = re.fullmatch(r"(\d+):(\d+):(\d+):(\d+)", args.cam or "")
    aspect = int(m.group(4)) / int(m.group(3)) if m else src_h / src_w
    return min(args.cam_y + round(args.cam_width * aspect), H - 2 * args.font_size - 120)


def cam_crop(args):
    if args.cam:
        m = re.fullmatch(r"(\d+):(\d+):(\d+):(\d+)", args.cam)
        if not m:
            die("--cam must look like x:y:w:h (source pixels)")
        x, y, w, h = m.groups()
        return f"crop={w}:{h}:{x}:{y}"
    s = args.cam_size
    xe = {"l": "0", "r": "iw-ow"}[args.cam_corner[1]]
    ye = {"t": "0", "b": "ih-oh"}[args.cam_corner[0]]
    return f"crop=iw*{s}:ih*{s}:{xe}:{ye}"


def screen_part(args, label):
    """Filters for the screen recording. With an exact --cam box, the facecam inside
    it is blurred, since the stack layout already shows it big below."""
    if not args.cam or args.keep_cam_in_screen or args.layout == "cam":
        return f"[{label}]scale={W}:-2[scr];"
    x, y = args.cam.split(":")[:2]
    return (f"[{label}]split=2[s1][s2];[s2]{cam_crop(args)},boxblur=20:2,eq=brightness=-0.25[hid];"
            f"[s1][hid]overlay={x}:{y},scale={W}:-2[scr];")


def layout(args):
    """Return (filtergraph prefix, label of the final video stream)."""
    # blur a quarter-size copy and scale it back up: looks the same, ~16x cheaper
    bg = (f"scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,crop={W // 4}:{H // 4},"
          f"boxblur=8:2,eq=brightness=-0.12,scale={W}:{H}")
    if args.layout == "stack":
        return (f"[0:v]split=3[a][b][c];[a]{bg}[bg];{screen_part(args, 'b')}"
                f"[c]{cam_crop(args)},scale={args.cam_width}:-2[cam];"
                f"[bg][scr]overlay=0:{args.screen_y}[t1];"
                f"[t1][cam]overlay=(W-w)/2:{args.cam_y}[t2];"), "t2"
    if args.layout == "screen":
        return (f"[0:v]split=2[a][b];[a]{bg}[bg];{screen_part(args, 'b')}"
                "[bg][scr]overlay=0:(H-h)/2[t2];"), "t2"
    return (f"[0:v]{cam_crop(args)},scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H}[t2];"), "t2"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def slug(text):
    return re.sub(r"[^\w]+", "-", text.lower()).strip("-")[:40] or "clip"


def render(args, video, clip, index, words, sep, outdir, preview=False):
    start = max(clip["start"] - PAD_START, 0)
    end = min(clip["end"] + PAD_END, args.duration)
    if preview:   # land on a spoken word so the caption style is visible
        target = start + min(5.0, (end - start) / 2)
        word = next((w for w in words if w["s"] >= target), None)
        start = (word["s"] + 0.05) if word else target
        end = start + 1
    dur = end - start
    out = os.path.abspath(os.path.join(outdir, "preview.jpg" if preview
                                       else f"{index:02d}_{slug(clip['title'])}.mp4"))

    with tempfile.TemporaryDirectory() as tmp:
        graph, last = layout(args)
        if args.no_captions:
            graph += f"[{last}]null[v]"
        else:
            with open(os.path.join(tmp, "caps.ass"), "w", encoding="utf-8") as f:
                f.write(build_ass(args, words, start, end, sep))
            graph += f"[{last}]ass=caps.ass[v]"   # relative path: ffmpeg runs inside tmp

        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", os.path.abspath(video),
               "-filter_complex", graph, "-map", "[v]"]
        if preview:
            cmd += ["-frames:v", "1", "-an", out]
        else:
            cmd += ["-map", f"0:a:{args.track}?"] + args.venc + [
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
        r = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True)
        if r.returncode:
            log(r.stderr.strip()[-800:])
            raise RuntimeError("ffmpeg failed")
    return out


def save_clips(path, clips):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(clips, f, ensure_ascii=False, indent=2)


def main():
    p = argparse.ArgumentParser(description="Make vertical short clips with word-by-word captions (free, local).")
    p.add_argument("video")
    g = p.add_argument_group("clip selection")
    g.add_argument("-n", "--count", type=int, default=5, help="number of clips (default 5)")
    g.add_argument("--min", type=float, default=30, help="minimum clip length in seconds (default 30)")
    g.add_argument("--max", type=float, default=60, help="maximum clip length in seconds (default 60)")
    g.add_argument("--summary", action="store_true", help="also summarize the whole video (printed to stdout)")
    g.add_argument("--no-clips", action="store_true", help="skip clips (use with --summary)")
    g.add_argument("-y", "--yes", action="store_true", help="don't ask, render all proposed clips")
    g.add_argument("--preview", action="store_true", help="render one test frame of the first clip and stop")
    g.add_argument("--pick", metavar="N,N",
                   help="render these clips from clips.json in the output folder (numbers as listed there), "
                        "without asking the LLM again")
    add_transcript_args(p)   # -t/--track is also the audio track put in the clips
    add_llm_args(p)
    v = p.add_argument_group("video layout")
    v.add_argument("--layout", choices=["stack", "screen", "cam"], default="stack")
    v.add_argument("--cam", default=CONFIG.get("CAM") or None,
                   help="facecam region in source pixels, x:y:w:h (default: config CAM)")
    v.add_argument("--keep-cam-in-screen", action="store_true",
                   help="don't blur the facecam inside the screen recording (it's blurred when --cam is set)")
    v.add_argument("--cam-corner", choices=["tl", "tr", "bl", "br"], default="br")
    v.add_argument("--cam-size", type=float, default=0.25, help="facecam fraction of the frame (default 0.25)")
    v.add_argument("--cam-width", type=int, default=800, help="facecam width in the output (default 800)")
    v.add_argument("--screen-y", type=int, default=180, help="top of the screen recording (default 180)")
    v.add_argument("--cam-y", type=int, default=860, help="top of the facecam (default 860)")
    v.add_argument("--encoder", default="auto", help="auto (NVENC if available, else libx264), h264_nvenc, libx264...")
    c = p.add_argument_group("captions")
    c.add_argument("--no-captions", action="store_true")
    c.add_argument("--words-per-caption", type=int, default=3)
    c.add_argument("--font", default="DejaVu Sans")
    c.add_argument("--font-size", type=int, default=96)
    c.add_argument("--highlight", default="FFD700", help="highlight color, hex RGB (default FFD700)")
    c.add_argument("--margin-v", type=int,
                   help="caption distance from the bottom, in pixels (default: right under the facecam in "
                        "'stack', else 330)")
    c.add_argument("-o", "--out", help="output directory (default: <video>_clips)")
    args = p.parse_args()

    if not os.path.isfile(args.video):
        die(f"File not found: {args.video}")
    if args.min >= args.max:
        die("--min must be smaller than --max")
    need("ffmpeg", "ffprobe")

    src_w, src_h, args.duration = probe(args.video)
    args.caption_align = 2   # ASS: bottom center, MarginV = distance from the bottom
    if args.margin_v is None:
        if args.layout == "stack":
            args.caption_align, args.margin_v = 8, cam_bottom(args, src_w, src_h) + 40   # top center
        else:
            args.margin_v = 330
    args.venc = video_encoder(args.encoder, 21)
    outdir = args.out or os.path.splitext(args.video)[0] + "_clips"
    os.makedirs(outdir, exist_ok=True)
    if args.cam and args.layout != "screen" and re.fullmatch(r"\d+:\d+:\d+:\d+", args.cam):
        check_cam(args.video, [int(v) for v in args.cam.split(":")], outdir)
    data = get_transcript(args, args.video)
    words = data["words"]
    if not words:
        die("No speech detected.")
    sep = word_sep(data)
    args.lang = lang_name(data.get("language"))
    sentences = make_sentences(words, sep)

    saved = os.path.join(outdir, "clips.json")
    if args.pick:
        if not os.path.isfile(saved):
            die(f"--pick needs {saved} from an earlier run")
        with open(saved, encoding="utf-8") as f:
            clips = json.load(f)
        try:
            nums = [int(x) for x in re.split(r"[,\s]+", args.pick) if x]
        except ValueError:
            die("--pick takes clip numbers, e.g. --pick 1,3")
        if not nums or not all(1 <= n <= len(clips) for n in nums):
            die(f"--pick: clips.json has clips 1-{len(clips)}")
        args.yes = True

    if args.summary or (not args.no_clips and not args.pick):
        setup_llm(args)

    if args.summary:
        text = summarize(args, sentences, args.lang)
        with open(os.path.join(outdir, "summary.txt"), "w", encoding="utf-8") as f:
            f.write(text + "\n")
        sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    if args.no_clips:
        return

    if args.pick:
        chosen = [clips[n - 1] for n in nums]
    else:
        clips = pick_clips(args, sentences)
        if not clips:
            die("The LLM proposed no usable clips. Try a bigger --llm, a wider --min/--max, or check the transcript.")
        save_clips(saved, clips)
        chosen = clips
        if not args.yes and not args.preview and sys.stdin.isatty():
            chosen = review(clips, args.duration)   # edits times in place, so save again
            save_clips(saved, clips)
            if not chosen:
                log("Nothing to render.")
                return

    if args.preview:
        try:
            log(f"Preview frame: {render(args, args.video, chosen[0], 1, words, sep, outdir, preview=True)}")
        except RuntimeError:
            die("could not render the preview")
        return

    failed = 0
    for n, c in enumerate(chosen, 1):
        num_ = clips.index(c) + 1   # file names keep the number from clips.json
        log(f"Rendering {n}/{len(chosen)}: {c['title']} ({c['end'] - c['start']:.0f}s)")
        try:
            out = render(args, args.video, c, num_, words, sep, outdir)
            c["file"] = os.path.basename(out)
            save_clips(saved, clips)   # upload.py --shorts reads titles from here
            log(f"   -> {out}")
        except RuntimeError:
            log("   failed, skipping this clip")
            failed += 1
    log(f"\nDone. Clips and clips.json are in: {outdir}")
    if failed:
        die(f"{failed} clip(s) failed to render")


if __name__ == "__main__":
    main()
