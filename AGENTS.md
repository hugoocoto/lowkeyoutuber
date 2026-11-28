# AGENTS.md — how an AI agent uses this repo

This is a collection of tools for making videos: editing recordings, Shorts, narration, animated
scenes, app captures, teleprompter takes, subtitles, titles, thumbnails and uploads. There is no fixed
pipeline: pick the tools the request needs and combine them. `flows/` has worked examples of common
videos (`lky flows`); read one when it's close to the request, adapt it or ignore it.

This file is for any coding agent (Codex, OpenCode, Claude Code, Gemini CLI...). The tools live next
to it: in a clone of the repo, or in the plugin's folder when installed as a Claude Code plugin. The
person's recordings and projects can live anywhere: work in their folder and call the tools through
`lky` (`./lky` or the full path to the clone's `lky` if it isn't on PATH; with the Claude Code plugin it
is on PATH).

## Getting going
- `lky backends` shows which models work on this machine (LLM, transcription, TTS, music, scene
  renderer, judge, embeddings). Say which voice will be used before generating narration.
- Run any tool with `lky run <tool> [args]`, e.g. `lky run subtitles.py rec.mkv > rec.srt`: it uses the
  toolkit's Python (next section) wherever you are.
- Tools that work on a timeline share a project folder: `lky new work/<name>` (format in
  `docs/project.md`). Tools that work on files don't need one; point their `-o` at the project's `out/`
  to keep things together.
- **The video's language:** ask the person, or detect it from the recording; set it once with
  `lky new work/<name> --lang <code>` (or `LANGUAGE=` in `~/.config/lowkeyoutuber/config`).
- Coming back to a project: `lky status -P work/<name>`.
- Before saying a video is done: `lky check -P work/<name>` (when you rendered with `render.py`) and
  look at frames of the result.

## Python packages and optional features
- **The toolkit has its own venv,** and `lky` uses it: `$LKY_VENV` if set, else (as a Claude Code
  plugin) the plugin's data folder, which survives plugin updates, else `~/.venvs/lowkeyoutuber`. `lky`
  creates it on first use; `lky venv` prints its path. Install packages only there, with
  `lky pip install <package>`. Never install system-wide, with `--user` or with
  `--break-system-packages`.
- **Many features are optional** and need a package that may not be installed yet. `lky backends` shows
  what works and why the rest doesn't; a tool that lacks a package says which one.

  | Feature | Needs | Alternatives without it |
  |---|---|---|
  | Transcription (subtitles, captions, align, cuts) | `faster-whisper` (+ `nvidia-cublas-cu12 'nvidia-cudnn-cu12==9.*'` for the GPU) | none: almost every editing tool needs it |
  | Narration | `chatterbox-tts`, `qwen-tts`, or `kokoro soundfile` (+ system `espeak-ng`) | another TTS backend; ElevenLabs (paid: ask first); TTS `none` (the person records the lines) |
  | App capture in a browser | `playwright` | terminal/desktop capture; the person records the screen |
  | Terminal/desktop capture (Linux) | system Xvfb, `xdotool`, `xterm` | the person records the screen |
  | Teleprompter controlling OBS | `websocket-client` | `teleprompter.py` without `--obs` (one long take, then `align.py --media`) |
  | Animated scenes | system Node.js 22+ and npm (HyperFrames installs itself) | stills or clips as each line's `visual` |
  | Local search | `open_clip_torch` (+ `ollama pull nomic-embed-text`) | Gemini Embedding (cloud, paid: ask first) |
  | Automatic checks (`judge.py`) | an Ollama vision model, or Gemini (cloud, paid: ask first) | look at the frames yourself |
  | Upload | `google-api-python-client google-auth-oauthlib` | the person uploads in YouTube Studio |
  | Composed music (`sound.py`) | system `fluidsynth` and a `.sf2` soundfont | tracks in `MUSIC_DIR`; no music |
- **When something is missing, you decide what to do:** install the package (`lky pip install ...`), or
  use one of the alternatives, whichever gives the better result for the request at a sensible cost
  (some installs download GBs: torch-based voices, OpenCLIP). Say what you chose and why. **If the person
  said they'd rather be asked**, ask instead, with the options (install X, or use Y) and what each costs.
