"""
yt_common.py - Code shared by the toolkit's Python scripts. Not meant to be run directly.

CONFIG
    ~/.config/lowkeyoutuber/config holds your defaults as KEY="value" lines,
    so you don't repeat options on every run. Command-line options win.
        LANGUAGE="es"                      Whisper language (-l)
        JARGON="PM, Neovim, Lua"           names Whisper should spell right (--prompt)
        CAM="934:384:346:303"              facecam box in the recording (clips/thumbnail --cam)
        FOOTER="~/.config/lowkeyoutuber/footer.txt"   appended to descriptions
        FIXES="~/.config/lowkeyoutuber/fixes.txt"     transcript fixes (this is the default)
        LLM_BACKEND="auto"                 auto, claude, codex, opencode, ollama
        LLM_MODEL=""                       model for that backend (empty = its default)

TRANSCRIPT CACHE
    Every script gets its transcript through get_transcript(), which runs
    Whisper once and stores the words with their timestamps in
    <video>.words.json. Any later run, by any of the scripts, reuses it.
    The cache is redone automatically when the video file, the audio track,
    the Whisper model or the requested language changes, or with
    --retranscribe. A different --prompt only prints a warning: the cache may
    have been fixed by hand and is kept.
    --audio-from FILE transcribes another file with the same timeline (the
    export before process-voice.sh cleaned it: Whisper does better on it)
    and caches the result for both files.

FIXES
    fixes.txt has one "wrong => right" per line for words Whisper keeps getting
    wrong ("atoy => atoi", "main .si => main.c"). They are applied every time a
    transcript is read, so subtitles, captions and LLM input all get them, and
    a new line also fixes old caches. Matching ignores case and punctuation.

LLM
    Text tasks (clip picking, chapters, titles...) use, in this order, the first
    AI CLI you already have and that works: Claude Code, Codex, OpenCode. They
    run with no tools, in an empty folder, using your existing subscription.
    Without any of them, a local Ollama model is used (free). Note that with a
    CLI the transcript is sent to that provider.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from typing import NoReturn

NO_SPACE_LANGS = {"zh", "ja", "th", "lo", "my", "km"}
SENTENCE_END = (".", "?", "!", "…", "。", "？", "！")
SAMPLE_RATE = 16000   # what Whisper expects

LANG_NAMES = {"es": "Spanish", "en": "English", "fr": "French", "de": "German", "it": "Italian",
              "pt": "Portuguese", "ca": "Catalan", "gl": "Galician", "eu": "Basque", "nl": "Dutch",
              "pl": "Polish", "ru": "Russian", "ja": "Japanese", "zh": "Chinese", "ko": "Korean"}

# Whisper imitates the style of its prompt. A short punctuated sentence in the
# right language keeps it writing capitals and punctuation, which it sometimes
# drops otherwise (and sentence detection depends on punctuation).
PUNCT_PROMPTS = {
    "es": "Hola, ¿qué tal? Bienvenidos. En este vídeo vamos a ver cómo funciona.",
    "en": "Hi, how are you? Welcome. In this video, we'll see how it works.",
    "fr": "Bonjour, ça va ? Bienvenue. Dans cette vidéo, on va voir comment ça marche.",
    "de": "Hallo, wie geht's? Willkommen. In diesem Video sehen wir, wie es funktioniert.",
    "it": "Ciao, come va? Benvenuti. In questo video vediamo come funziona.",
    "pt": "Olá, tudo bem? Bem-vindos. Neste vídeo vamos ver como funciona.",
    "ca": "Hola, què tal? Benvinguts. En aquest vídeo veurem com funciona.",
    "gl": "Ola, que tal? Benvidos. Neste vídeo imos ver como funciona.",
}


CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "lowkeyoutuber")


def load_config():
    conf = {}
    try:
        with open(os.path.join(CONFIG_DIR, "config"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = (x.strip() for x in line.split("=", 1))
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                        value = value[1:-1]
                    conf[key] = os.path.expanduser(value)
    except OSError:
        pass
    return conf


CONFIG = load_config()


def log(*a, end="\n"):
    print(*a, file=sys.stderr, end=end, flush=True)


def die(msg) -> NoReturn:
    sys.exit(f"Error: {msg}")


def need(*tools):
    for tool in tools:
        if not shutil.which(tool):
            die(f"{tool} not installed")


def num(x, default):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def lang_name(code):
    return LANG_NAMES.get(code, code or "the language of the transcript")


def clock(t, hours=False):
    """Seconds -> m:ss, or h:mm:ss when hours is true (YouTube chapter style)."""
    t = int(t)
    if hours:
        return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"
    return f"{t // 60}:{t % 60:02d}"


# ---------------------------------------------------------------------------
# Transcription (faster-whisper), cached per video
# ---------------------------------------------------------------------------
def add_transcript_args(p):
    g = p.add_argument_group("transcription")
    g.add_argument("-l", "--language", default=CONFIG.get("LANGUAGE") or None,
                   help="language code, e.g. en, es, fr (default: config LANGUAGE, else auto-detect)")
    g.add_argument("-m", "--model", default="large-v3-turbo",
                   help="Whisper model: small, medium, large-v3, large-v3-turbo (default large-v3-turbo)")
    g.add_argument("-t", "--track", "--audio-track", dest="track", type=int, default=0,
                   help="audio track with the voice, 0-based (default 0)")
    g.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    g.add_argument("--compute-type", default="auto",
                   help="default: int8_float16 on GPU (fits 4 GB VRAM), int8 on CPU")
    g.add_argument("--prompt", default=CONFIG.get("JARGON") or None,
                   help="names/jargon Whisper should spell right, e.g. 'PipeWire, OBS' (default: config JARGON)")
    g.add_argument("--retranscribe", action="store_true", help="ignore the cached transcript")
    g.add_argument("--audio-from", metavar="FILE",
                   help="transcribe the audio of FILE instead (same timeline, e.g. the export before "
                        "process-voice.sh cleaned it); the transcript is cached for both files")
    g.add_argument("--audio-from-track", type=int, default=0,
                   help="audio track of the video itself, stored in its cache with --audio-from (default 0)")
    return g


def cuda_lib_dirs():
    """cuBLAS/cuDNN from the pip wheels (nvidia-cublas-cu12, nvidia-cudnn-cu12), if installed."""
    import importlib
    dirs = []
    for mod in ("nvidia.cublas.lib", "nvidia.cudnn.lib"):
        try:
            dirs += list(importlib.import_module(mod).__path__)
        except ImportError:
            pass
    return dirs


def ensure_cuda_env():
    """faster-whisper's CUDA backend loads cuBLAS/cuDNN through LD_LIBRARY_PATH,
    which only counts when the process starts: restart once with it set."""
    cur = [d for d in os.environ.get("LD_LIBRARY_PATH", "").split(":") if d]
    missing = [d for d in cuda_lib_dirs() if d not in cur]
    if missing and not os.environ.get("YT_CUDA_REEXEC"):
        env = dict(os.environ, LD_LIBRARY_PATH=":".join(missing + cur), YT_CUDA_REEXEC="1")
        os.execve(sys.executable, [sys.executable] + sys.argv, env)


def free_ollama_vram():
    """Ollama keeps its model in VRAM for minutes after a request; with a small GPU
    Whisper then runs out of memory. Ask it to unload everything (no-op if it isn't running)."""
    base = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    base = (base if base.startswith("http") else "http://" + base).rstrip("/")
    try:
        with urllib.request.urlopen(base + "/api/ps", timeout=3) as r:
            loaded = [m["name"] for m in json.load(r).get("models", [])]
        for name in loaded:
            body = json.dumps({"model": name, "keep_alive": 0}).encode()
            urllib.request.urlopen(urllib.request.Request(base + "/api/generate", body,
                                                          {"Content-Type": "application/json"}), timeout=30).close()
            log(f"Unloaded {name} from Ollama to free GPU memory.")
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        pass


def load_whisper(args):
    """Load the model on the GPU when possible, falling back to the CPU."""
    from faster_whisper import WhisperModel
    import ctranslate2
    use_cuda = args.device == "cuda" or (args.device == "auto" and ctranslate2.get_cuda_device_count() > 0)
    if use_cuda:
        ct = "int8_float16" if args.compute_type == "auto" else args.compute_type
        try:
            log(f"Loading Whisper '{args.model}' on the GPU ({ct}, first run downloads it)...")
            return WhisperModel(args.model, device="cuda", compute_type=ct)
        except (RuntimeError, ValueError, OSError) as e:
            if args.device == "cuda":
                die(f"CUDA failed: {e}")
            log(f"GPU not usable ({e}), using the CPU. "
                "For CUDA: pip install nvidia-cublas-cu12 'nvidia-cudnn-cu12==9.*'")
    ct = "int8" if args.compute_type == "auto" else args.compute_type
    log(f"Loading Whisper '{args.model}' on the CPU ({ct}, first run downloads it)...")
    return WhisperModel(args.model, device="cpu", compute_type=ct)


def load_audio(video, track):
    """Decode one audio track to 16 kHz mono float32 (Whisper's input)."""
    import numpy as np   # installed together with faster-whisper
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", video, "-map", f"0:a:{track}",
                        "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"], capture_output=True)
    if r.returncode:
        die(f"could not read audio track {track} of {video}: "
            + r.stderr.decode(errors="replace").strip()[-300:])
    return np.frombuffer(r.stdout, np.float32)


