"""Local video-adaptive music composer: pure-Python MIDI + FluidSynth + FFmpeg mastering."""

import os
import shutil
import subprocess
import tempfile

import backends
from backends import BackendError
from backends.music._styles import build_midi, load_styles, style_from_prompt
from yt_common import CONFIG

DEFAULT_SOUNDFONTS = (
    "/usr/share/soundfonts/FluidR3_GM.sf2",
    "/usr/share/soundfonts/FluidR3_GS.sf2",
    "/usr/share/sounds/sf2/FluidR3_GM.sf2",
    "/usr/share/sounds/sf2/default-GM.sf2",
)


def find_soundfont(explicit=None):
    candidates = [explicit, CONFIG.get("SOUNDFONT"), *DEFAULT_SOUNDFONTS]
    for path in candidates:
        if path:
            p = os.path.expanduser(path)
            if os.path.isfile(p):
                return p
    for root in ("/usr/share/soundfonts", "/usr/share/sounds/sf2"):
        if os.path.isdir(root):
            for name in sorted(os.listdir(root)):
                if name.lower().endswith((".sf2", ".sf3")):
                    return os.path.join(root, name)
    return None


def cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    d = os.path.join(base, "lowkeyoutuber", "music")
    os.makedirs(d, exist_ok=True)
    return d


class Backend(backends.Base):
    caps = {"local": True, "generative": True, "sections": True, "styles": sorted(load_styles())}

    def check(self):
        if not shutil.which("fluidsynth"):
            return False, "fluidsynth not installed (sudo pacman -S fluidsynth)"
        if not shutil.which("ffmpeg"):
            return False, "ffmpeg not installed"
        if not find_soundfont():
            return False, "no .sf2 soundfont found (install soundfont-fluid or set SOUNDFONT)"
        return True, ""

    def compose(self, out_path, prompt="", duration=30.0, style=None, bpm=None, key=None,
                scale=None, sections=None, seed=0, soundfont=None, master=True, **overrides):
        sf = find_soundfont(soundfont)
        if not sf:
            raise BackendError("no .sf2 soundfont found (set SOUNDFONT in config)")
        if not shutil.which("fluidsynth"):
            raise BackendError("fluidsynth not installed")
        self.model = os.path.basename(sf)
        style_name = style or style_from_prompt(prompt)
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            mid = os.path.join(tmp, "score.mid")
            raw = os.path.join(tmp, "raw.wav")
            st, meta = build_midi(mid, duration=duration, style=style_name, bpm=bpm, key=key,
                                  scale=scale, sections=sections, seed=seed, overrides=overrides)
            r = subprocess.run(
                ["fluidsynth", "-ni", "-q", "-a", "file", "-T", "wav", "-g", "0.6", "-F", raw, "-r", "48000", sf, mid],
                capture_output=True, text=True,
            )
            if r.returncode or not os.path.isfile(raw):
                raise BackendError(f"fluidsynth failed: {(r.stderr or r.stdout).strip()[-400:]}")
            af = st.get("master_af", "") if master else ""
            cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", raw]
            if duration:
                cmd.extend(["-t", f"{float(duration):.3f}"])
            if af:
                cmd.extend(["-af", af])
            cmd.extend(["-ar", "48000", "-ac", "2"])
            if out_path.lower().endswith(".wav"):
                cmd.extend(["-c:a", "pcm_s16le"])
            base, ext = os.path.splitext(out_path)
            part = f"{base}.{os.getpid()}.part{ext}"  # per process; a failed run never leaves a file the cache reuses
            cmd.append(part)
            fr = subprocess.run(cmd, capture_output=True, text=True)
            if fr.returncode or not os.path.isfile(part):
                if os.path.exists(part):
                    os.remove(part)
                raise BackendError(f"ffmpeg mastering failed: {fr.stderr.strip()[-400:]}")
            os.replace(part, out_path)
        meta["soundfont"] = sf
        return out_path, meta

    def get(self, prompt, duration=None, **kw):
        dur = float(duration or 30.0)
        out = kw.pop("out", None)
        if not out:
            style = load_styles().get(kw.get("style") or style_from_prompt(prompt))
            h = backends.input_hash("composer", prompt, dur, kw, style, find_soundfont(kw.get("soundfont")))
            out = os.path.join(cache_dir(), f"{h}.wav")
            if os.path.isfile(out):
                return out
        path, _ = self.compose(out, prompt=prompt, duration=dur, **kw)
        return path
