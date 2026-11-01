# lowkeyoutuber

Turn a screen recording into a video that's ready for YouTube: clean audio, subtitles,
chapters, titles, description, tags, thumbnails and Shorts. One command, runs on your own machine.

```
./publish.sh my-video.mp4
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
