---
name: script-then-record
summary: You write the script; the person records it with a teleprompter (OBS takes per line); the takes are matched to the script and edited together.
status: ready
script: yes
inputs: the topic and notes (required); length; which parts are recorded (screen, camera) and which are generated; language; whether OBS's WebSocket server is on
---

# Script, then record

## Steps
1. **Project:** `./lky new script-then-record work/<name>`.
2. **Script:** write `script.json`: short lines (one breath each), in the order they'll be recorded.
   Lines the person won't record can have a `scene` (see flows/generated-short.md) and TTS. Note:
   `align.py --apply` (both modes) refuses to write the timeline unless every script line has a
   recording, and `tts.py --place` refuses a script where only some lines have a picture. With
   recorded and generated lines mixed, use the `align.py` report for the recorded lines' times and
   build `timeline.video` by hand, one item per line (`scene:<id>` media for the generated ones,
   padding as in step 4). In step 4, only recorded lines need a take ("not found" for a generated line
   is expected), and skip step 5 (`--apply`) once the timeline is built by hand.
   - **APPROVAL:** the person reads it and changes anything that doesn't sound like them.
3. **Teleprompter:** ask whether OBS's WebSocket server is on (OBS: Tools > WebSocket Server Settings)
   and the password is in `OBS_WEBSOCKET_PASSWORD`. Then tell the person to run (it opens a page):
   `./teleprompter.py -P work/<name> --obs`. In the page: Space starts/stops a take, → next line,
   B marks the last take bad, R retakes, Q finishes. Every take is its own OBS file.
   Without OBS: `./teleprompter.py -P work/<name>` only shows the text; they record one long take.
4. **Match the takes:**
   - OBS takes: `./align.py -P work/<name> --from-takes` (last good take of each line).
   - One long take: `./lky add-media -P work/<name> rec.mkv --id rec --track <voice track>`, then
     `./align.py -P work/<name> --media rec` (the last complete attempt of each line is kept).
   It prints each line's take; the lines said differently from the script are marked `changed` in
   `proposals/takes.json`, with what was really said in `said` (the OBS output doesn't show it).
   - Lines **not found**: ask the person to record them again, or drop them from the script.
     With OBS takes, a retake is just another take: run `--from-takes` again. With one long take, a
     second file is fine, but `align.py` works on one recording at a time and each run overwrites
     `proposals/takes.json`:
     1. `cp work/<name>/proposals/takes.json work/<name>/proposals/takes-rec.json`;
     2. `./lky add-media -P work/<name> rec2.mkv --id rec2 --track <voice track>`, then
        `./align.py -P work/<name> --media rec2` to find the missing lines' times;
     3. build `timeline.video` by hand from both reports: one item per line, `src` the recording it
        came from, `in` = `start` - 0.15 (not below 0) and `out` = `end` + 0.25 (not past the end of
        the file), the same padding `align.py --apply` uses. Listen to every join, then skip step 5.
   - **APPROVAL:** show the report; the person accepts lines said differently, or re-records them.
5. **Timeline:** the same command with `--apply` puts the chosen takes one after another (takes are kept
   whole: no pause inside a take is trimmed). Add zooms where the screen matters (flows/edit-recording.md
   step 5).
6. **Render:** `./render.py -P work/<name> --preview --frames auto`, then `./render.py -P work/<name>`.
7. **Publish:** `./publish.sh -L work/<name>/out/video.mp4` for subtitles, chapters, metadata,
   thumbnails; upload as in flows/edit-recording.md.

## Checks
`./lky check -P work/<name>`; listen to every join between takes.
