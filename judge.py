#!/usr/bin/env python3
"""
judge.py - Check video automatically with a multimodal `judge` backend before a person looks:
closed questions (yes/no, 0-4 scores) about frames, clips and narration. Results go to
checks/judge.json; anything below the thresholds is listed so the flow can redo it or ask.

    The judge catches obvious problems (text cut off, a picture that has nothing to do with the
    narration, a Short that ends mid-sentence). It doesn't replace the person's review.

WHAT
    --scenes          every rendered scene (scenes.py): text readable and inside the frame,
                      nothing overlapping, picture matches the line's narration
    --lines           the final video, line by line (voice-over, app demos): does the narration
                      describe what's on screen while it's said?
    --clips FILE      Shorts proposals (clips.py clips.json) of --video: hook, works without
                      context, ends cleanly. Adds "judge" to each clip; doesn't change the order.

USAGE
    judge.py -P project/ --scenes
    judge.py -P project/ --lines
    judge.py --clips work/x/shorts/clips.json --video work/x/video.mp4
    judge.py ... --backend ollama_vlm --json --strict     (--strict: exit 1 if anything fails)

    Backends: gemini_decisions (cloud: real probabilities, sees motion and sound) or ollama_vlm
    (local: frames only; stricter thresholds). `lky backends --kind judge`.
"""

import argparse
import json
import os
import sys

import backends
import project as proj
from backends._media import duration, frames
from yt_common import die, log

SCENE_Q = {
    "readable": {"type": "noul", "ask": "Is all the text fully inside the frame and big enough to read on a phone?"},
    "broken": {"type": "noul", "ask": "Is any text cut off at an edge, overlapping other text, or hidden behind something?"},
    "matches": {"type": "score", "ask": "How well does the picture illustrate this narration: \"{text}\"?",
                "levels": ["Unrelated", "Barely related", "Related", "Clearly illustrates it", "Illustrates it perfectly"]},
}
LINE_Q = {
    "describes": {"type": "score", "ask": "The narration said over this part is: \"{text}\". Does it describe what is on screen?",
                  "levels": ["Contradicts the screen", "Unrelated", "Partly", "Yes", "Exactly"]},
}
CLIP_Q = {
    "hook": {"type": "score", "ask": "Would the first seconds make someone scrolling stop and watch?",
             "levels": ["No", "Unlikely", "Maybe", "Likely", "Definitely"]},
    "standalone": {"type": "noul", "ask": "Does this clip make sense on its own, without the rest of the video?"},
    "clean_end": {"type": "noul", "ask": "Does it end at a natural point (not mid-sentence or mid-action)?"},
}
NEGATIVE = {"broken"}   # questions where "yes" is the problem


def fill(qs, **kw):
    return {n: dict(q, ask=q["ask"].format(**kw)) for n, q in qs.items()}


def verdict(answers, strict):
    """Which checks fail. Without calibrated probabilities, scores need a bit more."""
    bad = []
    for n, a in answers.items():
        if n.startswith("_"):
            continue
        if isinstance(a.get("answer"), bool):
            if a["answer"] == (n in NEGATIVE):
                bad.append(n)
        elif a.get("score") is not None and a["score"] < (0.625 if strict else 0.5):
            bad.append(n)
    return bad


def run(judge, items):
    results = []
    for it in items:
        log(f"  {it['name']}...")
        try:
            ans = judge.ask(it["inputs"], it["questions"])
        except backends.BackendError as e:
            results.append({"name": it["name"], "error": str(e)})
            continue
        fails = verdict(ans, not judge.caps.get("probabilities"))
        results.append({"name": it["name"], "answers": {k: v for k, v in ans.items() if not k.startswith("_")},
                        "fails": fails, **it.get("extra", {})})
    return results


