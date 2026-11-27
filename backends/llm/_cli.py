"""Shared code for AI command-line tools (Claude Code, Codex, OpenCode)."""

import shutil

from backends import Base, BackendError
from yt_common import CONFIG, LLMError, run_cli


class CLIBackend(Base):
    command = ""
    caps = {"local": False, "vision": False, "subscription": True}

    def __init__(self):
        self.model = CONFIG.get("LLM_MODEL") or None

    def check(self):
        if not shutil.which(self.command):
            return False, f"{self.command} is not installed"
        return True, "uses your own sign-in; the prompt is sent to that provider"

    def complete(self, prompt, json_mode=False):
        if json_mode:
            prompt += "\n\nAnswer with the JSON only: no code fences, no comments."
        try:
            return run_cli(self.command, self.model, prompt)
        except LLMError as e:
            raise BackendError(str(e))
