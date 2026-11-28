# lowkeyoutuber

A box of small tools for making videos on your own machine: for YouTube, Shorts and Reels, posts on
X or LinkedIn, or a demo of your project. Clean up a recording's voice, cut it, subtitle it, write
titles and chapters, make thumbnails and short clips, narrate a script, animate scenes, record an app
demo, render it in several shapes at once (16:9, 9:16, 1:1, 4:5), and upload to YouTube. Each tool does
one thing and works on files, so you (or an AI agent) can use any of them on their own or chain them
however the video needs.

## Ask an AI to do it

Any coding agent that reads `AGENTS.md` can use it (Codex, OpenCode, Claude Code, Gemini CLI,
Cursor...): clone the repo, open your agent there, and say what you want: "edit this recording",
"make a square clip for LinkedIn about this demo", "add a voice to this video", "write me a script and
I'll record it". The agent reads `AGENTS.md`, picks the tools, and asks as much as you want it to: say
"just do it" and it won't stop to ask; edit with it and it shows you each step. Python packages go into
the toolkit's own venv, which the agent creates and fills as features need them (or asks you first, if
you prefer).

```
git clone https://github.com/hugoocoto/lowkeyoutuber
cd lowkeyoutuber
./lky backends    # which models it can use on this machine
./lky flows       # worked examples of common videos (flows/)
```

To use it from the folder with your recordings instead, put the clone's `lky` on your PATH
(`ln -s "$PWD/lky" ~/.local/bin/lky`) and tell your agent to read the clone's `AGENTS.md`. In Claude Code, the repo also installs as a plugin that does
this for you:

```
/plugin marketplace add hugoocoto/lowkeyoutuber
/plugin install lowkeyoutuber@lowkeyoutuber
```

(updates: `/plugin marketplace update lowkeyoutuber`).

## Use the tools yourself

From a recording to an upload, using only what you need (outputs go wherever `-o` says):

```
mkdir -p out
./lky run process-voice.sh -t 1 -o out/video.mp4 rec.mkv   # clean voice on track 1, -14 LUFS
./lky run subtitles.py --audio-from rec.mkv -t 1 out/video.mp4 > out/video.srt
./lky run meta.py -o out out/video.mp4               # chapters, titles, description, tags (overwrites them)
./lky run thumbnail.py -o out out/video.mp4          # thumbnail designs
./lky run clips.py -o out/shorts out/video.mp4       # vertical Shorts
./lky run upload.py out/                             # upload to YouTube as private
```

`./lky run` runs a tool with the toolkit's venv (see Install); `python tool.py` works too inside an
activated venv.

`upload.py` takes any folder with these files (`--dry-run` checks them first):

| File | What it is |
|---|---|
| `video.mp4` | the video |
| `titles.txt` | title ideas; the first line is the one that gets uploaded |
| `description.txt` | description with chapters |
| `tags.txt` | comma-separated tags |
| `video.srt` | subtitles (optional); check names and technical words |
| `thumbnail.jpg` | the thumbnail (optional); other designs are in `thumbs/` |
| `shorts/` | vertical clips, uploaded with `--shorts` |

Edit any of them by hand. Rerunning `meta.py` or `thumbnail.py` overwrites these files, so once you have
edited them, resume from the next step instead. Tools that cache (transcripts, TTS) skip work already done.

## Install

System packages (ffmpeg and fonts; the rest only for the features you use, see `AGENTS.md`):

```
sudo pacman -S ffmpeg ttf-dejavu                 # Arch (+ nvidia-prime on a hybrid NVIDIA laptop)
sudo apt install ffmpeg fonts-dejavu python3-venv   # Debian / Ubuntu
brew install ffmpeg && brew install --cask font-dejavu   # macOS
```

Python packages go into the toolkit's own venv: `$LKY_VENV` if you set it, the plugin's data folder
when installed as a plugin, else `~/.venvs/lowkeyoutuber`. `./lky` creates it on first use, and
`./lky pip` installs into it:

```
./lky pip install faster-whisper                                        # transcription (needed by most tools)
./lky pip install nvidia-cublas-cu12 'nvidia-cudnn-cu12==9.*'          # Whisper on an NVIDIA GPU
./lky pip install google-api-python-client google-auth-oauthlib        # uploading to YouTube
```

Already have a venv? `export LKY_VENV=~/path/to/venv` and `lky` uses it.

