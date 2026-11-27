---
name: generated-short
summary: A Short made from a topic: script, narration with TTS, visuals, captions. Nothing to record.
status: partial
script: yes
inputs: the topic (required); notes, code or links to base it on; language (default config LANGUAGE); length (default 30-50 s); voice (configured TTS backend, see lky backends)
---

# Generated Short

## Steps
1. **Project:** `./lky new generated-short work/<name> --format 9:16`. Check `./lky backends --kind tts`:
   say which voice will be used (a clone of the person's voice, a preset voice, or none = they record it).
2. **Script:** write `work/<name>/script.json` (format in docs/project.md):
   - 5-9 lines, 30-50 s spoken. The first line is the hook: the surprising result or the question.
   - One idea per line. Code on screen: at most ~12 short lines, readable on a phone.
   - `text` is what the captions show; add `spoken` when it must be said differently
     (`"main.c"` → `"main punto c"`, symbols, acronyms).
   - Every code snippet must compile, and any output shown must come from really running it
     (`cc -Wall -o /tmp/x x.c && /tmp/x`). Never invent program output.
   - **APPROVAL:** show the person the script (text, and what will be on screen for each line).
3. **Narration:** `./tts.py -P work/<name> --draft` first (cheaper), listen to a couple of lines,
   then `./tts.py -P work/<name>` for the final voice. Exit code 3 = TTS is `none`: ask the person to
   record the lines and continue with flows/script-then-record.md instead.
   Lines with a low match (`--verify`) were misread: fix `spoken` and run `--lines <id> -f`.
4. **Visuals:** one image or clip per line, 1080x1920 or anything (it's fitted with a blurred
   background). Until scenes.py exists (issue #1 M4), make them with any tool available on this
   machine (a code-to-image tool such as `freeze` or `silicon`, a terminal screenshot, an HTML page
   screenshot, a frame of an earlier recording). Ask the person if nothing suitable is installed.
   Put each file in `media/` and set `"visual": "media/03-code.png"` on its line (or
   `{"src": "<media id>", "in": 12.0}` for a clip, optionally with `zooms`).
5. **Timeline:** `./tts.py -P work/<name> --place` puts the lines one after another and each visual
   under its line. Turn captions on in `project.json`: `"captions": {"burn": true, "style": "word-highlight"}`.
6. **Preview:** `./render.py -P work/<name> --preview --frames auto` and look at the frames.
   - **APPROVAL:** the person watches `out/preview.mp4`.
7. **Render:** `./render.py -P work/<name>` → `out/video.mp4` + `out/video.srt`.
8. **Metadata (optional):** `python meta.py work/<name>/out/video.mp4 -o work/<name>/out` for titles,
   description and tags.

## Checks
`./lky check -P work/<name>` (loudness, sync, subtitles), plus frames of every line: text inside the
frame, nothing covered by captions, code readable.

## Not built yet
- Animated scenes written as code (scenes.py with HyperFrames, issue #1 M4): today visuals are stills or clips.
- Automatic visual checks (judge backend, M8).
- Disclosure: a cloned or synthetic voice may need YouTube's "altered or synthetic content" flag; tell the person.
