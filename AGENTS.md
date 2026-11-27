# AGENTS.md — how an AI agent uses this repo

This repo turns recordings, scripts and apps into finished YouTube videos. You (the agent) pick a
**flow**, ask the person only for what's missing, run the **tools** on a **project folder**, and
stop for the person's OK where the flow says so.

## Start here
1. `./lky flows` lists the flows, their status and what each needs. Pick the one that matches the
   request; if two fit, ask. Then read `flows/<name>.md` and follow it step by step.
   - `ready`: every step has a tool. `partial`: some steps are done by you or the person until their
     tool exists (the flow says how). `planned`: tell the person it isn't available yet.
2. `./lky backends` shows which models can be used here (LLM, transcription, TTS, music) and what
   they can do. Say which voice will be used before generating narration.
3. Work in a project folder: `./lky new <flow> work/<name>`. Its format is in `docs/project.md`.
4. When you come back to a project: `./lky status -P work/<name>` before anything else.
5. Before saying a video is done: `./lky check -P work/<name>`, and look at frames of the result.

## Rules (from NOTES.md — the person's own lessons; read NOTES.md too)
- **Approvals.** Cuts, scripts, take choices, zooms and anything that costs money or can't be undone
  need the person's OK. Write proposals to `proposals/`, show them, wait. Never upload or publish
  without being asked; uploads are always private.
- **Only cut content** (rambling, false starts), and only when asked. **Never trim silences or speed
  anything up**: pauses in screencasts usually have screen activity.
- **Listen to every cut / join** (transcribe a ~12 s window around it): word times are loose.
- **GPU first.** Encoding uses NVENC when it works (the tools check), Whisper/TTS use CUDA; run them
  through `prime-run` when it exists. Before a long CPU-only job, say so.
- **Code on screen must compile, and program output must come from really running it.** Never invent output.
- **No secrets on screen**: API keys, tokens, real emails, customer data. Use demo data.
- **Don't hand-roll ffmpeg for final renders.** Edit `project.json` and run `render.py`; the classic
  scripts (`publish.sh`, `cut.py`, `clips.py`) are fine for what they do.
- **API keys** come from the environment (`ELEVENLABS_API_KEY`, `GEMINI_API_KEY`), never the config
  file or the project folder. Never print them.
- Subtitles and AI-written text make mistakes: always have the person review `video.srt`, titles and
  descriptions. Recurring transcription fixes go to `~/.config/lowkeyoutuber/fixes.txt`.

## Tools
Every tool explains itself with `-h`. Most take `-P <project>` and `--json` (results on stdout,
progress on stderr), and skip work that's already done (`-f` to redo).

| Tool | Does |
|---|---|
| `lky` | flows, new, add-media, status, check, backends, skills |
| `tts.py` | narrates `script.json` with the configured TTS backend; `--place` builds the timeline from it |
| `align.py` | matches a recording to `script.json`, keeps the last good take of each line |
| `render.py` | timeline → `out/video*.mp4` + `out/video.srt` (zooms, captions, ducking, -14 LUFS) |
| `publish.sh` | classic one-command path: clean audio, subtitles, metadata, thumbnails, Shorts |
| `process-voice.sh` | voice clean-up and loudness |
| `subtitles.py` | Whisper transcript (`<video>.words.json`, shared by every tool) and SRT |
| `cut.py` / `autocut.py` | remove listed parts / pauses and retakes (autocut: only if the person asks) |
| `meta.py` | chapters, titles, description, tags, thumbnail texts |
| `thumbnail.py` | thumbnail designs |
| `clips.py` | Shorts from a long video (`--propose`, then `--pick`) |
| `upload.py` | private upload to YouTube (`--dry-run` checks everything first) |

Exit code 3 from a tool means a person has to act (for example TTS is `none`: they record the lines).

## Backends (swappable models)
`backends/<kind>/<name>.py`, chosen by `PROFILE` (`local` = no API keys, `cloud`) or a per-kind chain in
`~/.config/lowkeyoutuber/config` (`TTS="elevenlabs,chatterbox,none"`): the first usable one is used,
the next ones are fallbacks. Flows never name a provider. To add one, copy a backend of the same kind;
`tests/test_backends.py` runs the contract tests against every backend.

## Changing the repo
- After editing a flow: `./lky skills` (regenerates `.claude/skills/`), and `python -m pytest tests`.
- New flows are markdown in `flows/` with the same header and sections; prefer combining existing tools
  over writing new scripts.
- Write what you learned on a real run into NOTES.md, as the person does.
