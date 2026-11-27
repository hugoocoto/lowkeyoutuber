"""LLM through a local Ollama server (free, private). Model: LLM_MODEL if it looks like an
Ollama tag (has a ':'), else OLLAMA_MODEL, else qwen2.5:7b."""

import argparse
import json
import os
import urllib.error
import urllib.request

from backends import Base, BackendError
from yt_common import CONFIG, ollama


class Backend(Base):
    caps = {"local": True, "vision": False}

    def __init__(self):
        m = CONFIG.get("LLM_MODEL") or ""
        self.model = m if ":" in m else CONFIG.get("OLLAMA_MODEL") or "qwen2.5:7b"
        url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        self.url = (url if url.startswith("http") else "http://" + url).rstrip("/")

    def check(self):
        try:
            with urllib.request.urlopen(self.url + "/api/tags", timeout=3) as r:
                tags = [m["name"] for m in json.load(r).get("models", [])]
        except (urllib.error.URLError, OSError, ValueError):
            return False, f"Ollama is not running at {self.url} (sudo systemctl start ollama)"
        if self.model not in tags and f"{self.model}:latest" not in tags:
            return False, f"model {self.model} not pulled (ollama pull {self.model})"
        return True, ""

    def complete(self, prompt, json_mode=False):
        args = argparse.Namespace(llm=self.model, ollama_url=self.url, num_ctx=int(CONFIG.get("OLLAMA_NUM_CTX") or 8192))
        try:
            return ollama(args, prompt, json_mode)
        except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
            raise BackendError(f"Ollama failed: {e}")
