#!/usr/bin/env python3
"""
render.py - Render a project's timeline (project.json) to video. The only tool that
encodes a final video: flows edit the timeline, this turns it into MP4.

WHAT A TIMELINE CAN HOLD (docs/project.md has the full format)
    video   recordings (in/out), images and generated scenes (dur), solid colours (dur),
            each with optional zooms (eased in and out) and a fit (pad, blur, fill)
    audio   narration, takes, music, placed at a time; music with "duck": true goes
            down under everything else (sidechain, like process-voice.sh -m)
    captions  word timings from the media (TTS lines, transcribed recordings): always
            written to out/video.srt; burned into the picture with "burn": true
            (word-highlight style: the current word is highlighted, as in clips.py)

    Audio is mixed first and normalized to -14 LUFS / -1 dBTP (two passes, like
    process-voice.sh), then the video is rendered in one ffmpeg pass with NVENC
    (libx264 when there's no NVIDIA GPU).

OUTPUT (in out/)
    video.mp4         the first format in project.json ("16:9" usually)
    video_9x16.mp4    the other formats, one file each
    video.srt         subtitles from the timeline's words
    Rendering is skipped when the timeline and its media haven't changed (-f to force).

USAGE
    render.py -P project/                    # every format
    render.py -P project/ --format 9:16      # one format
    render.py -P project/ --preview          # half size, fast: out/preview.mp4
    render.py -P project/ --frames 1.5,12    # also save stills to checks/frames/ (auto = 6 evenly spaced)
    render.py -P project/ --json             # summary on stdout for agents
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

import backends
import project as proj
from yt_common import CONFIG, SENTENCE_END, die, log, need, video_encoder

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
RAMP = 0.45          # zoom in/out time, seconds
MUSIC_DB = -22.0     # default gain of ducked tracks, as in process-voice.sh
TARGET_LUFS, TARGET_TP, TARGET_LRA = -14, -1, 11


class RenderError(RuntimeError):
    pass


def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise RenderError("ffmpeg failed:\n" + r.stderr.strip()[-1500:])
    return r


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_type,width,height:format=duration", "-of", "json", path],
                       capture_output=True, text=True)
    if r.returncode:
        raise RenderError(f"can't read {path}: {r.stderr.strip()[-300:]}")
    d = json.loads(r.stdout)
    v = next((s for s in d.get("streams", []) if s["codec_type"] == "video"), None)
    try:
        dur = float(d.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        dur = None
    return {"w": v and v.get("width"), "h": v and v.get("height"), "dur": dur,
            "n_audio": sum(s["codec_type"] == "audio" for s in d.get("streams", []))}


# ---------------------------------------------------------------------------
# Timeline -> concrete items
# ---------------------------------------------------------------------------
def source(path, pj, it, fmt=None):
    """(file, media entry or {}) for a timeline item's src. A media entry can have one file per
    format ("variants": {"9:16": "scenes/x_9x16.mp4"}), e.g. scenes rendered at each size."""
    src = it.get("src")
    m = pj.get("media", {}).get(src)
    if m:
        f = (m.get("variants") or {}).get(fmt) or m["file"]
        return proj.resolve(path, f), m
    return proj.resolve(path, src), {}


def load_words(path, m):
    if not m.get("words"):
        return []
    try:
        with open(proj.resolve(path, m["words"]), encoding="utf-8") as f:
            return json.load(f).get("words", [])
    except (OSError, ValueError):
        return []


def plan(path, pj, fmt=None):
    """Video items with absolute start times, audio items, total length, timeline words."""
    vids, t, words = [], 0.0, []
    for n, it in enumerate(pj["timeline"].get("video", []), 1):
        entry = dict(it, n=n, start=t)
        if "color" in it and "src" not in it:
            entry.update(kind="color", file=None, d=float(it["dur"]))
        else:
            f, m = source(path, pj, it, fmt)
            if not os.path.isfile(f):
                raise RenderError(f"timeline.video[{n}]: file not found: {f}")
            info = probe(f)
            image = f.lower().endswith(IMAGE_EXT) or m.get("kind") == "image"
            a = float(it.get("in", 0))
            d = float(it["out"]) - a if "out" in it else float(it["dur"])
            if not image and info["dur"] and a + d > info["dur"] + 0.05:
                raise RenderError(f"timeline.video[{n}]: out {a + d:.2f}s is past the end of {f} "
                                  f"({info['dur']:.2f}s)")
            entry.update(kind="image" if image else "video", file=f, info=info, d=d, a=a,
                         track=int(m.get("track", it.get("track", 0))))
            if not image and it.get("audio", True) and info["n_audio"] > entry["track"]:
                entry["audio_track"] = entry["track"]
            for w in load_words(path, m):
                if a - 0.05 <= w["s"] and w["e"] <= a + d + 0.05:
                    words.append({"w": w["w"], "s": t + w["s"] - a, "e": t + w["e"] - a})
        check_zooms(entry)
        vids.append(entry)
        t += entry["d"]
    total = t
    auds = []
    for n, it in enumerate(pj["timeline"].get("audio", []), 1):
        f, m = source(path, pj, it)
        if not os.path.isfile(f):
            raise RenderError(f"timeline.audio[{n}]: file not found: {f}")
        info = probe(f)
        a = float(it.get("in", 0))
        src_dur = max((info["dur"] or 0) - a, 0.0)
        loop = bool(it.get("loop"))
        at = float(it["at"])
        if "out" in it:
            seg_d = float(it["out"]) - a
        else:
            seg_d = src_dur
        if "dur" in it:
            d = float(it["dur"])
        elif loop:
            d = max(total - at, 0.0)
        else:
            d = seg_d
        if at >= total:
            log(f"timeline.audio[{n}] starts at {at:.1f}s, after the video ends ({total:.1f}s): skipped")
            continue
        if not loop and at + d > total + 0.05:
            log(f"timeline.audio[{n}] runs {at + d - total:.1f}s past the end of the video: cut there")
        bus = it.get("bus") or ("music" if it.get("duck") else "voice")
        duck = bool(it.get("duck")) or bus == "music"
        auds.append({"file": f, "a": a, "d": d, "seg_d": seg_d, "at": at, "duck": duck, "bus": bus,
                     "loop": loop, "fade_in": float(it.get("fade_in") or 0.0),
                     "fade_out": float(it.get("fade_out") or 0.0),
                     "gain": float(it.get("gain_db", MUSIC_DB if bus == "music" else 0.0)),
                     "track": int(it.get("track", m.get("track", 0)))})
        for w in load_words(path, m):
            if a - 0.05 <= w["s"] and w["e"] <= a + d + 0.05:
                words.append({"w": w["w"], "s": at + w["s"] - a, "e": at + w["e"] - a})
    words = [w for w in sorted(words, key=lambda w: w["s"]) if w["s"] < total]
    return vids, auds, total, words


def check_zooms(entry):
    zs = sorted(entry.get("zooms", []), key=lambda z: z["at"])
    for z in zs:
        if z["at"] < 0 or z["at"] + z["dur"] > entry["d"] + 0.01:
            raise RenderError(f"timeline.video[{entry['n']}]: zoom at {z['at']}s for {z['dur']}s is outside the item "
                              f"({entry['d']:.2f}s)")
    for a, b in zip(zs, zs[1:]):
        if b["at"] < a["at"] + a["dur"]:
            raise RenderError(f"timeline.video[{entry['n']}]: zooms at {a['at']}s and {b['at']}s overlap")


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def zoom_filter(zooms, w, h, fps):
    """zoompan with eased zooms. rect = [x, y, w, h] as fractions of the frame. The frame is
    upscaled first so the pan moves in half pixels (no jitter on small sources)."""
    up = 2 if w * h <= 1920 * 1080 else 1
    z_terms, cx_terms, cy_terms = [], [], []
    for zm in zooms:
        rx, ry, rw, rh = zm["rect"]
        at, d = float(zm["at"]), float(zm["dur"])
        r = min(RAMP, d / 2)
        zf = max(1.0, min(1 / rw, 1 / rh))
        p = f"clip(min((it-{at:.3f})/{r:.3f},({at + d:.3f}-it)/{r:.3f}),0,1)"
        s = f"({p})*({p})*(3-2*({p}))"   # smoothstep
        z_terms.append(f"{zf - 1:.4f}*{s}")
        cx_terms.append(f"{rx + rw / 2 - 0.5:.4f}*{s}")
        cy_terms.append(f"{ry + rh / 2 - 0.5:.4f}*{s}")
    z = "1+" + "+".join(z_terms)
    cx = "(0.5+" + "+".join(cx_terms) + ")"
    cy = "(0.5+" + "+".join(cy_terms) + ")"
    x = f"max(0,min(iw-iw/zoom,{cx}*iw-iw/zoom/2))"
    y = f"max(0,min(ih-ih/zoom,{cy}*ih-ih/zoom/2))"
    pre = f"scale={w * up}:{h * up}:flags=lanczos," if up > 1 else ""
    return f"{pre}zoompan=z='{z}':x='{x}':y='{y}':d=1:s={w}x{h}:fps={fps}"


def fit_mode(entry, W, H):
    if entry.get("fit"):
        return entry["fit"]
    info = entry.get("info") or {}
    if not info.get("w"):
        return "pad"
    src, dst = info["w"] / info["h"], W / H
    return "blur" if abs(src - dst) / dst > 0.15 else "pad"


def video_chain(entry, k, W, H, fps):
    """Filters for one video item, from input k to label [v{k}]."""
    d = entry["d"]
    if entry["kind"] == "color":
        return f"[{k}:v]format=yuv420p,setsar=1,trim=duration={d:.3f},setpts=PTS-STARTPTS[v{k}]"
    info = entry["info"]
    head = f"[{k}:v]setpts=PTS-STARTPTS,fps={fps}"
    if entry.get("zooms"):
        head += "," + zoom_filter(entry["zooms"], info["w"], info["h"], fps)
    tail = (f"setsar=1,format=yuv420p,tpad=stop_mode=clone:stop_duration={d:.3f},"
            f"trim=duration={d:.3f},setpts=PTS-STARTPTS[v{k}]")
    mode = fit_mode(entry, W, H)
    if mode == "fill":
        return f"{head},scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},{tail}"
    if mode == "pad":
        return (f"{head},scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,{tail}")
    # blur: the same picture, blurred, fills the frame behind it (blurred at quarter size: ~16x cheaper)
    bw, bh = max(W // 4 // 2 * 2, 2), max(H // 4 // 2 * 2, 2)
    return (f"{head},split=2[f{k}][b{k}];"
            f"[b{k}]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},boxblur=8:2,"
            f"eq=brightness=-0.12,scale={W}:{H}[bg{k}];"
            f"[f{k}]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos[fg{k}];"
            f"[bg{k}][fg{k}]overlay=(W-w)/2:(H-h)/2,{tail}")


def video_inputs(entry, fps, W, H):
    if entry["kind"] == "color":
        return ["-f", "lavfi", "-t", f"{entry['d']:.3f}", "-i",
                f"color=c={entry['color']}:s={W}x{H}:r={fps}"]
    if entry["kind"] == "image":
        return ["-loop", "1", "-framerate", str(fps), "-t", f"{entry['d']:.3f}", "-i", entry["file"]]
    return ["-ss", f"{entry['a']:.3f}", "-t", f"{entry['d']:.3f}", "-i", entry["file"]]


# ---------------------------------------------------------------------------
# Audio: mix, then loudness
# ---------------------------------------------------------------------------
def mix_audio(vids, auds, total, out, tmp):
    inputs, graph, voices, music, sfx = [], [], [], [], []
    k = 0

    def add(file, a, d, at, track, gain, bus, loop=False, seg_d=None, fade_in=0.0, fade_out=0.0):
        nonlocal k
        play_d = max(min(d, total - at), 0.0)
        if not loop and seg_d is not None:
            play_d = min(play_d, seg_d)  # 'dur' is a maximum: never read past 'out'
        if play_d <= 0:
            return
        if loop and seg_d and seg_d > 0:
            inputs.extend(["-i", file])
            chain = [
                f"[{k}:a:{track}]aresample=48000",
                "aformat=sample_fmts=fltp:channel_layouts=stereo",
                f"atrim=start={a:.3f}:end={a + seg_d:.3f}",
                "asetpts=PTS-STARTPTS",
                f"aloop=loop=-1:size={max(round(seg_d * 48000), 1)}",
                f"atrim=duration={play_d:.3f}",
                "asetpts=PTS-STARTPTS",
            ]
        else:
            inputs.extend(["-ss", f"{a:.3f}", "-t", f"{play_d:.3f}", "-i", file])
            chain = [
                f"[{k}:a:{track}]asetpts=PTS-STARTPTS",
                "aresample=48000",
                "aformat=sample_fmts=fltp:channel_layouts=stereo",
            ]
        if fade_in > 0:
            chain.append(f"afade=t=in:st=0:d={min(fade_in, play_d):.3f}")
        if fade_out > 0:
            fd = min(fade_out, play_d)
            chain.append(f"afade=t=out:st={max(play_d - fd, 0.0):.3f}:d={fd:.3f}")
        chain.extend([
            f"volume={gain:.2f}dB",
            f"adelay={round(at * 1000)}:all=1",
            f"apad=whole_dur={total:.3f}",
            "asetpts=N/SR/TB",
            f"atrim=duration={total:.3f}[a{k}]",
        ])
        graph.append(",".join(chain))
        {"music": music, "sfx": sfx}.get(bus, voices).append(f"[a{k}]")
        k += 1

    for e in vids:
        if "audio_track" in e:
            add(e["file"], e["a"], e["d"], e["start"], e["audio_track"], float(e.get("gain_db", 0)), "voice")
    for a in auds:
        bus = a.get("bus") or ("music" if a.get("duck") else "voice")
        add(a["file"], a["a"], a["d"], a["at"], a["track"], a["gain"], bus,
            loop=bool(a.get("loop")), seg_d=a.get("seg_d"),
            fade_in=float(a.get("fade_in") or 0.0), fade_out=float(a.get("fade_out") or 0.0))
    inputs.extend(["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=48000:cl=stereo"])
    sil = f"[{k}:a]"

    def mixed(labels, name):
        if len(labels) == 1:
            graph.append(f"{labels[0]}anull[{name}]")
        else:
            graph.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest[{name}]")
        return f"[{name}]"

    parts = []
    if voices and music:
        vb = mixed(voices, "vb")
        mb = mixed(music, "mb")
        graph.append(f"{vb}asplit=2[vb1][vb2]")
        graph.append(f"{mb}[vb2]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=500[md]")
        parts = ["[vb1]", "[md]"]
    elif voices or music:
        parts = [mixed(voices or music, "vb")]
    if sfx:
        parts.append(mixed(sfx, "sb"))
    graph.append(f"{''.join(parts)}{sil}amix=inputs={len(parts) + 1}:normalize=0:duration=longest,"
                 f"asetpts=N/SR/TB,atrim=duration={total:.3f}[mix]")
    run(["ffmpeg", "-nostdin", "-hide_banner", "-y", *inputs, "-filter_complex", ";".join(graph),
         "-map", "[mix]", "-c:a", "pcm_s24le", out], cwd=tmp)
    return bool(voices or music or sfx)


def loudnorm(src, dst):
    """Two-pass loudness normalization, like process-voice.sh. Returns the measured input."""
    base = f"I={TARGET_LUFS}:TP={TARGET_TP}:LRA={TARGET_LRA}"
    r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", src, "-af",
                        f"loudnorm={base}:print_format=json", "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr)
    stats = json.loads(m.group(0)) if m else {}
    if not stats or stats.get("input_i") in ("-inf", None):
        run(["ffmpeg", "-nostdin", "-y", "-i", src, "-c:a", "pcm_s24le", dst])
        return None
    ln = (f"loudnorm={base}:measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
          f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
          f"offset={stats['target_offset']}:linear=true,aresample=48000")
    run(["ffmpeg", "-nostdin", "-y", "-i", src, "-af", ln, "-c:a", "pcm_s24le", dst])
    return float(stats["input_i"])


# ---------------------------------------------------------------------------
# Captions and subtitles
# ---------------------------------------------------------------------------
def ass_time(t):
    cs = round(max(t, 0) * 100)
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def caption_groups(words, per_group, max_chars):
    groups, cur = [], []
    for w in words:
        if cur and (len(cur) >= per_group or w["s"] - cur[-1]["e"] > 0.6
                    or len(" ".join(x["w"] for x in cur)) + len(w["w"]) > max_chars):
            groups.append(cur)
            cur = []
        cur.append(w)
        if w["w"].endswith(SENTENCE_END):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def build_ass(words, W, H, style, highlight="FFD700"):
    vertical = H > W * 1.5   # 9:16
    square = not vertical and H >= W * 0.95   # 1:1, 4:5 (X, LinkedIn, Instagram feeds)
    if vertical:
        fs, margin, (per, chars) = round(0.088 * W), round(0.17 * H), (3, 24)
    elif square:   # narrower than 16:9: fewer, bigger words per caption, like vertical
        fs, margin, (per, chars) = round(0.07 * W), round(0.12 * H), (4, 26)
    else:
        fs, margin, (per, chars) = round(0.055 * H), round(0.07 * H), (7, 42)
    vertical = vertical or square   # upper case, like Shorts
    font = CONFIG.get("CAPTION_FONT") or "DejaVu Sans"
    hl = "&H00{2}{1}{0}&".format(highlight[0:2], highlight[2:4], highlight[4:6])
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{fs},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{max(2, fs // 12)},3,2,60,60,{margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for grp in caption_groups(words, per, chars):
        texts = [re.sub(r"[{}\\]", "", x["w"]) for x in grp]
        if vertical:
            texts = [t.upper() for t in texts]
        if style == "plain":
            end = grp[-1]["e"] + 0.15
            lines.append(f"Dialogue: 0,{ass_time(grp[0]['s'])},{ass_time(end)},Default,,0,0,0,,{' '.join(texts)}")
            continue
        for k, w in enumerate(grp):
            t0 = w["s"]
            t1 = max(grp[k + 1]["s"] if k + 1 < len(grp) else w["e"] + 0.15, t0 + 0.08)
            shown = " ".join((r"{\c" + hl + r"\fscx112\fscy112}" + t + r"{\r}") if i == k else t
                             for i, t in enumerate(texts))
            lines.append(f"Dialogue: 0,{ass_time(t0)},{ass_time(t1)},Default,,0,0,0,,{shown}")
    return head + "\n".join(lines) + "\n"


def srt_time(t):
    ms = round(max(t, 0) * 1000)
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def build_srt(words):
    cues, cur = [], []
    for w in words:
        if cur and (w["s"] - cur[-1]["e"] > 1.0 or w["e"] - cur[0]["s"] > 6.0
                    or len(" ".join(x["w"] for x in cur)) + len(w["w"]) > 84):
            cues.append(cur)
            cur = []
        cur.append(w)
        if w["w"].endswith(SENTENCE_END) and len(" ".join(x["w"] for x in cur)) >= 20:
            cues.append(cur)
            cur = []
    if cur:
        cues.append(cur)
    out = []
    for n, c in enumerate(cues, 1):
        text = " ".join(x["w"] for x in c)
        if len(text) > 42:   # two lines, split near the middle
            mid = len(text) // 2
            cut = min((i for i, ch in enumerate(text) if ch == " "), key=lambda i: abs(i - mid), default=None)
            if cut:
                text = text[:cut] + "\n" + text[cut + 1:]
        out.append(f"{n}\n{srt_time(c[0]['s'])} --> {srt_time(max(c[-1]['e'], c[0]['s'] + 0.4))}\n{text}\n")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Render one format
# ---------------------------------------------------------------------------
def out_name(fmt, first, preview):
    tag = "" if first else "_" + fmt.replace(":", "x")
    return ("preview" if preview else "video") + tag + ".mp4"


def render_format(path, pj, fmt, W, H, vids, auds, total, words, args, out):
    fps = pj["fps"]
    cap = pj["timeline"].get("captions", {})
    with tempfile.TemporaryDirectory() as tmp:
        mix, norm = os.path.join(tmp, "mix.wav"), os.path.join(tmp, "norm.wav")
        has_audio = mix_audio(vids, auds, total, mix, tmp)
        measured = loudnorm(mix, norm) if has_audio else None
        if not has_audio:
            os.replace(mix, norm)
        inputs, graph = [], []
        for k, e in enumerate(vids):
            inputs += video_inputs(e, fps, W, H)
            graph.append(video_chain(e, k, W, H, fps))
        graph.append("".join(f"[v{k}]" for k in range(len(vids))) + f"concat=n={len(vids)}:v=1:a=0[vcat]")
        if cap.get("burn") and words:
            with open(os.path.join(tmp, "caps.ass"), "w", encoding="utf-8") as f:
                f.write(build_ass(words, W, H, cap.get("style") or "word-highlight",
                                  (cap.get("highlight") or "FFD700").lstrip("#")))
            graph.append("[vcat]ass=caps.ass[vout]")
        else:
            graph.append("[vcat]null[vout]")
        inputs += ["-i", norm]
        q = 30 if args.preview else args.quality
        venc = video_encoder(args.encoder, q)
        cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-y", *inputs, "-filter_complex", ";".join(graph),
               "-map", "[vout]", "-map", f"{len(vids)}:a", *venc, "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "320k", "-t", f"{total:.3f}", "-movflags", "+faststart", out + ".tmp.mp4"]
        try:
            run(cmd, cwd=tmp)
        except RenderError:
            if os.path.exists(out + ".tmp.mp4"):
                os.remove(out + ".tmp.mp4")
            raise
        os.replace(out + ".tmp.mp4", out)
    return measured


def media_stamp(path, pj):
    out = {}
    for mid, m in pj.get("media", {}).items():
        for key in ("file", "words"):
            f = proj.resolve(path, m.get(key))
            if f and os.path.isfile(f):
                st = os.stat(f)
                out[f"{mid}.{key}"] = [st.st_size, st.st_mtime_ns]   # ns: a same-size replacement in the same second still counts
        variants = m.get("variants")
        for fmt, vf in (variants.items() if isinstance(variants, dict) else ()):
            f = proj.resolve(path, vf)
            if f and os.path.isfile(f):
                st = os.stat(f)
                out[f"{mid}.variants.{fmt}"] = [st.st_size, st.st_mtime_ns]   # ns: a same-size replacement in the same second still counts
    for it in pj["timeline"].get("video", []) + pj["timeline"].get("audio", []):
        src = it.get("src")
        if src and src not in pj.get("media", {}):
            f = proj.resolve(path, src)
            if os.path.isfile(f):
                st = os.stat(f)
                out[src] = [st.st_size, st.st_mtime_ns]   # ns: a same-size replacement in the same second still counts
    return out


def save_frames(out, spec, total, frames_dir, tag):
    if spec == "auto":
        times = [total * (i + 0.5) / 6 for i in range(6)]
    else:
        try:
            times = [float(x) for x in spec.split(",") if x.strip()]
        except ValueError:
            die("--frames takes seconds like 1.5,12 or 'auto'")
    os.makedirs(frames_dir, exist_ok=True)
    saved = []
    for t in times:
        if not 0 <= t < total:
            log(f"--frames: {t}s is outside the video (0-{total:.1f}s), skipped")
            continue
        f = os.path.join(frames_dir, f"{tag}_{t:07.2f}.jpg")
        run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", out, "-frames:v", "1", "-q:v", "3", f])
        saved.append(f)
    return saved


def main():
    p = argparse.ArgumentParser(description="Render project.json's timeline to video.")
    p.add_argument("-P", "--project", required=True)
    p.add_argument("--format", help="render only this format (a key of project.json 'formats')")
    p.add_argument("--preview", action="store_true", help="half size, lower quality: out/preview*.mp4")
    p.add_argument("--frames", help="also save stills at these seconds (1.5,12) or 'auto'")
    p.add_argument("--encoder", default="auto", help="auto (NVENC if available, else libx264), h264_nvenc, libx264")
    p.add_argument("--quality", type=int, default=19, help="CQ/CRF, lower = better (default 19)")
    p.add_argument("-f", "--force", action="store_true", help="render even if nothing changed")
    p.add_argument("--json", action="store_true", help="print a summary as JSON on stdout")
    args = p.parse_args()
    need("ffmpeg", "ffprobe")

    try:
        pj = proj.load(args.project)
    except proj.ProjectError as e:
        die(str(e))
    errs = proj.validate(args.project, pj)
    if errs:
        die("project.json has problems:\n  " + "\n  ".join(errs))
    if not pj["timeline"].get("video"):
        die("timeline.video is empty: nothing to render")
    formats = pj["formats"]
    if args.format and args.format not in formats:
        die(f"no format {args.format!r} in project.json (has: {', '.join(formats)})")

    try:
        vids, auds, total, words = plan(args.project, pj)
    except RenderError as e:
        die(str(e))
    root = proj.root(args.project)
    outdir = os.path.join(root, "out")
    os.makedirs(outdir, exist_ok=True)
    summary = {"duration": round(total, 3), "outputs": [], "skipped": [], "frames": [], "srt": None}

    if words and not args.preview:
        srt = os.path.join(outdir, "video.srt")
        with open(srt, "w", encoding="utf-8") as f:
            f.write(build_srt(words))
        summary["srt"] = proj.rel(args.project, srt)

    # the media entries too: their settings (track, kind...) change the render, not only their files
    stamp = backends.input_hash(pj["timeline"], pj["fps"], pj.get("media", {}), media_stamp(args.project, pj),
                                args.quality)
    renders = pj.setdefault("renders", {})
    first = next(iter(formats))
    for fmt, (W, H) in formats.items():
        if args.format and fmt != args.format:
            continue
        if args.preview:
            W, H = max(W // 2 // 2 * 2, 2), max(H // 2 // 2 * 2, 2)
        out = os.path.join(outdir, out_name(fmt, fmt == first, args.preview))
        key = backends.input_hash(stamp, fmt, W, H)
        prev = renders.get(fmt, {})
        if (not args.preview and not args.force and prev.get("hash") == key and os.path.isfile(out)):
            log(f"{fmt}: unchanged, keeping {proj.rel(args.project, out)} (-f to render again)")
            summary["skipped"].append(proj.rel(args.project, out))
        else:
            log(f"{fmt}: rendering {total:.1f}s at {W}x{H}{' (preview)' if args.preview else ''}...")
            try:
                fv, fa, _, _ = plan(args.project, pj, fmt)   # per-format files (scene variants)
                measured = render_format(args.project, pj, fmt, W, H, fv, fa, total, words, args, out)
            except RenderError as e:
                proj.set_state(pj, "render", "failed")
                proj.save(args.project, pj)
                die(str(e))
            if not args.preview:
                renders[fmt] = {"file": proj.rel(args.project, out), "hash": key,
                                "loudness_before": measured}
            summary["outputs"].append(proj.rel(args.project, out))
            log(f"  -> {proj.rel(args.project, out)}")
        if args.frames:
            summary["frames"] += [proj.rel(args.project, f) for f in
                                  save_frames(out, args.frames, total, os.path.join(root, "checks", "frames"),
                                              os.path.splitext(os.path.basename(out))[0])]
    if not args.preview and not args.format:
        proj.set_state(pj, "render", "done")
    proj.save(args.project, pj)
    if args.json:
        json.dump(summary, sys.stdout, ensure_ascii=False)
        print()


if __name__ == "__main__":
    main()