def file_src(video, track, args):
    st = os.stat(video)
    return {"size": st.st_size, "mtime": int(st.st_mtime), "track": track, "model": args.model,
            "prompt": args.prompt}


def save_transcript(data, video):
    try:
        with open(os.path.splitext(video)[0] + ".words.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError:
        log("(could not write transcript cache)")


def cached_transcript(args, video, src):
    """The cached transcript of video if it still belongs to it, else None.
    Only the video file, track, model and language decide: a cache made with a
    different --prompt is kept (it may have been fixed by hand), with a warning."""
    cache = os.path.splitext(video)[0] + ".words.json"
    if args.retranscribe or not os.path.isfile(cache):
        return None
    try:
        with open(cache, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    old = data.get("src") or {}
    same = all(old.get(k) == src[k] for k in ("size", "mtime", "track", "model"))
    if not same or args.language not in (None, data.get("language")):
        log("Cached transcript doesn't match (video, track, model or language changed), redoing it.")
        return None
    log(f"Using cached transcript: {cache}" + (f" (made from {old['from']})" if old.get("from") else ""))
    if old.get("prompt") != src["prompt"]:
        log(f"  It was made with --prompt {old.get('prompt')!r}, not {src['prompt']!r}. "
            "Use --retranscribe to redo it with the new one.")
    return data


def get_transcript(args, video):
    """Return {"language", "duration", "src", "words": [{"w", "s", "e"}, ...]},
    with the fixes from fixes.txt applied."""
    data = raw_transcript(args, video)
    data["words"] = apply_fixes(data["words"], load_fixes())
    return data


def raw_transcript(args, video):
    if args.audio_from and os.path.abspath(args.audio_from) != os.path.abspath(video):
        src = file_src(video, args.audio_from_track, args)
        data = cached_transcript(args, video, src)
        if data:
            return data
        if not os.path.isfile(args.audio_from):
            die(f"--audio-from file not found: {args.audio_from}")
        log(f"Transcribing the audio of {args.audio_from} (track {args.track}) for {video}")
        data = dict(raw_transcript(argparse.Namespace(**{**vars(args), "audio_from": None}), args.audio_from))
        data["src"] = dict(src, **{"from": os.path.abspath(args.audio_from)})
        save_transcript(data, video)
        return data

    src = file_src(video, args.track, args)
    data = cached_transcript(args, video, src)
    if data:
        return data

    need("ffmpeg")
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        die("faster-whisper is not installed. Activate the venv (source ~/.venvs/subs/bin/activate) "
            "or run: pip install faster-whisper")
    if args.device != "cpu":
        ensure_cuda_env()
        free_ollama_vram()

    model = load_whisper(args)
    log(f"Reading audio track {args.track}...")
    audio = load_audio(video, args.track)
    duration = len(audio) / SAMPLE_RATE
    language = args.language
    if not language:   # detect first, so the prompt below can be in the right language
        language, prob, _ = model.detect_language(audio, vad_filter=True, language_detection_segments=3)
        log(f"Language: {lang_name(language)} ({prob:.0%} sure). Wrong? Use -l.")
    prompt = " ".join(p for p in (PUNCT_PROMPTS.get(language), args.prompt) if p) or None
    segs, info = model.transcribe(
        audio,
        language=language,
        beam_size=5,
        vad_filter=True,               # skip silence: faster, fewer hallucinations
        word_timestamps=True,
        condition_on_previous_text=False,
        initial_prompt=prompt)
    log(f"Transcribing {duration:.0f}s of {lang_name(info.language)}...")

    words = []
    for seg in segs:
        for w in seg.words or []:
            t = w.word.strip()
            if t:
                words.append({"w": t, "s": round(w.start, 3), "e": round(w.end, 3)})
        log(f"\rTranscribing: {seg.end:.0f}/{duration:.0f}s", end="")
    log("")

    data = {"language": info.language, "duration": duration, "src": src, "words": words}
    save_transcript(data, video)
    return data


# ---------------------------------------------------------------------------
# Transcript fixes: words Whisper keeps getting wrong, from fixes.txt
# ---------------------------------------------------------------------------
LEAD_PUNCT, TRAIL_PUNCT = "¿¡\"'(«“", ".,;:!?…\"')»”"


def bare_word(w):
    return w.strip(LEAD_PUNCT + TRAIL_PUNCT).lower()


def load_fixes(path=None):
    """Read "wrong => right" lines (# comments). Matching ignores case and the
    punctuation around words; "wrong" can be several words ("a toy => atoi")."""
    path = path or CONFIG.get("FIXES") or os.path.join(CONFIG_DIR, "fixes.txt")
    fixes = []
    try:
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                wrong, arrow, right = line.partition("=>")
                if not arrow or not wrong.split():
                    log(f"{path}:{n}: expected 'wrong => right', skipping")
                    continue
                tokens = [b for b in (bare_word(t) for t in wrong.split()) if b]
                if tokens:
                    fixes.append((tokens, right.strip()))
    except OSError:
        pass
    fixes.sort(key=lambda fx: -len(fx[0]))   # longest match first
    return fixes


def apply_fixes(words, fixes):
    if not fixes:
        return words
    bare = [bare_word(w["w"]) for w in words]
    out, i = [], 0
    while i < len(words):
        for wrong, right in fixes:
            if bare[i:i + len(wrong)] == wrong:
                first, last = words[i], words[i + len(wrong) - 1]
                lead = first["w"][:len(first["w"]) - len(first["w"].lstrip(LEAD_PUNCT))]
                trail = last["w"][len(last["w"].rstrip(TRAIL_PUNCT)):]
                if right:
                    out.append({"w": lead + right + trail, "s": first["s"], "e": last["e"]})
                i += len(wrong)
                break
        else:
            out.append(words[i])
            i += 1
    return out


# ---------------------------------------------------------------------------
# Facecam box check
# ---------------------------------------------------------------------------
def check_cam(video, cam, report_dir=None):
    """Warn when the facecam box (x, y, w, h) has no movement in it: a wrong
    box doesn't fail, it just gives empty facecams. Measured on real
    recordings: a box on the face moves 0.13-0.26, a box on the screen < 0.01."""
    import numpy as np
    x, y, w, h = cam
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video],
                       capture_output=True, text=True)
    dur = num(r.stdout.strip(), 0)
    moving = []
    for i in range(8):
        r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{dur * (i + 0.5) / 8:.2f}", "-i", video,
                            "-frames:v", "4", "-vf", f"fps=4,crop={w}:{h}:{x}:{y},scale=96:96,format=gray",
                            "-f", "rawvideo", "-"], capture_output=True)
        frames = np.frombuffer(r.stdout, np.uint8)
        if r.returncode or frames.size < 2 * 96 * 96:
            return True   # can't tell (box outside the frame is reported by ffmpeg later)
        frames = frames[:frames.size // (96 * 96) * 96 * 96].reshape(-1, 96, 96).astype(np.int16)
        moving.append((np.abs(np.diff(frames, axis=0)) > 2).mean())
    if np.median(moving) >= 0.03:
        return True
    log(f"\nWARNING: nothing moves inside the facecam box {x}:{y}:{w}:{h}. Did the facecam move in OBS?")
    if report_dir:
        img = os.path.join(report_dir, "cam_check.jpg")
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{dur / 2:.2f}", "-i", video,
                        "-frames:v", "1", "-vf", f"drawbox={x}:{y}:{w}:{h}:color=red:t=4", img])
        log(f"  The box is drawn in red on: {img}")
    log("  Pass the right box with --cam x:y:w:h (or CAM in the config).\n")
    return False