- Always ask first for system packages and for anything paid, as above. System packages need the
  person's package manager and often sudo: tell them the line for their system and let them run it
  (in Claude Code they can type `! <command>`). Common ones:

  | | Arch | Debian/Ubuntu | macOS (Homebrew) |
  |---|---|---|---|
  | ffmpeg (required) | `sudo pacman -S ffmpeg` | `sudo apt install ffmpeg` | `brew install ffmpeg` |
  | Python venv support (required) | included in `python` | `sudo apt install python3-venv` | included in `python` |
  | fonts for thumbnails/captions | `sudo pacman -S ttf-dejavu` | `sudo apt install fonts-dejavu` | `brew install --cask font-dejavu` |
  | Node.js + npm (scenes) | `sudo pacman -S nodejs npm` | `sudo apt install nodejs npm` (needs 22+) | `brew install node` |
  | espeak-ng (Kokoro) | `sudo pacman -S espeak-ng` | `sudo apt install espeak-ng` | `brew install espeak-ng` |
  | Xvfb, xdotool, xterm (capture) | `sudo pacman -S xorg-server-xvfb xdotool xterm` | `sudo apt install xvfb xdotool xterm` | not available: record the screen |
  | FluidSynth + soundfont (music) | `sudo pacman -S fluidsynth soundfont-fluid` | `sudo apt install fluidsynth fluid-soundfont-gm` | `brew install fluid-synth` (soundfont: set `SOUNDFONT`) |
  | Ollama (local LLM/judge/search) | `sudo pacman -S ollama` (`ollama-cuda` for NVIDIA) | ollama.com install script | `brew install ollama` |

## How much to ask
It depends on what the person asked for.
- **"Just do it" / automation:** don't stop to ask. Make sensible choices, say what you chose at the end.
- **Editing together:** keep the person aware of each step and show them what matters before going on:
  cuts, scripts, take choices, zooms, titles, subtitles. The `Check-in` points in `flows/` are where
  that usually happens. Proposals go to `proposals/`.
- Either way, ask before anything that costs money (cloud TTS, paid APIs) or can't be undone.

## Rules
- **Uploads are private.** Never upload unless asked; the person publishes in YouTube Studio. There is
  no upload tool for other platforms (X, LinkedIn...): leave the files in `out/` for the person to post.
- **No secrets on screen:** API keys, tokens, real emails, customer data. Use demo data.
- **API keys** come from the environment (`ELEVENLABS_API_KEY`, `GEMINI_API_KEY`, `FREESOUND_API_KEY`,
  `OBS_WEBSOCKET_PASSWORD`), never the config file or the project folder. Never print them.
- **GPU when there is one.** With an NVIDIA GPU, every encode, transcription or model run uses it: NVENC
  (`yt_common.video_encoder("auto", q)` from Python; `-c:v h264_nvenc -preset p5 -tune hq -rc vbr -cq 18
  -b:v 0` in shell), CUDA for Whisper/TTS/Ollama; on a hybrid laptop, through `prime-run` when it exists
  (the Python tools set its variables themselves). Without one (or on macOS) everything runs on the CPU:
  that works, only slower; before a long CPU-only job, say so.

## Recommendations (learned on real videos; break them when you have a reason)
- **Pauses may have something on screen.** In screencasts, silence often comes with typing, a program
  running or scrolling docs. Before cutting a pause or speeding it up, look at its frames or ask.
  `autocut.py` cuts every pause: fine for talking-head footage, risky for screencasts.
- **Listen to every cut and join** (transcribe ~12 s around it). Whisper's word times are loose on fast
  speech: a stray syllable from the word before, or a dangling reference ("I'll do it in another video"
  after the sentence it points to was cut). `cut.py --check` prints the words on both sides of each cut.
- **Transcribe the raw audio,** not the audio cleaned by `process-voice.sh`: on a real video the cleaned
  one lost punctuation and misheard words (`--audio-from` does this). Recurring mistakes go in
  `~/.config/lowkeyoutuber/fixes.txt`; names and jargon in the config's `JARGON`.
- **Final renders through `render.py`** (edit `project.json`, then render) rather than hand-written ffmpeg:
  it handles captions, zooms, ducking, loudness and several formats, and `lky check` can verify it.