Titles, chapters and Shorts picks are written by the first AI command-line tool it finds:
Claude Code, Codex or OpenCode. Without any, install a free local model with
[Ollama](https://ollama.com) and `ollama pull qwen2.5:7b`.

Works without an NVIDIA GPU (and on macOS), just slower.

## Configuration

Your defaults go in `~/.config/lowkeyoutuber/config`:

```
LANGUAGE="es"                                 # the language you speak in your videos
JARGON="Neovim, Lua, GitHub"                  # names the subtitles should spell right
CAM="934:384:346:303"                         # where your facecam is: x:y:width:height in pixels
FOOTER="~/.config/lowkeyoutuber/footer.txt"   # links added to every description
```

Words the subtitles keep getting wrong go in `~/.config/lowkeyoutuber/fixes.txt`:

```
atoy => atoi
main .si => main.c
```

If you move your facecam in OBS, update `CAM`. You'll get a warning when it looks wrong,
and `cam_check.jpg` showing where it points.

## Good to know

- **Cutting.** Edit your recording in Kdenlive or Shotcut, or write the parts to remove in a
  text file (`17:14 - 17:22`, one per line) and run `lky run cut.py rec.mkv cuts.txt`. Add `--check`
  to read the words around each cut before rendering. `autocut.py` removes every pause and
  repeated sentence by itself; in screencasts, pauses often have something happening on screen.
- **Music or desktop audio on its own track?** Clean the voice before editing, because editors
  mix all tracks together: `lky run process-voice.sh -t 1 -m 0 -c rec.mkv` (voice on track 1, music
  on 0), edit `rec_clean.mkv`, then `lky run process-voice.sh -L export.mp4` for the loudness only.
- **Different Shorts.** `lky run clips.py out/video.mp4 -o out/shorts --pick 2,4` makes clips 2
  and 4 of the ones listed in `out/shorts/clips.json`.
- **Uploading** needs a one-time Google setup, explained in `lky run upload.py -h`. Videos
  arrive private (YouTube's rule for personal projects): publish them in YouTube Studio.
  Upload the video first, then `lky run upload.py --shorts out/`, so the Shorts link to it.
- **Review what AI wrote.** Subtitles, titles and descriptions make mistakes.

## The tools

Every tool explains its options with `-h`.

On files:

| Tool | What it does |
|---|---|
| `process-voice.sh` | Removes background noise and sets the volume platforms expect (-14 LUFS). |
| `subtitles.py` | Writes subtitles from what you say. |
| `meta.py` | Writes chapters, titles, description, tags and thumbnail texts. |
| `thumbnail.py` | Makes thumbnails: a frame of the video, your face (if there's a facecam), a few big words. |
| `clips.py` | Finds the best moments and turns them into vertical Shorts with captions. |
| `upload.py` | Uploads a folder to YouTube, private. |
| `cut.py` | Removes the parts of a recording you list in a text file. |
| `autocut.py` | Removes every pause and repeated sentence automatically. |

On a project folder (`./lky new work/<name>`, format in `docs/project.md`):

| Tool | What it does |
|---|---|
| `lky` | Creates projects, shows their state, runs the final checks, lists backends and examples. |
| `tts.py` | Narrates a script with the configured voice (yours, cloned, or a preset), with word timings. |
| `sound.py` | Composes adaptive FluidSynth music and fetches CC0 sound effects synced to the project. |
| `align.py` | Matches a recording to the script and keeps the last good take of each line. |
| `render.py` | Renders the project's timeline: clips, images, zooms, captions, music under the voice, -14 LUFS. |
| `scenes.py` | Animated scenes written as code (code typing, diagrams, terminal, titles), rendered with HyperFrames. |
| `capture.py` | Records an app by driving it: a web page, a terminal or a desktop program, with zooms on each action. |
| `frame.py` | Puts phone captures in a phone frame on a background, one or two side by side, for each video shape. |
| `teleprompter.py` | Shows the script while you record; with OBS, one recording per take. |
| `judge.py` | Checks scenes, narration and Shorts with a multimodal model before you look. |
| `search.py` | Finds moments in your old recordings by describing them. |

## Models: all swappable

Every model sits behind one interface (`backends/`), and the config picks which one, with
fallbacks: `PROFILE="local"` needs no API keys; `PROFILE="cloud"` uses paid APIs where they're
better. Override one kind at a time:

```
PROFILE="local"                            # local | cloud
TTS="elevenlabs,chatterbox,none"           # tried in order; "none" = you record it
VOICE_REF="~/.config/lowkeyoutuber/voice_ref.wav"   # 10-30 s of your clean voice, for cloning
ELEVEN_VOICE_ID="..."                      # your cloned voice on ElevenLabs
MUSIC_DIR="~/Music/lowkeyoutuber"          # tracks you're allowed to use
SFX_DIR="~/Music/lowkeyoutuber/sfx"        # optional folder of local sound effects
SOUNDFONT="/usr/share/soundfonts/FluidR3_GM.sf2"   # custom .sf2 for FluidSynth music
```

| Kind | Backends |
|---|---|
| LLM | Claude Code, Codex, OpenCode (your own sign-in), Ollama (local) |
| Transcription | faster-whisper (local) |
| Voice | Chatterbox Multilingual (local, clones your voice), Qwen3-TTS (local, clones from 3 s, CUDA), Kokoro (local, preset voices, CPU), ElevenLabs (cloud, `ELEVENLABS_API_KEY`), none |
| Music | a local folder (`MUSIC_DIR`), FluidSynth adaptive composer (local, `fluidsynth`) |
| SFX | local folder/cache, Freesound CC0 (`FREESOUND_API_KEY`), Wikimedia Commons CC0, procedural synth |
| Scenes | HyperFrames (local, Node.js 22+ and Chrome; installed and pinned on first use) |
| Judge | Ollama vision model (local), Gemini Decisions (cloud, `GEMINI_API_KEY`) |
| Search | OpenCLIP + Ollama text embeddings (local), Gemini Embedding 2 (cloud) |

Install what you use into the toolkit's venv (never system-wide). An AI agent does this itself, or
picks an alternative (see `AGENTS.md`, which also has the system packages for Debian/Ubuntu and macOS):

```
./lky pip install chatterbox-tts      # or qwen-tts, or: kokoro soundfile (+ espeak-ng from your system)
./lky pip install playwright          # app capture in a browser (uses your Chrome/Chromium)
./lky pip install websocket-client    # teleprompter controlling OBS
./lky pip install open_clip_torch     # local search (with: ollama pull nomic-embed-text)
```

From your system: Node.js 22+ and npm for scenes (HyperFrames installs itself on first use); on Linux,
Xvfb, xdotool and xterm for terminal and desktop capture.

API keys go in the environment, never in the config.

## Tests

```
./lky pip install pytest
"$(./lky venv)/bin/python" -m pytest tests     # no GPU, network or API keys needed
```
