#!/usr/bin/env python3
"""
scenes.py - Animated scenes written as code (HTML + GSAP), rendered with a `render` backend
(HyperFrames by default). One scene per script line that has a "scene" field.

For every such line it writes scenes/<id>/index.html from a template, checks it (lint), and
renders scenes/<id>.mp4 (one file per format: scenes/<id>_9x16.mp4 ...), as long as the line's
narration, so `tts.py --place` lines them up. The scene becomes media "scene:<id>" and is the
line's picture unless the line has its own "visual".

    You can edit scenes/<id>/index.html by hand: an edited scene is never overwritten, only
    rendered again. Delete it to get the template's version back.

SCENE TYPES (script.json: "scene": {"type": ..., ...}; "at" = seconds into the line or a word
of the narration, e.g. "at": "coma" = when the voice says "coma")
    title     {"title", "subtitle"}
    text      {"items": ["...", ...], "at": [..]}             points appearing one by one
    code      {"code", "lang": "c", "check": "program"|"fragment"|false, "run": true,
               "highlight": [{"lines": [2], "at": "coma"}], "output_at": ...}
              C code is compiled (and run, with "run") for real: the output shown is the
              program's real output. A compile error stops the scene.
    terminal  {"commands": [{"cmd": "ls -l", "at": ...}], "cwd": "path"}   commands run for
              real (20 s timeout, no API keys in the environment); their output is shown
    diagram   {"boxes": [{"id", "label", "x", "y", "w", "h"}], "arrows": [{"from", "to", "label"}],
               "steps": [{"show": ["box id", "a0"], "at": ...}]}   x/y/w/h in % of the whole frame
               (keep the top ~15% free for the title); arrows are a0, a1... in order
    image     {"src": "media/shot.png", "caption"}           slow zoom on a picture
    custom    {"brief": "what to show"}                      the LLM backend writes the scene

USAGE
    scenes.py -P project/                 # every line with a "scene"
    scenes.py -P project/ --lines 02-code
    scenes.py -P project/ --draft         # faster, lower quality
    scenes.py -P project/ --only-html     # write and lint, don't render
    scenes.py -P project/ --json

STYLE
    Dark theme by default. SCENE_STYLE="~/.config/lowkeyoutuber/style.css" in the config is
    appended to every scene (override the CSS variables: --bg, --fg, --accent, --muted,
    --code-bg, --font, --mono).
"""

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import backends
import project as proj
from tts import line_spans, probe_duration
from yt_common import CONFIG, die, log, need

WORDS_PER_SECOND = 2.5   # length estimate for lines without narration yet

BASE_CSS = """
:root { --bg: #0f1117; --fg: #e8e8e8; --accent: #ffd166; --muted: #8b93a7; --code-bg: #161a23;
        --hl: rgba(255, 209, 102, 0.18); --font: "DejaVu Sans", sans-serif; --mono: "DejaVu Sans Mono", monospace;
        --k: #c678dd; --s: #98c379; --n: #d19a66; --c: #7f848e; --p: #56b6c2; --f: #61afef; }
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { width: @W@px; height: @H@px; overflow: hidden; background: var(--bg); }
#scene { position: absolute; inset: 0; display: flex; flex-direction: column; justify-content: center;
         align-items: stretch; padding: @PAD@px; gap: @GAP@px; color: var(--fg); font-family: var(--font); }
.title { font-size: @T@px; font-weight: 700; line-height: 1.15; letter-spacing: -0.02em; }
.subtitle { font-size: @S@px; color: var(--muted); line-height: 1.3; }
.item { font-size: @S@px; line-height: 1.35; display: flex; gap: 0.6em; }
.item::before { content: "›"; color: var(--accent); font-weight: 700; }
pre { font-family: var(--mono); font-size: @C@px; line-height: 1.45; white-space: pre; color: var(--fg);
      background: var(--code-bg); border-radius: @R@px; padding: @CP@px; }
.line { display: block; border-radius: 6px; padding: 0 0.2em; margin: 0 -0.2em; }
.tk-k { color: var(--k); } .tk-s { color: var(--s); } .tk-n { color: var(--n); } .tk-c { color: var(--c); }
.tk-p { color: var(--p); } .tk-f { color: var(--f); }
.out-label { font-size: @L@px; color: var(--muted); font-family: var(--font); margin-bottom: 0.3em; }
.term { white-space: pre-wrap; word-break: break-all; }
.term .prompt { color: var(--s); }
.diagram { position: absolute; inset: 0; }
.box { position: absolute; display: flex; align-items: center; justify-content: center; text-align: center;
       border: 3px solid var(--accent); border-radius: @R@px; font-family: var(--mono); font-size: @S@px;
       background: var(--code-bg); padding: 0.2em; }
svg.arrows { position: absolute; inset: 0; width: 100%; height: 100%; }
.img { flex: 1; background-size: cover; background-position: center; border-radius: @R@px; }
"""