def word_sep(data):
    return "" if data.get("language") in NO_SPACE_LANGS else " "


def make_sentences(words, sep):
    out, cur = [], []

    def flush():
        if cur:
            out.append({"start": cur[0]["s"], "end": cur[-1]["e"],
                        "text": sep.join(w["w"] for w in cur)})
            cur.clear()

    for w in words:
        if cur and w["s"] - cur[-1]["e"] > 0.8:
            flush()
        cur.append(w)
        if w["w"].endswith(SENTENCE_END) or len(cur) >= 40:
            flush()
    flush()
    return out


def chunk_transcript(sentences, max_chars, overlap=4):
    chunks, i = [], 0
    while i < len(sentences):
        lines, size, j = [], 0, i
        while j < len(sentences):
            line = f"[{sentences[j]['start']:.1f}] {sentences[j]['text']}"
            if lines and size + len(line) > max_chars:
                break
            lines.append(line)
            size += len(line) + 1
            j += 1
        chunks.append("\n".join(lines))
        if j >= len(sentences):
            break
        i = max(j - overlap, i + 1)
    return chunks


# ---------------------------------------------------------------------------
# Video encoding
# ---------------------------------------------------------------------------
def video_encoder(name, quality):
    """ffmpeg video encoder args. "auto" = NVENC on the NVIDIA GPU if ffmpeg has it,
    else libx264. quality is a CRF/CQ value (lower = better, 18 ~ visually lossless)."""
    if name == "auto":
        r = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True)
        name = "h264_nvenc" if " h264_nvenc " in r.stdout else "libx264"
    if name == "libx264":
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(quality)]
    if name.endswith("_nvenc"):
        return ["-c:v", name, "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", str(quality), "-b:v", "0"]
    return ["-c:v", name, "-b:v", "12M"]


