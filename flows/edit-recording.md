---
name: edit-recording
summary: The person recorded a video (screencast, talk) and wants it edited and ready for YouTube.
status: ready
script: no
inputs: the recording (required); which audio track is the voice, and which is music/desktop audio; facecam box if it moved (config CAM); whether to make Shorts; whether to upload
---

# Edit a recording

The person did the recording. You propose content cuts and polish; they approve; the
tools render.

## Ask first (only what you can't find)
- The recording file. Check its audio tracks: `ffprobe -v error -show_entries stream=index,codec_type -of csv rec.mkv`.
  With two audio tracks, ask which one is the voice (OBS setups usually put the mic on its own track).
- Shorts too? Upload at the end? (Uploads are always private.)

## Steps
1. **Project:** `./lky new work/<name> --flow edit-recording --format 16:9` (add `--format 9:16` only if
   the person wants a vertical version of the whole video; Shorts are made in step 8).
   `./lky add-media -P work/<name> rec.mkv --id rec --track <voice track>`.
2. **Transcript:** `python subtitles.py rec.mkv -t <voice track> > /dev/null` makes `rec.words.json`
   (cached, shared by every tool). Read it.
3. **Propose content cuts** (only if the person asked for cuts): off-topic rambling, false starts,
   dead ends. Write them to `work/<name>/proposals/cuts.txt`, one `start - end` per line with a
   `# reason` comment, then `python cut.py rec.mkv work/<name>/proposals/cuts.txt --check -t <track>`
   and show the person the words on both sides of every cut.
   - **Check-in:** the person OKs, edits or drops each cut.
   - Pauses in a screencast often have something on screen (typing, a program running): look at the
     frames, or ask, before cutting a pause or speeding it up.
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
   - **Check-in:** show the frames; the person OKs the zooms.
   - **Footage from older videos (optional):** `./search.py query "<what>"` finds moments in
     recordings indexed with `./search.py index <videos>`, to cut in as a timeline item. Add it
     before rendering: the next step renders with `render.py`, and the upload folder only has
     what was rendered.
   - Then `./render.py -P work/<name>` → `out/video.mp4` (loudness already at -14 LUFS).
6. **Upload folder** (`work/<name>/out/`; take only the parts the person wants):
   - Audio: when anything was added on the timeline (zooms, older footage), `render.py` already made
     `out/video.mp4` and normalized it (-14 LUFS). Only when nothing was added:
     `./process-voice.sh -t <track> [-m <music track>] -o work/<name>/out/video.mp4 <cut.mkv>`.
   - Subtitles: `render.py` writes `out/video.srt` from the timeline, but only for sources that have a
     transcript.
     - Older footage added, all with transcripts: keep that `out/video.srt` (the raw cut doesn't have the
       inserted speech, and its times would be off after the insert).
     - Older footage added without a transcript: its speech is missing, so transcribe the rendered
       `out/video.mp4` instead: `python subtitles.py work/<name>/out/video.mp4 > work/<name>/out/video.srt`.
     - No older footage added: `python subtitles.py --audio-from <cut.mkv> -t <track> work/<name>/out/video.mp4 > work/<name>/out/video.srt`
       (transcribing the raw audio does better than the cleaned one).
   - Chapters, titles, description, tags: `python meta.py -o work/<name>/out work/<name>/out/video.mp4`.
   - Thumbnails: `python thumbnail.py -o work/<name>/out work/<name>/out/video.mp4` (`--cam ""` when
     there is no facecam).
7. **Review with the person:** `titles.txt` (first line is uploaded), `description.txt`, `video.srt`
   (names and jargon; add recurring fixes to `~/.config/lowkeyoutuber/fixes.txt`), `thumbnail.jpg`.
8. **Shorts (optional):** see flows/long-to-shorts.md, on `out/video.mp4`, into `out/shorts/`.
9. **Upload (optional):** `python upload.py work/<name>/out/` (private), then
   `python upload.py --shorts work/<name>/out/`.

## Checks
- `./lky check -P work/<name>` when you rendered with render.py.
- Loudness, chapters, links: `python upload.py --dry-run work/<name>/out/`.
- Look at a contact sheet of the final video and at every thumbnail before saying it's done.

## Outputs
`work/<name>/out/` with `video.mp4`, `video.srt`, `titles.txt`, `description.txt`, `tags.txt`,
`thumbnail.jpg`, `thumbs/`, optional `shorts/`.