KEYWORDS = {
    "c": "auto break case char const continue default do double else enum extern float for goto if inline int "
         "long register restrict return short signed sizeof static struct switch typedef union unsigned void "
         "volatile while bool true false NULL size_t uint8_t uint16_t uint32_t uint64_t int8_t int16_t int32_t "
         "int64_t FILE",
    "sh": "if then else elif fi for while do done case esac in function return export local echo cd",
    "python": "and as assert break class continue def del elif else except False finally for from global if "
              "import in is lambda None nonlocal not or pass raise return True try while with yield",
}
KEYWORDS["bash"] = KEYWORDS["sh"]
KEYWORDS["cpp"] = KEYWORDS["c"] + " class namespace template typename public private protected new delete std"


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------
def esc(s):
    return html.escape(str(s), quote=True)


def sizes(W, H):
    u = min(W, H) / 1080
    return {"W": W, "H": H, "PAD": round(90 * u), "GAP": round(40 * u), "T": round(84 * u), "S": round(50 * u),
            "C": round(44 * u), "L": round(30 * u), "R": round(18 * u), "CP": round(36 * u)}


def css(W, H):
    out = BASE_CSS
    for k, v in sizes(W, H).items():
        out = out.replace(f"@{k}@", str(v))
    extra = os.path.expanduser(CONFIG.get("SCENE_STYLE") or "")
    if extra and os.path.isfile(extra):
        with open(extra, encoding="utf-8") as f:
            out += "\n" + f.read()
    return out


def page(W, H, dur, body, js, lang="es"):
    return f"""<!doctype html>
<html lang="{esc(lang)}"><head><meta charset="UTF-8" />
<meta name="viewport" content="width={W}, height={H}" />
<script src="gsap.min.js"></script>
<style>{css(W, H)}</style></head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-duration="{dur:.3f}" data-width="{W}" data-height="{H}">
<div id="scene" class="clip" data-start="0" data-duration="{dur:.3f}" data-track-index="0">
{body}
</div>
</div>
<script>
const tl = gsap.timeline({{ paused: true }});
{js}
window.__timelines = window.__timelines || {{}};
window.__timelines["main"] = tl;
</script>
</body></html>
"""


def bare(w):
    return re.sub(r"[^\w]", "", w.lower())


class Clock:
    """Resolves "at" values: seconds, or a word of the narration (searched forward from the
    last word found, so the same word said twice maps to the right one)."""

    def __init__(self, words, offset, dur):
        self.words, self.offset, self.dur, self.pos = words, offset, dur, 0

    def at(self, value, default):
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return max(0.0, min(float(value), self.dur - 0.1))
        target = [bare(x) for x in str(value).split() if bare(x)]
        bw = [bare(w["w"]) for w in self.words]
        for i in range(self.pos, len(bw)):
            if bw[i:i + len(target)] == target:
                self.pos = i + 1
                return max(0.0, min(self.words[i]["s"] + self.offset, self.dur - 0.1))
        log(f"  'at': the narration never says {value!r}; using {default:.1f}s")
        return default


