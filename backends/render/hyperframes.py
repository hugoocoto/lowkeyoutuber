"""HyperFrames (heygen-com/hyperframes, Apache 2.0): HTML + CSS + GSAP -> MP4, frame by frame in
headless Chrome, so the same scene always renders the same video.

The CLI and GSAP are installed once, pinned, into ~/.cache/lowkeyoutuber/hyperframes-<version>
(npm), so renders work offline afterwards and don't change when HyperFrames updates.
Needs Node.js 22+ and Chrome/Chromium (HyperFrames finds it, or downloads its own).

CONFIG
    HYPERFRAMES_VERSION="0.9.104"   pin; bump on purpose after checking a render
    HYPERFRAMES_LOW_MEMORY="no"     with less than 8 GB of RAM HyperFrames switches to a much slower
                                    capture mode (one worker, screenshots: ~5x slower measured);
                                    "no" turns that off, "yes" forces it, empty lets it decide
"""

import json
import os
import shutil
import subprocess

from backends import Base, BackendError
from yt_common import CONFIG, log, nvenc_works

DEFAULT_VERSION = "0.9.104"
GSAP_VERSION = "3.15.0"


def node_major():
    try:
        r = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=10)
        return int(r.stdout.strip().lstrip("v").split(".")[0])
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0


class Backend(Base):
    caps = {"local": True, "deterministic": True, "gpu_encode": True, "html": True}

    def __init__(self):
        self.version = CONFIG.get("HYPERFRAMES_VERSION") or DEFAULT_VERSION
        self.model = f"hyperframes@{self.version}"
        cache = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
        self.home = os.path.join(cache, "lowkeyoutuber", f"hyperframes-{self.version}")

    @property
    def bin(self):
        return os.path.join(self.home, "node_modules", ".bin", "hyperframes")

    @property
    def gsap(self):
        return os.path.join(self.home, "node_modules", "gsap", "dist", "gsap.min.js")

    def check(self):
        if not shutil.which("node") or not shutil.which("npm"):
            return False, "Node.js 22+ is not installed (pacman/apt: nodejs npm; brew: node)"
        if node_major() < 22:
            return False, "HyperFrames needs Node.js 22 or newer"
        if not os.path.isfile(self.bin):
            return True, f"installs {self.model} on first use (npm, once)"
        return True, ""

    def ensure(self):
        """Install the pinned CLI and GSAP once."""
        if os.path.isfile(self.bin) and os.path.isfile(self.gsap):
            return
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "package.json"), "w") as f:
            json.dump({"name": "lowkeyoutuber-hyperframes", "private": True}, f)
        log(f"Installing {self.model} and gsap@{GSAP_VERSION} (once)...")
        r = subprocess.run(["npm", "install", "--no-fund", "--no-audit", f"hyperframes@{self.version}",
                            f"gsap@{GSAP_VERSION}"], cwd=self.home, capture_output=True, text=True)
        if r.returncode or not os.path.isfile(self.bin):
            raise BackendError("npm install failed: " + (r.stderr or r.stdout).strip()[-500:])

    def prepare(self, scene_dir):
        """Put the pinned GSAP next to the scene (scenes load it as gsap.min.js, no network)."""
        self.ensure()
        dst = os.path.join(scene_dir, "gsap.min.js")
        if not os.path.isfile(dst):
            shutil.copy2(self.gsap, dst)

    def lint(self, scene_dir):
        self.prepare(scene_dir)
        r = subprocess.run([self.bin, "lint", scene_dir, "--json"], capture_output=True, text=True)
        try:
            data = json.loads(r.stdout[r.stdout.find("{"):])
        except ValueError:
            return [f"lint failed to run: {(r.stderr or r.stdout).strip()[-300:]}"]
        return [f"{f.get('severity', 'error')}: {f.get('message', f)}" for f in data.get("findings", [])
                if f.get("severity", "error") == "error"]

    def render_scene(self, scene_dir, out, fps, w, h, draft=False):
        self.prepare(scene_dir)
        cmd = [self.bin, "render", scene_dir, "-o", out, "--fps", str(fps),
               "--quality", "draft" if draft else "delivery", "--quiet", "--strict"]
        if nvenc_works():
            cmd.append("--gpu")
        env = dict(os.environ)
        low = (CONFIG.get("HYPERFRAMES_LOW_MEMORY") or "").lower()
        if low in ("no", "false", "0"):
            env["PRODUCER_LOW_MEMORY_MODE"] = "false"
        elif low in ("yes", "true", "1"):
            env["PRODUCER_LOW_MEMORY_MODE"] = "true"
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if r.returncode or not os.path.isfile(out):
            raise BackendError("hyperframes render failed: " + (r.stderr or r.stdout).strip()[-800:])
        return out
