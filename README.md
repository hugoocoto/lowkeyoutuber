# lowkeyoutuber

Turn a screen recording into a video that's ready for YouTube: clean audio, subtitles,
chapters, titles, description, tags, thumbnails and Shorts. One command, runs on your own machine.

```
./publish.sh my-video.mp4
```

## Ask an AI to do it

Open this folder in Claude Code (or Codex, OpenCode...) and say what you want: "edit this
recording", "make a Short about the comma operator", "add a voice to this demo", "write me a
script and I'll record it". The agent reads `AGENTS.md`, picks a **flow** from `flows/`, asks
only for what's missing, and stops for your OK before cutting, generating or uploading anything.

```
./lky flows       # what it can do, and what each flow needs
./lky backends    # which models it can use on this machine
```

## Quick start

```
source ~/.venvs/subs/bin/activate    # see Install
./publish.sh my-video.mp4            # everything, into my-video_publish/
./publish.sh -s my-video.mp4         # the same, plus vertical Shorts
python upload.py my-video_publish/   # upload to YouTube as private
```

Before uploading, open `my-video_publish/` and check:

| File | What it is |
|---|---|
| `video.mp4` | your video, with a clean voice at the loudness YouTube expects |
| `titles.txt` | 5 title ideas; the first line is the one that gets uploaded |
| `description.txt` | description with chapters |
| `video.srt` | subtitles; check names and technical words |
| `thumbnail.jpg` | a thumbnail; other designs are in `thumbs/` |
| `shorts/` | vertical clips with captions (with `-s`) |

Edit any of them by hand. Running `publish.sh` again skips what's already done; delete a file
to make it again.

## Install (Arch Linux)

```
sudo pacman -S ffmpeg ttf-dejavu nvidia-prime
python -m venv ~/.venvs/subs
~/.venvs/subs/bin/pip install faster-whisper nvidia-cublas-cu12 'nvidia-cudnn-cu12==9.*'
~/.venvs/subs/bin/pip install google-api-python-client google-auth-oauthlib   # for uploading
```

Titles, chapters and Shorts picks are written by the first AI command-line tool it finds:
Claude Code, Codex or OpenCode. Without any, install a free local model:

```
sudo pacman -S ollama-cuda && sudo systemctl enable --now ollama && ollama pull qwen2.5:7b
```

Works without an NVIDIA GPU too, just slower.

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

- **Cut first.** Edit your recording in Kdenlive or Shotcut, or write the parts to remove in a
  text file (`17:14 - 17:22`, one per line) and run `./cut.py rec.mkv cuts.txt`. Add `--check`
  to read the words around each cut before rendering.
- **Music or desktop audio on its own track?** Clean the voice before editing, because editors
  mix all tracks together: `./process-voice.sh -t 1 -m 0 -c rec.mkv` (voice on track 1, music
  on 0), edit `rec_clean.mkv`, then `./publish.sh -L export.mp4`.
- **Different Shorts.** `python clips.py my-video_publish/video.mp4 -o my-video_publish/shorts
  --pick 2,4` makes clips 2 and 4 of the ones listed in `shorts/clips.json`.
- **Uploading** needs a one-time Google setup, explained in `python upload.py -h`. Videos
  arrive private (YouTube's rule for personal projects): publish them in YouTube Studio.
  Upload the video first, then `python upload.py --shorts my-video_publish/`, so the Shorts
  link to it.
- **Always review.** Subtitles and AI-written text make mistakes.

## The scripts

`publish.sh` runs the first five. Every script explains its options with `-h`.

| Script | What it does |
|---|---|
| `process-voice.sh` | Removes background noise and sets the volume YouTube expects. |
| `subtitles.py` | Writes subtitles from what you say. |
| `meta.py` | Writes chapters, titles, description, tags and thumbnail texts. |
| `thumbnail.py` | Makes thumbnails: a frame of the video, your face, a few big words. |
| `clips.py` | Finds the best moments and turns them into vertical Shorts with captions. |
| `upload.py` | Uploads the result to YouTube. |
| `cut.py` | Removes the parts of a recording you list in a text file. |
| `autocut.py` | Removes every pause and repeated sentence automatically. |

For flows (see `AGENTS.md`), working in a project folder (`docs/project.md`):

| Script | What it does |
|---|---|
| `lky` | Lists flows, creates projects, shows their state, runs the final checks, lists backends. |
| `tts.py` | Narrates a script with the configured voice (yours, cloned, or a preset), with word timings. |
| `align.py` | Matches a recording to the script and keeps the last good take of each line. |
| `render.py` | Renders the project's timeline: clips, images, zooms, captions, music under the voice, -14 LUFS. |

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
```

| Kind | Backends |
|---|---|
| LLM | Claude Code, Codex, OpenCode (your own sign-in), Ollama (local) |
| Transcription | faster-whisper (local) |
| Voice | Chatterbox Multilingual (local, clones your voice), Kokoro (local, preset voices, CPU), ElevenLabs (cloud, `ELEVENLABS_API_KEY`), none |
| Music | a local folder |

Install the local voices in the same venv: `pip install chatterbox-tts` and/or `pip install kokoro soundfile`
(`sudo pacman -S espeak-ng` for Kokoro in Spanish). API keys go in the environment, never in the config.

## Tests

```
python -m pytest tests     # no GPU, network or API keys needed
```