def tokens(code, lang):
    """Tiny syntax highlighter: [(class or None, text)]."""
    kw = set(KEYWORDS.get(lang, "").split())
    comment = r"//[^\n]*|/\*.*?\*/" if lang in ("c", "cpp") else r"#[^\n]*"
    rx = re.compile(rf"(?P<c>{comment})|(?P<p>^\s*#\s*\w+[^\n]*)|(?P<s>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')"
                    r"|(?P<n>\b\d[\w.]*\b)|(?P<w>[A-Za-z_]\w*)(?P<call>\s*\()?|(?P<o>.)", re.S | re.M)
    out = []
    for m in rx.finditer(code):
        if m.group("c"):
            out.append(("c", m.group("c")))
        elif m.group("p") and lang in ("c", "cpp"):
            out.append(("p", m.group("p")))
        elif m.group("s"):
            out.append(("s", m.group("s")))
        elif m.group("n"):
            out.append(("n", m.group("n")))
        elif m.group("w"):
            w = m.group("w")
            out.append(("k" if w in kw else "f" if m.group("call") else None, w))
            if m.group("call"):
                out.append((None, m.group("call")))
        else:
            out.append((None, m.group(0)))
    return out


def fit_font(text_lines, W, H, base=None):
    """Monospace font size so the longest line fits the frame (never more than the default)."""
    z = sizes(W, H)
    base = base or (round(z["C"] * 1.15) if H > W else z["C"])
    avail = W - 2 * z["PAD"] - 2 * z["CP"]
    longest = max((len(x) for x in text_lines), default=1) or 1
    return max(round(base * 0.55), min(base, int(avail / (longest * 0.61))))


def code_html(code, lang, typing):
    """<pre> with one span per character (for typing) inside token spans, one .line per line."""
    lines_html, cur, n_chars = [], [], 0
    for cls, text in tokens(code, lang):
        parts = text.split("\n")
        for k, part in enumerate(parts):
            if k:
                lines_html.append("".join(cur))
                cur = []
            if not part:
                continue
            inner = "".join(f'<span class="ch">{esc(c)}</span>' for c in part) if typing else esc(part)
            n_chars += len(part) if typing else 0
            cur.append(f'<span class="tk-{cls}">{inner}</span>' if cls else inner)
    lines_html.append("".join(cur))
    body = "".join(f'<span class="line" id="L{i + 1}">{ln or " "}</span>' for i, ln in enumerate(lines_html))
    return f"<pre>{body}</pre>", n_chars


