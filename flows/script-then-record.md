---
name: script-then-record
summary: You write the script; the person records it (reading it); the takes are matched to the script and edited together.
status: partial
script: yes
inputs: the topic and notes (required); length; which parts are recorded (screen, camera) and which are generated; language
---

# Script, then record

## Steps
1. **Project:** `./lky new script-then-record work/<name>`.
2. **Script:** write `script.json`: short lines (one breath each), in the order they'll be recorded.
   Lines the person won't record (generated cards, narration over images) get `"visual"` and are
   narrated with TTS later.
   - **APPROVAL:** the person reads it and changes anything that doesn't sound like them.
3. **Teleprompter:** until teleprompter.py exists (issue #1 M6), write `out/teleprompter.txt` with one
   line per paragraph, big gaps between lines, and tell the person:
   - read each line; if it goes wrong, pause and say the whole line again (the last take is kept);
   - record with OBS as usual (voice on its own track).
4. **Recording:** when the person gives you the file:
   `./lky add-media -P work/<name> rec.mkv --id rec --track <voice track>`.
5. **Match the takes:** `./align.py -P work/<name> --media rec` writes `proposals/takes.json` and prints
   each line's take, how many retakes were dropped, and lines said differently from the script.
   - Lines **not found**: ask the person to record them (a second file is fine: add it as another
     media and edit the timeline by hand), or drop them from the script.
   - **APPROVAL:** show the report; the person accepts lines said differently, or re-records them.
6. **Timeline:** `./align.py -P work/<name> --media rec --apply` puts the chosen takes one after
   another (takes are kept whole: no pause inside a take is trimmed). Add zooms where the screen
   matters (see flows/edit-recording.md step 5).
7. **Render:** `./render.py -P work/<name> --preview --frames auto`, then `./render.py -P work/<name>`.
8. **Publish:** `./publish.sh -L work/<name>/out/video.mp4` for subtitles, chapters, metadata,
   thumbnails; upload as in flows/edit-recording.md.

## Checks
`./lky check -P work/<name>`; listen to every join between takes.

## Not built yet
- teleprompter.py controlling OBS (one recording per line, labelled), issue #1 M6.
- Mixing recorded lines and TTS lines in one timeline is possible by editing `project.json`, but no
  tool does it automatically yet.