# ---------------------------------------------------------------------------
# LLM: an AI CLI you already pay for (Claude Code, Codex, OpenCode), else Ollama
# ---------------------------------------------------------------------------
CLI_BACKENDS = ("claude", "codex", "opencode")


class LLMError(OSError):
    pass


def add_llm_args(p):
    g = p.add_argument_group("LLM")
    g.add_argument("--backend", choices=["auto", *CLI_BACKENDS, "ollama"],
                   default=CONFIG.get("LLM_BACKEND") or "auto",
                   help="auto (default): the first that works of claude, codex, opencode, then ollama")
    g.add_argument("--llm", default=CONFIG.get("LLM_MODEL") or None,
                   help="model for the backend (default: the CLI's own default; Ollama qwen2.5:7b)")
    g.add_argument("--ollama-url", default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    g.add_argument("--num-ctx", type=int, default=8192, help="Ollama context size (default 8192)")
    g.add_argument("--chunk-chars", type=int,
                   help="transcript characters per request (default: 9000 for Ollama, the whole transcript otherwise)")
    return g


def run_cli(backend, model, prompt, timeout=900):
    """One prompt through an AI CLI, without tools, in an empty folder. Returns its answer."""
    with tempfile.TemporaryDirectory() as tmp:
        answer, stdin = None, prompt
        if backend == "claude":
            cmd = ["claude", "-p", "--tools", "", "--no-session-persistence", "--strict-mcp-config",
                   "--setting-sources", "", "--output-format", "text",
                   "--system-prompt", "Follow the output format instructions exactly."]
            cmd += ["--model", model] if model else []
        elif backend == "codex":
            answer = os.path.join(tmp, "answer.txt")
            cmd = ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only", "-o", answer]
            cmd += (["-m", model] if model else []) + ["-"]   # "-": prompt from stdin
        else:   # opencode: long prompts go in an attached file
            with open(os.path.join(tmp, "prompt.md"), "w", encoding="utf-8") as f:
                f.write(prompt)
            cmd = ["opencode", "run", "-f", "prompt.md"] + (["-m", model] if model else []) + [
                "Follow the instructions in the attached prompt.md exactly. Reply with the requested output only."]
            stdin = None
        try:
            r = subprocess.run(cmd, input=stdin, capture_output=True, text=True, cwd=tmp, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise LLMError(f"{backend} timed out")
        text = r.stdout
        if answer and os.path.isfile(answer):
            with open(answer, encoding="utf-8") as f:
                text = f.read()
        text = re.sub(r"\x1b\[[0-9;]*m", "", text).strip()   # strip terminal colors
        if r.returncode or not text or "model_not_found" in text:
            raise LLMError(f"{backend} failed: " + (r.stderr.strip() or text)[-300:])
        return text


def setup_llm(args):
    """Pick the backend (args.backend) and the chunk size for the transcript."""
    if args.backend == "auto":
        candidates = [b for b in CLI_BACKENDS if shutil.which(b)] + ["ollama"]
    else:
        candidates = [args.backend]
    for backend in candidates:
        if backend == "ollama":
            if args.backend == "ollama":
                args.llm = args.llm or "qwen2.5:7b"
            elif ":" not in (args.llm or ""):   # --llm was meant for a CLI: use Ollama's default
                args.llm = "qwen2.5:7b"
            check_ollama(args)
        else:
            if not shutil.which(backend):
                die(f"{backend} is not installed")
            try:
                ok = parse_json(run_cli(backend, args.llm, 'Return ONLY this JSON: {"ok": true}', timeout=120))
            except (LLMError, ValueError) as e:
                log(f"{backend} is not usable ({str(e).strip()[:200]}), trying the next option.")
                continue
            if not isinstance(ok, dict) or ok.get("ok") is not True:
                log(f"{backend} gave an unexpected answer, trying the next option.")
                continue
        args.backend = backend
        break
    else:
        die("no usable LLM (claude, codex, opencode or ollama)")
    if args.chunk_chars is None:
        args.chunk_chars = 9000 if args.backend == "ollama" else 200_000
    log(f"LLM: {args.backend}" + (f" ({args.llm})" if args.llm else ""))


def llm(args, prompt, json_mode=False):
    if args.backend == "ollama":
        return ollama(args, prompt, json_mode)
    if json_mode:
        prompt += "\n\nAnswer with the JSON only: no code fences, no comments."
    return run_cli(args.backend, args.llm, prompt)


def ollama_url(args):
    url = args.ollama_url
    if not url.startswith("http"):
        url = "http://" + url
    return url.rstrip("/")


def check_ollama(args):
    try:
        with urllib.request.urlopen(ollama_url(args) + "/api/tags", timeout=5) as r:
            names = [m["name"] for m in json.load(r).get("models", [])]
    except (urllib.error.URLError, OSError):
        die(f"Cannot reach Ollama at {ollama_url(args)}. Start it with: sudo systemctl start ollama")
    if args.llm not in names and f"{args.llm}:latest" not in names:
        die(f"Model '{args.llm}' not found. Run: ollama pull {args.llm}")


def ollama(args, prompt, json_mode=False):
    body = {"model": args.llm, "stream": False,
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0.3, "num_ctx": args.num_ctx}}
    if json_mode:
        body["format"] = "json"
    req = urllib.request.Request(ollama_url(args) + "/api/chat", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.load(r)["message"]["content"]


def parse_json(raw):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        a, b = raw.find("{"), raw.rfind("}")
        return json.loads(raw[a:b + 1])


def summarize(args, sentences, lang):
    parts = []
    for n, chunk in enumerate(chunk_transcript(sentences, args.chunk_chars, overlap=0), 1):
        log(f"Summarizing part {n}...")
        parts.append(llm(args, f"Summarize this part of a video transcript in 2-3 sentences, written in {lang}. "
                                  "Lines start with their time in seconds.\n\n" + chunk))
    combined = "\n".join(f"- {p.strip()}" for p in parts)
    return llm(args, "Below are summaries of consecutive parts of one video. Write one concise summary of "
                        f"the whole video (max 120 words), then a line 'Key points:' followed by 3-6 bullet "
                        f"points. Write everything in {lang}. Plain text only.\n\n" + combined).strip()