SAFE_ENV = {k: v for k, v in os.environ.items()
            if not re.search(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", k, re.I)}


def run_c(code, check, run, workdir):
    """Compile (and run) C code for real. Returns the program's output, or None if not run."""
    if not shutil.which("cc"):
        raise SceneError("no C compiler (cc) to check the code on screen")
    src = os.path.join(workdir, "scene.c")
    if check == "fragment":
        code = "#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\nint main(void) {\n" + code + "\nreturn 0;\n}\n"
    with open(src, "w", encoding="utf-8") as f:
        f.write(code)
    exe = os.path.join(workdir, "scene")
    flags = ["-Wall", "-Wno-unused-variable", "-Wno-unused-value", "-Wno-unused-but-set-variable"]
    r = subprocess.run(["cc", *flags, "-o", exe, src], capture_output=True, text=True)
    if r.returncode:
        raise SceneError("the code doesn't compile:\n" + r.stderr.strip()[-800:])
    if not run:
        return None
    try:
        r = subprocess.run([exe], capture_output=True, text=True, timeout=10, cwd=workdir, env=SAFE_ENV)
    except subprocess.TimeoutExpired:
        raise SceneError("the program ran for more than 10 s")
    return (r.stdout + r.stderr).rstrip("\n")


def run_shell(cmd, cwd):
    try:
        r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=20, cwd=cwd,
                           env=dict(SAFE_ENV, TERM="dumb", COLUMNS="80"))
    except subprocess.TimeoutExpired:
        raise SceneError(f"command ran for more than 20 s: {cmd}")
    out = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", r.stdout + r.stderr).rstrip("\n")
    return out


class SceneError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Templates: (body html, timeline js)
# ---------------------------------------------------------------------------
def t_title(sc, clock, dur):
    body = f'<div class="title" id="t">{esc(sc.get("title", ""))}</div>'
    js = 'tl.from("#t", {opacity: 0, y: 40, duration: 0.5, ease: "power3.out"}, 0.1);'
    if sc.get("subtitle"):
        body += f'\n<div class="subtitle" id="st">{esc(sc["subtitle"])}</div>'
        js += f'\ntl.from("#st", {{opacity: 0, y: 20, duration: 0.4}}, {clock.at(sc.get("subtitle_at"), 0.6):.3f});'
    return body, js


def t_text(sc, clock, dur):
    items = sc.get("items") or []
    ats = sc.get("at") or []
    body, js = [], []
    if sc.get("title"):
        body.append(f'<div class="title" id="t">{esc(sc["title"])}</div>')
        js.append('tl.from("#t", {opacity: 0, y: 30, duration: 0.4}, 0.1);')
    default = 0.5
    for i, it in enumerate(items):
        t = clock.at(ats[i] if i < len(ats) else None, default)
        body.append(f'<div class="item" id="i{i}">{esc(it)}</div>')
        js.append(f'tl.from("#i{i}", {{opacity: 0, x: -30, duration: 0.35}}, {t:.3f});')
        default = t + max(0.8, (dur - t) / max(len(items) - i, 1))
    return "\n".join(body), "\n".join(js)


def t_code(sc, clock, dur, workdir, meta, W, H):
    code = sc.get("code") or ""
    lang = (sc.get("lang") or "c").lower()
    typing = sc.get("typing", True)
    output = None
    if lang in ("c",) and sc.get("check", "program" if "main(" in code else "fragment"):
        check = sc.get("check", "program" if "main(" in code else "fragment")
        output = run_c(code, check, bool(sc.get("run")), workdir)
        meta["compiled"] = True
        if output is not None:
            meta["output"] = output
    pre, n_chars = code_html(code, lang, typing)
    fs = fit_font(code.splitlines(), W, H)
    pre = pre.replace("<pre>", f'<pre style="font-size:{fs}px">', 1)
    body = [f'<div class="title" id="t">{esc(sc["title"])}</div>'] if sc.get("title") else []
    body.append(f'<div id="code">{pre}</div>')
    js = ['tl.from("#t", {opacity: 0, y: 30, duration: 0.4}, 0);'] if sc.get("title") else []
    start = clock.at(sc.get("type_at"), 0.3)
    type_dur = 0.0
    if typing and n_chars:
        type_dur = min(max(dur * 0.45, 0.5), n_chars / 35)
        js.append('gsap.set("#code .ch", {opacity: 0});')
        js.append(f'tl.to("#code .ch", {{opacity: 1, duration: 0.01, stagger: {type_dur / n_chars:.5f}}}, {start:.3f});')
    else:
        js.append(f'tl.from("#code", {{opacity: 0, duration: 0.3}}, {start:.3f});')
    for h in sc.get("highlight") or []:
        t = clock.at(h.get("at"), start + type_dur + 0.2)
        sel = ", ".join(f"#L{int(n)}" for n in h.get("lines", []))
        if sel:
            js.append(f'tl.to("{sel}", {{backgroundColor: "var(--hl)", duration: 0.25}}, {t:.3f});')
            if h.get("dur"):
                js.append(f'tl.to("{sel}", {{backgroundColor: "rgba(0,0,0,0)", duration: 0.25}}, {t + float(h["dur"]):.3f});')
    if output is not None:
        lines = output.splitlines()[-12:] or ["(no output)"]
        body.append('<div id="out"><div class="out-label">$ ./a.out</div>'
                    f'<pre style="font-size:{fit_font(lines, W, H)}px">'
                    + "\n".join(esc(x) for x in lines) + "</pre></div>")
        t = clock.at(sc.get("output_at"), min(start + type_dur + 0.4, dur - 0.6))
        js.append(f'tl.from("#out", {{opacity: 0, y: 20, duration: 0.35}}, {t:.3f});')
    return "\n".join(body), "\n".join(js)


def t_terminal(sc, clock, dur, workdir, meta, project_root, W, H):
    cwd = sc.get("cwd")
    cwd = proj.resolve(project_root, cwd) if cwd else workdir
    if not os.path.isdir(cwd):
        raise SceneError(f"terminal cwd not found: {cwd}")
    cmds = sc.get("commands") or []
    body, js, t = [], [], 0.3
    meta["runs"] = []
    for i, c in enumerate(cmds):
        cmd = c["cmd"] if isinstance(c, dict) else str(c)
        out = run_shell(cmd, cwd) if (not isinstance(c, dict) or c.get("run", True)) else None
        meta["runs"].append({"cmd": cmd, "output": out})
        t = clock.at(c.get("at") if isinstance(c, dict) else None, t)
        typed = "".join(f'<span class="ch">{esc(ch)}</span>' for ch in cmd)
        body.append(f'<span class="line" id="c{i}"><span class="prompt">$ </span>{typed}</span>')
        js.append(f'gsap.set("#c{i}", {{opacity: 0}}); tl.set("#c{i}", {{opacity: 1}}, {t:.3f});')
        js.append(f'gsap.set("#c{i} .ch", {{opacity: 0}});')
        td = min(len(cmd) / 25, 1.5)
        js.append(f'tl.to("#c{i} .ch", {{opacity: 1, duration: 0.01, stagger: {td / max(len(cmd), 1):.5f}}}, {t:.3f});')
        t += td + 0.25
        if out:
            shown = out.splitlines()[-14:]
            body.append(f'<span class="line" id="o{i}">' + "\n".join(esc(x) for x in shown) + "</span>")
            js.append(f'gsap.set("#o{i}", {{opacity: 0}}); tl.set("#o{i}", {{opacity: 1}}, {t:.3f});')
            t += 0.4
    shown_lines = ["$ " + r["cmd"] for r in meta["runs"]] + \
        [x for r in meta["runs"] for x in (r["output"] or "").splitlines()[-14:]]
    fs = fit_font([x[:60] for x in shown_lines], W, H)   # past 60 characters, lines wrap
    pre = f'<pre class="term" style="font-size:{fs}px">' + "".join(body) + "</pre>"
    head = f'<div class="title" id="t">{esc(sc["title"])}</div>\n' if sc.get("title") else ""
    return head + pre, "\n".join(js)


def edge_point(b, tx, ty):
    """Where the line from the centre of box b (x, y, w, h in px) towards (tx, ty) leaves the box."""
    x, y, w, h = b
    cx, cy = x + w / 2, y + h / 2
    dx, dy = tx - cx, ty - cy
    if dx == 0 and dy == 0:
        return cx, cy
    k = min((w / 2) / abs(dx) if dx else 1e9, (h / 2) / abs(dy) if dy else 1e9)
    return cx + dx * k, cy + dy * k


def t_diagram(sc, clock, dur, W, H):
    """Boxes and arrows; x, y, w, h in % of the whole frame (keep the top ~15% free for a title)."""
    boxes = {b["id"]: b for b in sc.get("boxes", [])}
    px = {i: (b["x"] * W / 100, b["y"] * H / 100, b.get("w", 20) * W / 100, b.get("h", 12) * H / 100)
          for i, b in boxes.items()}
    sw = max(3, round(min(W, H) / 270))
    body = ['<div class="diagram" id="dg">', f'<svg class="arrows" viewBox="0 0 {W} {H}">',
            '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" '
            'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--accent)"/></marker></defs>']
    for i, a in enumerate(sc.get("arrows", [])):
        if a.get("from") not in px or a.get("to") not in px:
            raise SceneError(f"arrow {a} refers to a box that doesn't exist")
        f, t = px[a["from"]], px[a["to"]]
        x1, y1 = edge_point(f, t[0] + t[2] / 2, t[1] + t[3] / 2)
        x2, y2 = edge_point(t, f[0] + f[2] / 2, f[1] + f[3] / 2)
        body.append(f'<g id="a{i}"><line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                    f'stroke="var(--accent)" stroke-width="{sw}" marker-end="url(#ah)"/>')
        if a.get("label"):
            body.append(f'<text x="{(x1 + x2) / 2:.1f}" y="{(y1 + y2) / 2 - sw * 4:.1f}" fill="var(--muted)" '
                        f'font-size="{sizes(W, H)["L"]}" text-anchor="middle" font-family="DejaVu Sans">'
                        f'{esc(a["label"])}</text>')
        body.append("</g>")
    body.append("</svg>")
    for i, b in boxes.items():
        x, y, w, h = px[i]
        body.append(f'<div class="box" id="b-{esc(i)}" style="left:{x:.0f}px;top:{y:.0f}px;'
                    f'width:{w:.0f}px;height:{h:.0f}px">{esc(b.get("label", i))}</div>')
    body.append("</div>")
    head = f'<div class="title" id="t" style="position:absolute;top:{sizes(W, H)["PAD"]}px;left:{sizes(W, H)["PAD"]}px">' \
           f'{esc(sc["title"])}</div>\n' if sc.get("title") else ""
    js = []
    steps = sc.get("steps") or [{"show": list(boxes) + [f"a{i}" for i in range(len(sc.get("arrows", [])))]}]
    t = 0.3
    for st in steps:
        t = clock.at(st.get("at"), t)
        sel = ", ".join(f"#b-{x}" if x in boxes else f"#{x}" for x in st.get("show", []))
        if sel:
            js.append(f'gsap.set("{sel}", {{opacity: 0}}); tl.to("{sel}", {{opacity: 1, duration: 0.35}}, {t:.3f});')
        t += 0.8
    return head + "\n".join(body), "\n".join(js)


def t_image(sc, clock, dur, scene_dir, project_root):
    src = proj.resolve(project_root, sc.get("src"))
    if not src or not os.path.isfile(src):
        raise SceneError(f"image not found: {sc.get('src')}")
    name = "img" + os.path.splitext(src)[1].lower()
    shutil.copy2(src, os.path.join(scene_dir, name))
    body = f'<div class="img" id="img" style="background-image:url({name})"></div>'
    if sc.get("caption"):
        body += f'\n<div class="subtitle" id="cap">{esc(sc["caption"])}</div>'
    js = f'tl.fromTo("#img", {{scale: 1}}, {{scale: 1.08, duration: {dur:.3f}, ease: "none"}}, 0);'
    return body, js


CUSTOM_PROMPT = """Write one HyperFrames scene: a complete HTML document, {W}x{H} pixels, exactly {dur:.2f} seconds.
What it must show: {brief}
Narration said during the scene, with each word's start time in seconds: {words}

Rules (the renderer seeks frames, so they are strict):
- Load GSAP with <script src="gsap.min.js"></script> (a local file). No other scripts, no network, no fonts from the web.
- <body> has ONE root <div id="root" data-composition-id="main" data-start="0" data-duration="{dur:.3f}" data-width="{W}" data-height="{H}">,
  and inside it ONE <div id="scene" class="clip" data-start="0" data-duration="{dur:.3f}"> that holds everything (normal CSS layout inside).
- All motion is on one paused GSAP timeline registered as: const tl = gsap.timeline({{paused: true}}); window.__timelines = window.__timelines || {{}}; window.__timelines["main"] = tl;
  Put tweens on tl at explicit times. No setTimeout, requestAnimationFrame, Date.now() or Math.random().
- html, body: width {W}px; height {H}px; overflow hidden. Dark background (#0f1117), light text, accent #ffd166, fonts "DejaVu Sans" / "DejaVu Sans Mono".
- Text must be big enough to read on a phone and stay inside the frame with a margin of at least {pad}px.
- Language of any text: {lang}. Don't show program output you made up: show only code or text from the brief.
Return ONLY the HTML document."""


def t_custom(sc, clock, dur, W, H, lang, errors=None):
    words = " ".join(f"{w['w']}@{w['s'] + clock.offset:.1f}" for w in clock.words) or "(none)"
    prompt = CUSTOM_PROMPT.format(W=W, H=H, dur=dur, brief=sc.get("brief", ""), words=words, lang=lang,
                                  pad=sizes(W, H)["PAD"])
    if errors:
        prompt += "\n\nYour previous version had these problems, fix them:\n" + "\n".join(errors)
    text, b = backends.call("llm", prompt)
    m = re.search(r"<!doctype html>.*</html>", text, re.S | re.I)
    if not m:
        raise SceneError(f"the LLM ({b.name}) didn't return an HTML document")
    return m.group(0)


# ---------------------------------------------------------------------------
def build(sc, line, W, H, dur, clock, scene_dir, workdir, meta, project_root, lang, errors=None):
    kind = sc.get("type")
    if kind == "custom":
        return t_custom(sc, clock, dur, W, H, lang, errors)
    if kind == "title":
        body, js = t_title(sc, clock, dur)
    elif kind == "text":
        body, js = t_text(sc, clock, dur)
    elif kind == "code":
        body, js = t_code(sc, clock, dur, workdir, meta, W, H)
    elif kind == "terminal":
        body, js = t_terminal(sc, clock, dur, workdir, meta, project_root, W, H)
    elif kind == "diagram":
        body, js = t_diagram(sc, clock, dur, W, H)
    elif kind == "image":
        body, js = t_image(sc, clock, dur, scene_dir, project_root)
    else:
        raise SceneError(f"unknown scene type {kind!r} (title, text, code, terminal, diagram, image, custom)")
    return page(W, H, dur, body, js, lang)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def main():
    p = argparse.ArgumentParser(description="Animated scenes for script lines, rendered with HyperFrames.")
    p.add_argument("-P", "--project", required=True)
    p.add_argument("--lines", help="only these line ids, comma-separated")
    p.add_argument("--backend", help="render backend (default: config RENDER / profile)")
    p.add_argument("--draft", action="store_true", help="faster, lower-quality render")
    p.add_argument("--only-html", action="store_true", help="write and lint the scenes, don't render")
    p.add_argument("-f", "--force", action="store_true", help="render again even if nothing changed")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    need("ffprobe")

    try:
        pj = proj.load(args.project)
        script = proj.load_script(args.project)
    except proj.ProjectError as e:
        die(str(e))
    root = proj.root(args.project)
    lines = script["lines"]
    lang = script.get("lang") or pj.get("lang") or "es"
    want = {x.strip() for x in args.lines.split(",")} if args.lines else None
    if want and want - {line["id"] for line in lines}:
        die(f"no such lines: {', '.join(sorted(want - {line['id'] for line in lines}))}")
    try:
        rb = backends.pick("render", args.backend)
    except backends.BackendError as e:
        die(str(e))

    # how long each line lasts on screen: from its narration if it exists, else an estimate
    durs, have = [], []
    for line in lines:
        wav = os.path.join(root, "narration", line["id"] + ".wav")
        if os.path.isfile(wav):
            durs.append(probe_duration(wav))
            have.append(True)
        else:
            durs.append(float(line.get("dur") or len(line["text"].split()) / WORDS_PER_SECOND + 0.5))
            have.append(False)
    lead, spans = line_spans(lines, durs)
    if not all(have):
        log("Some lines have no narration yet (tts.py): their scenes use an estimated length. "
            "Run scenes.py again after tts.py so they match the voice.")

    summary = {"rendered": [], "unchanged": [], "edited_by_hand": [], "failed": [], "backend": rb.name}
    first = next(iter(pj["formats"]))
    for i, line in enumerate(lines):
        sc = line.get("scene")
        if not sc or (want and line["id"] not in want):
            continue
        lid, dur = line["id"], round(spans[i], 3)
        scene_dir = os.path.join(root, "scenes", lid)
        os.makedirs(scene_dir, exist_ok=True)
        meta_path = os.path.join(root, "scenes", lid + ".json")
        meta_old = read_json(meta_path) or {}
        words = (read_json(os.path.join(root, "narration", lid + ".words.json")) or {}).get("words", [])
        offset = lead if i == 0 else 0.0
        variants, meta = {}, {"type": sc.get("type"), "dur": dur}
        try:
            for fmt, (W, H) in pj["formats"].items():
                tag = "" if fmt == first else "_" + fmt.replace(":", "x")
                fdir = scene_dir if fmt == first else scene_dir + tag
                os.makedirs(fdir, exist_ok=True)
                index = os.path.join(fdir, "index.html")
                current = open(index, encoding="utf-8").read() if os.path.isfile(index) else None
                generated_before = (meta_old.get("generated") or {}).get(fmt)
                # a file we have no record of generating is somebody's: keep it
                edited = current is not None and (not generated_before or
                                                  backends.input_hash(current) != generated_before)
                if edited:
                    summary["edited_by_hand"].append(f"{lid} {fmt}")
                    doc = current
                else:
                    gen_key = backends.input_hash(sc, line["text"], words, W, H, dur, lang, css(W, H))
                    if (current is not None and not args.force and meta_old.get("gen_key", {}).get(fmt) == gen_key
                            and sc.get("type") == "custom"):
                        doc = current   # don't ask the LLM again for an unchanged custom scene
                    else:
                        with tempfile.TemporaryDirectory() as work:
                            doc = build(sc, line, W, H, dur, Clock(words, offset, dur), fdir, work, meta, root, lang)
                    meta.setdefault("gen_key", {})[fmt] = gen_key
                with open(index, "w", encoding="utf-8") as f:
                    f.write(doc)
                if not edited:   # remembered even if lint or the render fails below
                    meta.setdefault("generated", {})[fmt] = backends.input_hash(doc)
                problems = rb.lint(fdir) if hasattr(rb, "lint") else []
                if problems and sc.get("type") == "custom" and not edited:
                    log(f"  {lid}: fixing lint problems ({len(problems)})...")
                    with tempfile.TemporaryDirectory() as work:
                        doc = build(sc, line, W, H, dur, Clock(words, offset, dur), fdir, work, meta, root, lang,
                                    errors=problems)
                    with open(index, "w", encoding="utf-8") as f:
                        f.write(doc)
                    meta["generated"][fmt] = backends.input_hash(doc)
                    problems = rb.lint(fdir)
                if problems:
                    raise SceneError("lint: " + "; ".join(problems))
                if edited and generated_before:
                    meta.setdefault("generated", {})[fmt] = generated_before
                out = os.path.join(root, "scenes", lid + tag + ".mp4")
                imgs = [backends.file_hash(os.path.join(fdir, n)) for n in sorted(os.listdir(fdir))
                        if n.startswith("img.")]   # an image can change under the same path
                rkey = backends.input_hash(doc, W, H, pj["fps"], args.draft, rb.model, imgs)
                if args.only_html:
                    continue
                if not args.force and meta_old.get("render_key", {}).get(fmt) == rkey and os.path.isfile(out):
                    summary["unchanged"].append(f"{lid} {fmt}")
                else:
                    log(f"{lid}: rendering {fmt} ({dur:.1f}s, {rb.name})...")
                    rb.render_scene(fdir, out, pj["fps"], W, H, draft=args.draft)
                    summary["rendered"].append(f"{lid} {fmt}")
                meta.setdefault("render_key", {})[fmt] = rkey
                variants[fmt] = proj.rel(args.project, out)
        except (SceneError, backends.BackendError) as e:
            log(f"{lid}: {e}")
            summary["failed"].append({"id": lid, "error": str(e)})
            if meta.get("generated"):   # keep who wrote index.html, or the next run calls it a hand edit
                # each generated hash goes with the inputs (gen_key) that made it: an old gen_key next
                # to new HTML would let the old inputs reuse the wrong scene
                keep = dict(meta_old, generated={**(meta_old.get("generated") or {}), **meta["generated"]})
                keys = dict(meta_old.get("gen_key") or {})
                for fmt in meta["generated"]:
                    if fmt in (meta.get("gen_key") or {}):   # absent: a kept hand edit, old pair stands
                        keys[fmt] = meta["gen_key"][fmt]
                keep["gen_key"] = keys
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(keep, f, ensure_ascii=False, indent=2)
            continue
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        if variants:
            pj.setdefault("media", {})[f"scene:{lid}"] = {"file": variants[first], "kind": "video",
                                                          "variants": variants}
    proj.set_state(pj, "scenes", "failed" if summary["failed"] else "done")
    proj.save(args.project, pj)
    log(f"Scenes: {len(summary['rendered'])} rendered, {len(summary['unchanged'])} unchanged, "
        f"{len(summary['failed'])} failed" + (f", kept hand edits: {', '.join(summary['edited_by_hand'])}"
                                              if summary["edited_by_hand"] else ""))
    if args.json:
        json.dump(summary, sys.stdout, ensure_ascii=False)
        print()
    if summary["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