- **Facecam** (only if the video has one): its box moves between recordings. Check it on a frame
  (`config CAM`, or `--cam x:y:w:h`); `thumbnail.py` and `clips.py` warn and save `cam_check.jpg` when the
  box looks empty. No facecam: `--cam ""`.
- **Shorts:** avoid ones that cross a hard cut, and check how each one ends. `clips.py --propose`, then
  `--pick 1,3` renders chosen ones without asking the LLM again.
- **Sync:** check it by decoding the audio (`ffmpeg -i x.mkv -map 0:a a.wav`) and comparing with frames;
  summing packet durations in MKV is wrong.
- **Upload:** the first run needs `~/.config/lowkeyoutuber/client_secret.json` and a browser login, which
  the person does with `! lky run upload.py ...`; Google expires the token after 7 days in Testing. One long
  video plus 3 Shorts uses most of a day's quota. Don't run `pkill -f upload.py; lky run upload.py ...` in
  one command: `pkill -f` kills the shell running that line.
- Code shown on screen should compile; `scenes.py` compiles and runs it for real, so its output is real. Run only
  what the person approved in the script, in a demo folder, never anything destructive.

## Tools
Every tool explains itself with `-h`. Project tools take `-P <project>` and `--json` (results on stdout,
progress on stderr), and skip work that's already done (`-f` to redo).

| Tool | Does |
|---|---|
| `lky` | new, add-media, status, check, backends, flows, skills; `run <tool>`, `pip`, `venv` |
| `render.py` | timeline → `out/video*.mp4` + `out/video.srt` (zooms, captions, ducking, -14 LUFS) |
| `tts.py` | narrates `script.json` with the configured TTS backend; `--place` builds the timeline from it |
| `sound.py` | video-adaptive FluidSynth music (`--propose`/`--place`, `generate`) and CC0 SFX (`fetch`, `import-pack`) |
| `align.py` | matches a recording (or teleprompter takes) to `script.json`, keeps the last good take of each line |
| `scenes.py` | animated scenes per script line (title, text, code, terminal, diagram, image, custom), rendered with HyperFrames |
| `capture.py` | drives and records an app (web with Playwright, terminal, desktop on a virtual screen), logs every action, proposes zooms |
| `teleprompter.py` | prompter page for the person; with `--obs` one OBS recording per take |
| `judge.py` | automatic checks with a multimodal model: scenes, lines vs picture, Shorts proposals |
| `search.py` | finds moments in past recordings by description (embeddings) |
| `subtitles.py` | Whisper transcript (`<video>.words.json`, shared by every tool) and SRT/VTT |
| `cut.py` | removes the parts listed in a file |
| `autocut.py` | removes pauses and retakes automatically (see the pauses recommendation) |
| `process-voice.sh` | voice clean-up (noise, EQ, compression) and loudness |
| `meta.py` | chapters, titles, description, tags, thumbnail texts |
| `thumbnail.py` | thumbnail designs |
| `clips.py` | vertical Shorts with word captions from a long video (`--propose`, then `--pick`) |
| `upload.py` | private upload of a folder (`video.mp4`, `titles.txt`, `description.txt`...) to YouTube; `--dry-run` checks it |

Exit code 3 from a tool means a person has to act (for example TTS is `none`: they record the lines).

## Backends (swappable models)
`backends/<kind>/<name>.py`, chosen by `PROFILE` (`local` = no API keys, `cloud`) or a per-kind chain in
`~/.config/lowkeyoutuber/config` (`TTS="elevenlabs,chatterbox,none"`): the first usable one is used,
the next ones are fallbacks. Tools ask for a capability, never a provider. To add one, copy a backend of
the same kind; `tests/test_backends.py` runs the contract tests against every backend.

## Changing the repo
- The repo is also a Claude Code plugin and its own marketplace (`.claude-plugin/`); its one skill is
  `skills/lowkeyoutuber/SKILL.md`, generated: after editing a flow or this tool list, run `./lky skills`.
  Try the plugin from a checkout with `claude --plugin-dir .`.
- Tests: `./lky pip install pytest` once, then `"$(./lky venv)/bin/python" -m pytest tests`. They use
  fake backends: never point them at a paid one.
- Prefer adding a tool that does one thing, with `-h`, `--json` and files in/files out, over a script that
  chains others.
- New lessons from a real video go in the Recommendations above.
