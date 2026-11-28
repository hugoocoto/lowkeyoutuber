#!/usr/bin/env python3
"""
sound.py - Video-adaptive music composer and CC0 SFX picker/downloader.

Works inside a project (-P project/) or standalone (generate / fetch / import-pack / styles).

PROJECT WORKFLOW
    sound.py -P project/ --propose          analyze script/timeline/captures -> proposals/audio.json
    sound.py -P project/ --place            render music + fetch SFX -> media/ & timeline.audio
    sound.py -P project/ --propose --place  do both in one shot

    Options in project mode:
      --backend composer|library  force adaptive FluidSynth composer or local MUSIC_DIR track
      --sfx-backend CHAIN         override SFX backend chain (default: library,freesound,cc0,synth)
      --style NAME                lofi | ambient_tech | synthwave | minimal | tension | chiptune
      --bpm N --key K             override tempo and musical key
      --no-music / --no-sfx       place only SFX or only music
      --no-llm                    skip LLM planning and use deterministic timeline heuristics
      -f / --force                regenerate audio even if cached

STANDALONE SUBCOMMANDS
    sound.py generate --style lofi --bpm 84 --dur 45 -o bg.wav
    sound.py fetch "soft ui click" --max-dur 1.0 -o click.mp3
    sound.py import-pack pack.zip --tag ui
    sound.py styles
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

import backends
import project as proj
from backends.music._styles import load_styles, style_from_prompt
from backends.music.composer import find_soundfont
from backends.sfx._cache import probe_duration, slug
from backends.sfx.cc0 import import_pack
from yt_common import die, log, need

FLOW_DEFAULT_STYLE = {
    "generated-short": ("synthwave", 112),
    "app-demo": ("ambient_tech", 104),
    "voice-over": ("minimal", 94),
    "script-then-record": ("lofi", 84),
    "edit-recording": ("lofi", 82),
}


def _probe_dur(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _timeline_duration(root, pj, script=None):
    """Determine video duration from timeline.video, or fallback to audio / script estimate."""
    total = 0.0
    for it in pj.get("timeline", {}).get("video", []):
        if "out" in it and "in" in it:
            total += max(float(it["out"]) - float(it["in"]), 0.0)
        elif "dur" in it:
            total += max(float(it["dur"]), 0.0)
    if total > 0:
        return total
    max_aud = 0.0
    for it in pj.get("timeline", {}).get("audio", []):
        src = it.get("src")
        m = pj.get("media", {}).get(src, {})
        f = proj.resolve(root, m.get("file") or src)
        d = float(it.get("out") or 0) - float(it.get("in") or 0)
        if d <= 0 and f and os.path.isfile(f):
            d = _probe_dur(f)
        max_aud = max(max_aud, float(it.get("at") or 0) + d)
    if max_aud > 0:
        return max_aud
    if script and script.get("lines"):
        words = sum(len(l.get("text", "").split()) for l in script["lines"])
        return max(10.0, round(words / 2.5 + len(script["lines"]) * 0.4, 1))
    return 30.0


def _line_windows(root, pj, script, total):
    """Map script lines to approximate [start, end] timeline windows."""
    if not script or not script.get("lines"):
        return []
    lines = script["lines"]
    # Try exact voice placements in timeline.audio (vo:<id>)
    vo_times = {}
    for it in pj.get("timeline", {}).get("audio", []):
        src = str(it.get("src", ""))
        if src.startswith("vo:"):
            lid = src[3:]
            m = pj.get("media", {}).get(src, {})
            f = proj.resolve(root, m.get("file")) if m else None
            d = _probe_dur(f) if (f and os.path.isfile(f)) else max(2.0, len(lid) * 0.3)
            vo_times[lid] = (float(it.get("at") or 0.0), float(it.get("at") or 0.0) + d)
    if all(l["id"] in vo_times for l in lines):
        out = []
        for i, line in enumerate(lines):
            s, e = vo_times[line["id"]]
            nxt = vo_times[lines[i + 1]["id"]][0] if i + 1 < len(lines) else total
            out.append({"id": line["id"], "start": round(s, 2), "end": round(max(e, nxt), 2), "line": line})
        return out
    # Fallback: proportional split across total duration by word count
    counts = [max(len(l.get("text", "").split()), 3) for l in lines]
    wsum = sum(counts) or 1
    t = 0.0
    out = []
    for line, c in zip(lines, counts):
        span = total * (c / wsum)
        out.append({"id": line["id"], "start": round(t, 2), "end": round(t + span, 2), "line": line})
        t += span
    return out


def _capture_events(root, pj):
    """capture.py events ({"t", "type"}, seconds into the capture) mapped to timeline seconds."""
    events = []
    for mid, m in (pj.get("media") or {}).items():
        p = proj.resolve(root, m["events"]) if m.get("events") else None
        if not p or not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        evs = [e for e in (data if isinstance(data, list) else data.get("events", []))
               if isinstance(e, dict) and isinstance(e.get("t"), (int, float))]
        start = 0.0
        for it in pj.get("timeline", {}).get("video", []):
            a = float(it.get("in", 0))
            d = float(it["out"]) - a if "out" in it else float(it.get("dur") or 0)
            if it.get("src") == mid:
                events += [{"at": start + e["t"] - a, "action": e.get("type", "")}
                           for e in evs if a <= e["t"] < a + d]
            start += d
    return events


def _heuristic_plan(root, pj, script, total, args):
    flow = pj.get("flow") or ""
    default_style, default_bpm = FLOW_DEFAULT_STYLE.get(flow, ("lofi", 86))
    style = args.style or default_style
    styles = load_styles()
    st_spec = styles.get(style, styles["lofi"])
    bpm = float(args.bpm or st_spec["bpm"] or default_bpm)
    key = args.key or st_spec["key"]
    scale = st_spec["scale"]

    windows = _line_windows(root, pj, script, total)
    sections = []
    sfx_cues = []

    if windows:
        n = len(windows)
        for idx, w in enumerate(windows):
            line = w["line"]
            text_low = (line.get("text") or "").lower()
            scene_type = (line.get("scene") or {}).get("type", "") if isinstance(line.get("scene"), dict) else ""
            is_first = idx == 0
            is_last = idx == n - 1
            if is_first:
                mood, energy, drums = "hook", 0.72, True
            elif is_last:
                mood, energy, drums = "resolve", 0.55, False
            elif any(k in text_low for k in ("error", "bug", "falla", "fail", "careful", "cuidado", "crash", "warning")):
                mood, energy, drums = "tension", 0.65, True
            elif scene_type in ("code", "terminal", "diagram"):
                mood, energy, drums = "explain", 0.40, False
            else:
                mood, energy, drums = "normal", 0.55, True
            sections.append({
                "start": w["start"],
                "end": w["end"],
                "label": w["id"],
                "mood": mood,
                "energy": energy,
                "drums": drums,
            })
            # Propose subtle transition SFX on scene changes or explicit line.sfx
            if isinstance(line.get("sfx"), str) and line["sfx"].strip():
                sfx_cues.append({"at": w["start"], "query": line["sfx"].strip(),
                                 "reason": f"script line {w['id']}", "gain_db": -12.0, "max_dur": 1.5})
            elif idx > 0 and scene_type in ("title", "diagram", "code"):
                q = "soft subtle whoosh" if scene_type in ("title", "diagram") else "soft mechanical click"
                sfx_cues.append({"at": w["start"], "query": q,
                                 "reason": f"{scene_type} transition at {w['id']}", "gain_db": -16.0, "max_dur": 1.0})
    else:
        # Unscripted video: intro -> body -> outro energy arc
        t1 = round(min(4.0, total * 0.15), 2)
        t2 = round(max(t1 + 1.0, total - min(5.0, total * 0.15)), 2)
        sections = [
            {"start": 0.0, "end": t1, "label": "intro", "mood": "hook", "energy": 0.7, "drums": True},
            {"start": t1, "end": t2, "label": "body", "mood": "explain", "energy": 0.45, "drums": True},
            {"start": t2, "end": round(total, 2), "label": "outro", "mood": "resolve", "energy": 0.5, "drums": False},
        ]

    # Add subtle UI clicks for capture.py actions if no script SFX already sits nearby
    for ev in _capture_events(root, pj):
        at = round(float(ev["at"]), 2)
        if at >= total - 0.2:
            continue
        if any(abs(c["at"] - at) < 0.4 for c in sfx_cues):
            continue
        act = (ev.get("action") or "").lower()
        if act == "click":
            sfx_cues.append({"at": at, "query": "soft ui click", "reason": f"capture {act}",
                             "gain_db": -18.0, "max_dur": 0.4})
        elif act in ("key", "press", "type", "run"):
            sfx_cues.append({"at": at, "query": "keyboard tap", "reason": f"capture {act}",
                             "gain_db": -18.0, "max_dur": 0.5})
    sfx_cues = sorted(sfx_cues, key=lambda c: c["at"])[:12]

    topic = (pj.get("inputs") or {}).get("topic") or (script or {}).get("title") or flow or "background music"
    prompt = f"{style.replace('_', ' ')} {topic}".strip()
    return {
        "style": style,
        "bpm": bpm,
        "key": key,
        "scale": scale,
        "prompt": prompt,
        "gain_db": -22.0,
        "fade_in": 0.4,
        "fade_out": 1.2,
        "sections": sections,
        "sfx": sfx_cues,
    }


def _refine_with_llm(base_plan, pj, script, total):
    """Ask the configured LLM backend to adapt style, sections, and SFX cues to the script/project."""
    styles = load_styles()
    style_list = ", ".join(f"{k} ({v['description']})" for k, v in styles.items())
    lines_summary = [
        {"id": l["id"], "text": l.get("text", ""),
         "scene": (l.get("scene") or {}).get("type") if isinstance(l.get("scene"), dict) else None}
        for l in (script or {}).get("lines", [])
    ]
    prompt = (
        "You are an audio director for a YouTube video.\n"
        f"Project flow: {pj.get('flow') or 'custom'}, duration: {total:.1f}s, inputs: {pj.get('inputs', {})}.\n"
        f"Available music styles: {style_list}.\n"
        f"Script lines: {json.dumps(lines_summary, ensure_ascii=False)}\n"
        f"Baseline plan (keep the exact section start/end times):\n{json.dumps(base_plan, ensure_ascii=False)}\n\n"
        "Return ONLY valid JSON with keys: style, bpm, key, scale, prompt, sections, sfx.\n"
        "- style must be one of the available styles.\n"
        "- Each section has start, end, label, mood (hook|explain|tension|build|resolve|normal), "
        "energy (0.2-0.9), drums (true/false).\n"
        "- Keep sfx sparse and tasteful (0 to 8 cues, max_dur <= 1.5, gain_db between -20 and -10), "
        "using simple search queries like 'soft whoosh', 'ui click', 'subtle chime', 'error buzz'."
    )
    try:
        raw, _ = backends.call("llm", prompt, json_mode=True)
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0) if m else raw)
        if not isinstance(data, dict):
            return base_plan
        out = dict(base_plan)
        if data.get("style") in styles:
            out["style"] = data["style"]
        if isinstance(data.get("bpm"), (int, float)) and 55 <= data["bpm"] <= 180:
            out["bpm"] = float(data["bpm"])
        if isinstance(data.get("key"), str) and data["key"].strip():
            out["key"] = data["key"].strip()
        if isinstance(data.get("scale"), str) and data["scale"].strip():
            out["scale"] = data["scale"].strip()
        if isinstance(data.get("prompt"), str) and data["prompt"].strip():
            out["prompt"] = data["prompt"].strip()
        num = (int, float)
        secs = data.get("sections")
        if isinstance(secs, list) and secs and all(
                isinstance(s, dict) and isinstance(s.get("start"), num) and isinstance(s.get("end"), num)
                and isinstance(s.get("energy", 0.5), num) and isinstance(s.get("mood", ""), str) for s in secs):
            out["sections"] = secs
        if isinstance(data.get("sfx"), list):
            out["sfx"] = [c for c in data["sfx"] if isinstance(c, dict) and isinstance(c.get("at"), num)
                          and c.get("query") and isinstance(c.get("gain_db", 0), num)
                          and isinstance(c.get("max_dur", 0), (num, type(None)))][:12]
        return out
    except Exception as e:
        log(f"LLM audio planning skipped ({e}), using timeline heuristics.")
        return base_plan


def _recommend_music_mode(plan, total, explicit_backend=None):
    """Recommend 'composer' vs 'library' based on project shape and MUSIC_DIR matches."""
    lib_hits = []
    try:
        lib = backends.load("music", "library")
        if lib.check()[0]:
            lib_hits = [
                {"file": e["rel"], "path": e["path"], "overlap": ov, "duration": round(e["duration"], 2)}
                for ov, e in lib.search(plan["prompt"], duration=total, limit=3)
                if ov > 0
            ]
    except Exception:
        pass
    if explicit_backend in ("composer", "library"):
        mode = explicit_backend
        reason = f"requested --backend {explicit_backend}"
    elif len(plan.get("sections") or []) >= 2:
        mode = "composer"
        reason = "project has distinct script/timeline sections suited for adaptive FluidSynth scoring"
    elif lib_hits:
        mode = "library"
        reason = f"found matching track {lib_hits[0]['file']!r} in MUSIC_DIR"
    else:
        mode = "composer"
        reason = "no matching track in MUSIC_DIR; using adaptive FluidSynth composer"
    return mode, reason, lib_hits


def _load(args):
    root = proj.root(args.project)
    try:
        pj = proj.load(args.project)
        script = proj.load_script(args.project) if os.path.isfile(os.path.join(root, "script.json")) else None
    except proj.ProjectError as e:
        die(str(e))
    return root, pj, script


def propose(args):
    root, pj, script = _load(args)
    total = _timeline_duration(root, pj, script)
    plan = _heuristic_plan(root, pj, script, total, args)
    if not args.no_llm:
        plan = _refine_with_llm(plan, pj, script, total)
        # Re-apply explicit CLI overrides after LLM
        if args.style:
            plan["style"] = args.style
        if args.bpm:
            plan["bpm"] = float(args.bpm)
        if args.key:
            plan["key"] = args.key
    if args.no_music:
        plan["music_enabled"] = False
    else:
        plan["music_enabled"] = True
    if args.no_sfx:
        plan["sfx"] = []

    mode, reason, lib_hits = _recommend_music_mode(plan, total, explicit_backend=args.backend)
    proposal = {
        "mode": mode,
        "reason": reason,
        "duration": round(total, 3),
        "music": {
            "enabled": plan["music_enabled"],
            "prompt": plan["prompt"],
            "style": plan["style"],
            "bpm": plan["bpm"],
            "key": plan["key"],
            "scale": plan["scale"],
            "gain_db": plan["gain_db"],
            "fade_in": plan["fade_in"],
            "fade_out": plan["fade_out"],
            "loop": mode == "library",
            "sections": plan["sections"],
            "library_candidates": lib_hits,
        },
        "sfx": plan["sfx"],
    }
    os.makedirs(os.path.join(root, "proposals"), exist_ok=True)
    prop_path = os.path.join(root, "proposals", "audio.json")
    with open(prop_path, "w", encoding="utf-8") as f:
        json.dump(proposal, f, ensure_ascii=False, indent=2)
        f.write("\n")
    proj.set_state(pj, "sound", "proposed", note=f"{mode}: {plan['style']} @ {int(plan['bpm'])} BPM")
    proj.save(args.project, pj)
    return proposal, prop_path


def _clean_sound_entries(pj):
    """Remove previously placed sound.py items while keeping voices/takes/user audio."""
    tl = pj["timeline"]
    ours = [it for it in tl.get("audio", []) if it.get("managed_by") == "sound.py"]
    tl["audio"] = [it for it in tl.get("audio", []) if it.get("managed_by") != "sound.py"]
    used = {it.get("src") for it in tl["audio"] + tl.get("video", [])}
    for it in ours:
        if it.get("src") not in used:
            pj.get("media", {}).pop(it.get("src"), None)


def place(args, proposal=None):
    root, pj, script = _load(args)
    prop_path = os.path.join(root, "proposals", "audio.json")
    if proposal is None:
        if not os.path.isfile(prop_path):
            proposal, _ = propose(args)
            pj = proj.load(args.project)
        else:
            with open(prop_path, encoding="utf-8") as f:
                proposal = json.load(f)

    total = _timeline_duration(root, pj, script)
    music_cfg = proposal.get("music") or {}
    mode = args.backend or proposal.get("mode") or "composer"
    if args.style:
        music_cfg["style"] = args.style
    if args.bpm:
        music_cfg["bpm"] = float(args.bpm)
    if args.key:
        music_cfg["key"] = args.key

    _clean_sound_entries(pj)
    placed = {"music": None, "sfx": []}

    if not args.no_music and music_cfg.get("enabled", True):
        picked_file = music_cfg.get("file")
        if mode == "library" and picked_file and os.path.isfile(proj.resolve(root, picked_file)):
            src_path = proj.resolve(root, picked_file)
            used = backends.load("music", "library")
        elif mode == "library":
            try:
                src_path, used = backends.call(
                    "music", music_cfg.get("prompt") or music_cfg.get("style") or "calm",
                    duration=total, override="library", strict=True,
                )
            except backends.BackendError:
                log("No matching track in MUSIC_DIR; falling back to FluidSynth composer.")
                mode = "composer"
        if mode == "composer":
            comp = backends.load("music", "composer")
            ok, why = comp.check()
            if not ok:
                die(f"music composer unavailable: {why}")
            key_hash = backends.input_hash(
                "sound-music", total, music_cfg.get("style"), music_cfg.get("bpm"),
                music_cfg.get("key"), music_cfg.get("scale"), music_cfg.get("sections"),
                load_styles().get(music_cfg.get("style")), find_soundfont(),
            )
            dst = os.path.join(root, "media", f"music_{key_hash}.wav")
            if args.force or not os.path.isfile(dst):
                comp.compose(
                    dst,
                    prompt=music_cfg.get("prompt", ""),
                    duration=total,
                    style=music_cfg.get("style"),
                    bpm=music_cfg.get("bpm"),
                    key=music_cfg.get("key"),
                    scale=music_cfg.get("scale"),
                    sections=music_cfg.get("sections"),
                )
            src_path = dst
            used = comp
        else:
            ext = os.path.splitext(src_path)[1] or ".mp3"
            dst = os.path.join(root, "media", f"music_library{ext}")
            if os.path.abspath(src_path) != os.path.abspath(dst):
                shutil.copy2(src_path, dst)
            src_path = dst

        pj["media"]["bg-music"] = {"file": proj.rel(args.project, src_path), "kind": "audio"}
        track_dur = _probe_dur(src_path)
        item = {
            "src": "bg-music",
            "at": 0.0,
            "duck": True,
            "bus": "music",
            "gain_db": float(music_cfg.get("gain_db", -22.0)),
            "fade_in": float(music_cfg.get("fade_in", 0.4)),
            "fade_out": float(music_cfg.get("fade_out", 1.2)),
            "managed_by": "sound.py",
        }
        if bool(music_cfg.get("loop")) or (track_dur > 0 and track_dur < total - 0.5):
            item["loop"] = True
        pj["timeline"]["audio"].append(item)
        pj.setdefault("backends_used", {})["music"] = used.name
        placed["music"] = {"backend": used.name, "file": proj.rel(args.project, src_path), **item}

    if not args.no_sfx:
        for idx, cue in enumerate(proposal.get("sfx") or [], 1):
            q = cue.get("query")
            if not q:
                continue
            try:
                sfx_path, used_sfx = backends.call(
                    "sfx", q, max_dur=cue.get("max_dur"), override=args.sfx_backend,
                )
            except backends.BackendError as e:
                log(f"SFX cue {idx} ({q!r}) skipped: {e}")
                continue
            ext = os.path.splitext(sfx_path)[1] or ".wav"
            mid = f"sfx:{idx}-{slug(q)}"
            dst = os.path.join(root, "media", f"sfx_{idx:02d}_{slug(q)}{ext}")
            if os.path.abspath(sfx_path) != os.path.abspath(dst):
                shutil.copy2(sfx_path, dst)
            pj["media"][mid] = {"file": proj.rel(args.project, dst), "kind": "audio"}
            item = {
                "src": mid,
                "at": round(float(cue["at"]), 3),
                "bus": "sfx",
                "duck": False,
                "gain_db": float(cue.get("gain_db", -14.0)),
                "managed_by": "sound.py",
            }
            pj["timeline"]["audio"].append(item)
            pj.setdefault("backends_used", {})["sfx"] = used_sfx.name
            placed["sfx"].append({"id": mid, "backend": used_sfx.name, "query": q, "at": item["at"]})

    proj.set_state(pj, "sound", "done")
    proj.save(args.project, pj)
    return placed


def cmd_generate(args):
    need("ffmpeg")
    comp = backends.load("music", "composer")
    sections = None
    if args.plan:
        with open(args.plan, encoding="utf-8") as f:
            data = json.load(f)
        sections = data.get("sections") if isinstance(data, dict) else data
    try:  # compose() checks fluidsynth and the soundfont itself, honouring --soundfont
        out_path, meta = comp.compose(
            args.output,
            prompt=args.prompt or "",
            duration=args.dur,
            style=args.style,
            bpm=args.bpm,
            key=args.key,
            scale=args.scale,
            sections=sections,
            seed=args.seed,
            soundfont=args.soundfont,
        )
    except backends.BackendError as e:
        die(str(e))
    res = {"file": out_path, **meta}
    if args.json:
        json.dump(res, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(f"Generated {out_path} ({meta['style']}, {meta['bpm']} BPM, {meta['key']} {meta['scale']}, {meta['duration']}s)")


def cmd_fetch(args):
    try:
        path, used = backends.call("sfx", args.query, max_dur=args.max_dur, override=args.backend)
    except backends.BackendError as e:
        die(str(e))
    out = args.output
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        shutil.copy2(path, out)
    res = {"file": out or path, "cached": path, "backend": used.name, "duration": round(probe_duration(path), 3)}
    if args.json:
        json.dump(res, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(f"{res['file']} ({used.name}, {res['duration']}s)")


def cmd_import_pack(args):
    if not os.path.isfile(args.zipfile):
        die(f"pack not found: {args.zipfile}")
    files = import_pack(args.zipfile, tag=args.tag or "")
    res = {"imported": len(files), "files": files}
    if args.json:
        json.dump(res, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(f"Imported {len(files)} CC0 audio files into SFX cache.")


def cmd_styles(args):
    styles = load_styles()
    if args.json:
        json.dump(styles, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        for name, spec in styles.items():
            print(f"{name:<14} {spec.get('bpm')} BPM  {spec.get('key')} {spec.get('scale'):<9} {spec.get('description', '')}")


def main():
    # Dispatch standalone subcommands cleanly without breaking -P project mode
    subcmds = {"generate", "fetch", "import-pack", "styles"}
    if len(sys.argv) > 1 and sys.argv[1] in subcmds:
        p = argparse.ArgumentParser(prog="sound.py")
        sp = p.add_subparsers(dest="cmd", required=True)

        g = sp.add_parser("generate", help="generate a custom music track with FluidSynth")
        g.add_argument("--prompt", default="", help="mood/topic prompt")
        g.add_argument("--style", help="style preset name")
        g.add_argument("--bpm", type=float)
        g.add_argument("--key", help="musical key, e.g. C, F#, Bb")
        g.add_argument("--scale", help="major, minor, dorian, lydian, phrygian")
        g.add_argument("--dur", type=float, default=30.0, help="target duration in seconds")
        g.add_argument("--plan", help="JSON file with section list [{start, end, mood, energy, drums}]")
        g.add_argument("--seed", type=int, default=0)
        g.add_argument("--soundfont", help="path to a .sf2/.sf3 file")
        g.add_argument("-o", "--output", required=True)
        g.add_argument("--json", action="store_true")
        g.set_defaults(fn=cmd_generate)

        f = sp.add_parser("fetch", help="find/download a CC0 sound effect")
        f.add_argument("query", help="e.g. 'soft ui click', 'whoosh'")
        f.add_argument("--max-dur", type=float, help="maximum duration in seconds")
        f.add_argument("--backend", help="override SFX backend chain (e.g. freesound,cc0,synth)")
        f.add_argument("-o", "--output", help="copy result to this path (default: print cache path)")
        f.add_argument("--json", action="store_true")
        f.set_defaults(fn=cmd_fetch)

        ip = sp.add_parser("import-pack", help="import a CC0 zip pack (e.g. Kenney audio) into cache")
        ip.add_argument("zipfile")
        ip.add_argument("--tag", help="tag added to imported files")
        ip.add_argument("--json", action="store_true")
        ip.set_defaults(fn=cmd_import_pack)

        st = sp.add_parser("styles", help="list available music styles")
        st.add_argument("--json", action="store_true")
        st.set_defaults(fn=cmd_styles)

        args = p.parse_args()
        args.fn(args)
        return

    p = argparse.ArgumentParser(
        description="Analyze a project to propose/generate adaptive music (FluidSynth) and CC0 SFX.",
        epilog="Standalone commands: generate, fetch, import-pack, styles (run `sound.py <cmd> -h`).",
    )
    p.add_argument("-P", "--project", required=True)
    p.add_argument("--propose", action="store_true", help="write proposals/audio.json adapted to the video")
    p.add_argument("--place", "--apply", dest="place", action="store_true",
                   help="render music + fetch SFX and write timeline.audio")
    p.add_argument("--backend", choices=["composer", "library"], help="force music backend")
    p.add_argument("--sfx-backend", help="override SFX backend chain")
    p.add_argument("--style", help="override music style")
    p.add_argument("--bpm", type=float, help="override tempo")
    p.add_argument("--key", help="override musical key")
    p.add_argument("--no-music", action="store_true")
    p.add_argument("--no-sfx", action="store_true")
    p.add_argument("--no-llm", action="store_true", help="use deterministic timeline heuristics instead of LLM")
    p.add_argument("-f", "--force", action="store_true", help="re-render music even if cached")
    p.add_argument("--json", action="store_true", help="summary on stdout")
    args = p.parse_args()

    if not args.propose and not args.place:
        args.propose = True

    need("ffmpeg", "ffprobe")
    proposal = None
    out = {}
    if args.propose:
        proposal, prop_path = propose(args)
        out["proposal"] = proj.rel(args.project, prop_path)
        out["plan"] = proposal
    if args.place:
        placed = place(args, proposal=proposal)
        out["placed"] = placed

    if args.json:
        json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        if "plan" in out:
            pl = out["plan"]
            m = pl["music"]
            print(f"Proposed ({pl['mode']}): {m['style']} @ {int(m['bpm'])} BPM ({m['key']} {m['scale']}), "
                  f"{len(m['sections'])} sections, {len(pl['sfx'])} SFX cues -> {out['proposal']}")
            print(f"  reason: {pl['reason']}")
        if "placed" in out:
            pl = out["placed"]
            if pl["music"]:
                print(f"Placed music ({pl['music']['backend']}): {pl['music']['file']}")
            print(f"Placed {len(pl['sfx'])} SFX cues on timeline.audio.")


if __name__ == "__main__":
    main()