def main():
    p = argparse.ArgumentParser(description="Automatic checks with a multimodal judge backend.")
    p.add_argument("-P", "--project")
    p.add_argument("--scenes", action="store_true")
    p.add_argument("--lines", action="store_true")
    p.add_argument("--clips", help="clips.json from clips.py")
    p.add_argument("--video", help="the long video the clips come from")
    p.add_argument("--backend")
    p.add_argument("--strict", action="store_true", help="exit 1 if any check fails")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    if not (args.scenes or args.lines or args.clips):
        die("say what to check: --scenes, --lines or --clips")
    try:
        judge = backends.pick("judge", args.backend)
    except backends.BackendError as e:
        die(str(e))
    log(f"Judge: {judge.name} ({judge.model})")
    items, out_path = [], None

    if args.clips:
        if not args.video or not os.path.isfile(args.video):
            die("--clips needs --video, the long video")
        with open(args.clips, encoding="utf-8") as f:
            clips = json.load(f)
        for n, c in enumerate(clips, 1):
            inputs = ([{"video": args.video, "start": c["start"], "end": c["end"]}] if judge.caps.get("video")
                      else [{"jpeg": j} for j in frames(args.video, [c["start"] + 1, (c["start"] + c["end"]) / 2,
                                                                       c["end"] - 1.5])])
            inputs.append(f"Title: {c.get('title', '')}. Summary: {c.get('summary', '')}")
            items.append({"name": f"clip {n}", "inputs": inputs, "questions": CLIP_Q, "extra": {"clip": n}})
        res = run(judge, items)
        for r in res:
            if "answers" in r:
                a = r["answers"]
                clips[r["clip"] - 1]["judge"] = {k: round(v["score"], 3) for k, v in a.items()} | {"fails": r["fails"]}
        with open(args.clips, "w", encoding="utf-8") as f:
            json.dump(clips, f, ensure_ascii=False, indent=2)
        out_path = os.path.join(os.path.dirname(os.path.abspath(args.clips)), "judge.json")
    else:
        if not args.project:
            die("-P project is required for --scenes / --lines")
        try:
            pj = proj.load(args.project)
            script = proj.load_script(args.project)
        except proj.ProjectError as e:
            die(str(e))
        root = proj.root(args.project)
        lines = {line["id"]: line for line in script["lines"]}
        if args.scenes:
            for lid, line in lines.items():
                m = pj.get("media", {}).get(f"scene:{lid}")
                if not m:
                    continue
                f = proj.resolve(args.project, m["file"])
                d = duration(f)
                inputs = [{"jpeg": j} for j in frames(f, [d * 0.5, max(d - 0.3, 0)])]
                inputs.append(f"Narration: {line['text']}")
                items.append({"name": f"scene {lid}", "inputs": inputs, "questions": fill(SCENE_Q, text=line["text"]),
                              "extra": {"line": lid}})
        if args.lines:
            video = os.path.join(root, "out", "video.mp4")
            if not os.path.isfile(video):
                die("render the video first (render.py)")
            vo = sorted((a for a in pj["timeline"].get("audio", []) if str(a.get("src", "")).startswith("vo:")),
                        key=lambda a: a["at"])
            for k, a in enumerate(vo):
                lid = a["src"][3:]
                end = vo[k + 1]["at"] if k + 1 < len(vo) else duration(video)
                if lid not in lines:
                    continue
                inputs = ([{"video": video, "start": a["at"], "end": end}] if judge.caps.get("video")
                          else [{"jpeg": j} for j in frames(video, [a["at"] + 0.3, (a["at"] + end) / 2])])
                items.append({"name": f"line {lid}", "inputs": inputs,
                              "questions": fill(LINE_Q, text=lines[lid]["text"]), "extra": {"line": lid}})
        if not items:
            die("nothing to check (no rendered scenes / no narration on the timeline)")
        res = run(judge, items)
        out_path = os.path.join(root, "checks", "judge.json")
        proj.set_state(pj, "judge", "done" if not any(r.get("fails") or r.get("error") for r in res) else "proposed")
        proj.save(args.project, pj)

    report = {"backend": judge.name, "model": judge.model, "results": res}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    failed = [r for r in res if r.get("fails") or r.get("error")]
    for r in res:
        log(f"{r['name']}: " + (f"ERROR {r['error']}" if r.get("error") else
                                ("ok" if not r["fails"] else "check " + ", ".join(r["fails"]))))
    log(f"{len(res) - len(failed)}/{len(res)} passed -> {out_path}")
    if args.json:
        json.dump(report, sys.stdout, ensure_ascii=False)
        print()
    if failed and args.strict:
        sys.exit(1)


if __name__ == "__main__":
    main()
