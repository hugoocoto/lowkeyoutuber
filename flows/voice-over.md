---
name: voice-over
summary: The person has a video without narration (or with narration to replace) and wants a voice added.
status: ready
script: yes
inputs: the video (required); what it's about and who it's for; tone; language; keep the original audio under the voice, or remove it
---

# Voice-over for an existing video

## Steps
1. **Project:** `./lky new voice-over work/<name> --format 16:9` (or the video's own shape),
   `./lky add-media -P work/<name> video.mp4 --id src`.
2. **Understand the video:** find scene changes and look at them:
   `ffmpeg -i video.mp4 -vf "select='gt(scene,0.3)',showinfo" -vsync vfr work/<name>/checks/scene_%03d.jpg 2>&1 | grep pts_time`
   gives the times; also take a frame every few seconds where nothing changes for long. Read on-screen
   text in the frames. Ask the person about anything you can't tell from the picture.
3. **Script with times:** `script.json` lines with `"at"` = second in the video where the line starts.
   Leave the line's slot (until the next `at`) longer than it takes to say: about 2.5 words per second
   in Spanish, 2.8 in English. Describe what the viewer sees and why it matters, not every click.
   - **APPROVAL:** show the person each line with its time and a frame of that moment.
4. **Narration:** `./tts.py -P work/<name>` (draft first with `--draft`).
5. **Timeline:** set `timeline.video` to the whole video:
   `[{"src": "src", "in": 0, "out": <duration>, "audio": false}]` (`"audio": true, "gain_db": -18`
   keeps the original sound under the voice). Then `./tts.py -P work/<name> --place`.
   Lines reported in `overruns` are too long for their slot: rewrite them shorter (don't speed the
   voice up), `./tts.py -P work/<name> --lines <id> -f --place`, repeat. If a line can't be shorter,
   propose a hold: split the video item and insert a still of that frame with a `dur`.
6. **Preview** with frames at each line's `at`, then **render**: `./render.py -P work/<name>`.
   Captions burned only if the person wants them; `out/video.srt` is always written.
7. **Automatic check (optional):** `./judge.py -P work/<name> --lines`: does each line describe what's
   on screen while it's said? Rewrite the lines that fail.

## Checks
`./lky check -P work/<name>`; listen to every line against its picture.
