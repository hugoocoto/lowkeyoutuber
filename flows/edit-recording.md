---
name: edit-recording
summary: The person recorded a video (screencast, talk) and wants it edited and ready for YouTube.
status: ready
script: no
inputs: the recording (required); which audio track is the voice, and which is music/desktop audio; facecam box if it moved (config CAM); whether to make Shorts; whether to upload
---

# Edit a recording

The person did the recording. You propose content cuts and polish; they approve; the
tools render. This is the classic `publish.sh` path, run step by step so the person can
approve what matters.

## Ask first (only what you can't find)
- The recording file. Check its audio tracks: `ffprobe -v error -show_entries stream=index,codec_type -of csv rec.mkv`.
  With two audio tracks, ask which one is the voice (OBS setups usually put the mic on its own track).
- Shorts too? Upload at the end? (Uploads are always private.)

## Steps
1. **Project:** `./lky new edit-recording work/<name> --format 16:9` (add `--format 9:16` only if
   the person wants a vertical version of the whole video; Shorts are made in step 8).
   `./lky add-media -P work/<name> rec.mkv --id rec --track <voice track>`.
2. **Transcript:** `python subtitles.py rec.mkv -t <voice track> > /dev/null` makes `rec.words.json`
   (cached, shared by every tool). Read it.
3. **Propose content cuts** (only if the person asked for cuts): off-topic rambling, false starts,
   dead ends. Write them to `work/<name>/proposals/cuts.txt`, one `start - end` per line with a
   `# reason` comment, then `python cut.py rec.mkv work/<name>/proposals/cuts.txt --check -t <track>`
   and show the person the words on both sides of every cut.
   - **APPROVAL:** the person OKs, edits or drops each cut. Never cut without it.
   - Never trim silences or speed anything up: pauses usually have screen activity (NOTES.md).
4. **Cut** (only if at least one cut was approved; otherwise skip this step and use `rec.mkv` wherever
   the next steps say `cut.mkv`):
   `python cut.py rec.mkv work/<name>/proposals/cuts.txt -t <track> -o work/<name>/media/cut.mkv`.
   Listen around every cut (transcribe a ~12 s window) and fix dangling references or stray syllables.
5. **Zooms (optional, recommended for code):** where the person points at a small part of the
   screen (a line of code, gdb output, an error), propose zooms. `./lky add-media -P work/<name>
   work/<name>/media/cut.mkv --id cut --track <track>`, then put one timeline item with zooms in
   `project.json` (see docs/project.md):
   `{"src": "cut", "in": 0, "out": <duration>, "zooms": [{"at": 312.0, "dur": 6.0, "rect": [0.05, 0.40, 0.5, 0.3]}]}`.
   Find the rect on a frame: `./render.py -P work/<name> --preview --frames 312,315`.
   - **APPROVAL:** show the frames; the person OKs the zooms.
   - Then `./render.py -P work/<name>` → `out/video.mp4` (loudness already at -14 LUFS).
6. **Publish folder:** `./publish.sh -t <track> [-m <music track>] <cut.mkv>` without zooms, or
   `./publish.sh -L work/<name>/out/video.mp4` with them. It makes clean audio, subtitles, chapters,
   titles, description, tags and thumbnails in `<video>_publish/`.
7. **Review with the person:** `titles.txt` (first line is uploaded), `description.txt`, `video.srt`
   (names and jargon; add recurring fixes to `~/.config/lowkeyoutuber/fixes.txt`), `thumbnail.jpg`.
8. **Shorts (optional):** follow flows/long-to-shorts.md on the published video.
9. **Upload (optional):** `python upload.py <publish folder>` (private), then
   `python upload.py --shorts <publish folder>`.

## Checks
- `./lky check -P work/<name>` when you rendered with render.py.
- Loudness, chapters, links: `python upload.py --dry-run <publish folder>`.
- Look at a contact sheet of the final video and at every thumbnail before saying it's done.

## Outputs
`<video>_publish/` with `video.mp4`, `video.srt`, `titles.txt`, `description.txt`, `tags.txt`,
`thumbnail.jpg`, `thumbs/`, optional `shorts/`.
