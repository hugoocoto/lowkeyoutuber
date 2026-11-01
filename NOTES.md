# Notes from a real run

Lessons from processing `2026-11-01_11-52-33.mp4` (20:50 raw, Spanish, C screencast) end to end:
cut, `publish.sh`, Shorts, upload. Most of what went wrong is now handled by the tools; what's
left here is what to check by hand.

## Before anything: check the recording

- **Facecam box.** It moved between recordings (`934:384:346:303` before, `991:0:289:252`
  top-right on 2026-11-01). `thumbnail.py` and `clips.py` now warn when nothing moves inside the
  box and save `cam_check.jpg`; when that happens, find the box on a frame:
  ```
  ffmpeg -ss 300 -i rec.mp4 -frames:v 1 -vf "crop=480:400:800:0,drawgrid=w=40:h=40:color=red" corner.png
  ```
  Exact edges: where the column/row brightness jumps. Auto-detecting the box was tried and
  rejected: a dark shirt on a dark terminal hides the bottom edge, and screen motion confuses it.
- **Footer.** `upload.py` now warns when the description has no links. The file behind `FOOTER`
  in the config didn't exist. The flag.h repo is https://github.com/hugoocoto/flag.h.

## Transcripts

- Whisper on the **raw** audio was much better than on the audio cleaned by `process-voice.sh`
  (punctuation kept; "operador de C que nadie conoce" instead of "operador de cegra y conoce").
  `publish.sh` now transcribes its input (`--audio-from`); `-R` brings back the old behaviour.
  One video so far: if a noisy recording does better cleaned, that's the flag to use.
- Recurring mistakes go in `~/.config/lowkeyoutuber/fixes.txt`, applied on every read. Add to it
  after reviewing each video's `video.srt`.
- A cache made with a different `--prompt` is now kept with a warning (it used to be redone,
  which threw away hand fixes in `video.words.json`). Keep jargon in the config's `JARGON` anyway.

## Cutting (only when Hugo asks)

- Only cut **content**: off-topic rambling, false starts. Propose each cut with timestamps and get
  Hugo's OK. **Never speed up or trim silences**: on this video every pause over 2.5 s had screen
  activity (typing, gdb, scrolling docs), and Hugo doesn't want them sped up.
- `cut.py rec.mkv cuts.txt --check` prints the words on both sides of each cut; then render
  without `--check`. It writes the transcript on the new timeline, which `publish.sh` reuses.
- **Listen to every cut** anyway (transcribe a ~12 s window around it). Found that way:
  - A dangling reference: cutting "Ahora voy a hacer la librería de config." left "Bueno, *lo*
    voy a hacer en otro vídeo" pointing at nothing. Cut the pause after the sentence instead.
  - A stray syllable from the word before the cut: Whisper's word times are loose on fast
    speech. Resume at a natural phrase start ("Pero bueno, nada de eso. Espero que...").
- Sync: `autocut.py`'s single-pass renderer (which `cut.py` uses) was measured at 3 ms total
  over 15 cuts. Check sync by **decoding** the audio (`ffmpeg -i cut.mkv -map 0:a a.wav`, compare
  with frames / fps); summing PCM packet durations in MKV is wrong and fakes a big drift.

## Shorts and thumbnails

- `clips.py --pick 1,3` renders proposals from `clips.json` without asking the LLM again (it
  never proposes the same clips twice). File names keep the proposal number.
- Avoid Shorts that cross a hard cut, and check how each one ends: one stopped mid-action.
- `meta.py` now ties each thumbnail text to a chapter and `thumbnail.py` takes the frame from it
  ("SEGFAULT POR MI CULPA" had landed on a hello-world frame).
- Cheap checks worth doing: loudness (`ebur128`, expect -14 LUFS), a contact sheet of the
  thumbnails and of frames from each Short, chapters against the final timeline.

## Upload

- First run needs `~/.config/lowkeyoutuber/client_secret.json` (Desktop-app OAuth client, account
  added as a test user) and a browser login; Hugo runs that one with `! ...`. After that
  `token.json` is saved and uploads run unattended, until Google expires it (7 days in Testing).
- Everything uploads **private** (unaudited API project). Hugo publishes in Studio.
- `upload.py --shorts folder/` after the video: titles from `clips.json` + `#shorts`, a link to
  the full video, ids in `shorts/uploaded.json` (already-uploaded clips are skipped).
- Quota: one long video (with captions and thumbnail) plus 3 Shorts used most of a day's quota.
- Don't write `pkill -f upload.py; python upload.py ...` in one command: `pkill -f` matches the
  shell running that same line and kills it before the upload starts.
