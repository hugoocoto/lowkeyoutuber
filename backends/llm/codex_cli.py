"""LLM through the codex command-line tool, with no tools, in an empty folder."""

from backends.llm._cli import CLIBackend


class Backend(CLIBackend):
    command = "codex"
