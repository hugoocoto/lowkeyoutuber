---
name: generated-short
summary: A Short made from a topic: script, narration with TTS, animated scenes, captions. Nothing to record.
status: ready
script: yes
inputs: the topic (required); notes, code or links to base it on; language (default config LANGUAGE); length (default 30-50 s); voice (configured TTS backend, see lky backends)
---

# Generated Short

## Steps
1. **Project:** `./lky new generated-short work/<name> --format 9:16` (add `--format 16:9` for a
   landscape version too). Check `./lky backends --kind tts`: tell the person which voice will be used
   (a clone of their voice, a preset voice, or none = they record it).
2. **Script:** write `work/<name>/script.json` (docs/project.md):
   - 5-9 lines, 30-50 s spoken. The first line is the hook: the surprising result or the question.
   - One idea per line, each with a `scene` (scenes.py -h): `title`, `text`, `code`, `terminal`,
     `diagram`, `image`, or `custom` (a brief the LLM turns into a scene). Time things to words:
     `"highlight": [{"lines": [4], "at": "coma"}]` lights line 4 when the voice says "coma".
   - `text` is what the captions show; add `spoken` when it must be said differently
     (`"main.c"` → `"main punto c"`, symbols, acronyms).
   - Code is compiled for real (`"check": "program"` or `"fragment"`), and with `"run": true` its real
     output is shown. Terminal scenes run their commands for real. Never write output by hand.
   - **APPROVAL:** show the person the script: the narration and what each scene shows.
3. **Narration:** `./tts.py -P work/<name> --draft` first (cheaper), listen to a couple of lines, then
   `./tts.py -P work/<name>`. Exit code 3 = TTS is `none`: continue with flows/script-then-record.md.
   Lines with a low match (`--verify`) were misread: fix `spoken` and run `--lines <id> -f`.
4. **Scenes:** `./scenes.py -P work/<name> --draft` (fast check), look at frames of a few:
   `ffmpeg -sseof -0.3 -i work/<name>/scenes/<id>.mp4 -frames:v 1 /tmp/<id>.jpg`. A scene that fails
   (compile error, lint) says why: fix the script line and run `--lines <id>`. Then
   `./scenes.py -P work/<name>` for the final quality. The person may edit `scenes/<id>/index.html`
   by hand; edited scenes are kept.
5. **Automatic check (optional):** `./judge.py -P work/<name> --scenes` (text inside the frame,
   readable, picture matches the line). Fix what fails before the person looks.
6. **Timeline:** `./tts.py -P work/<name> --place` puts each line's voice over its scene. Turn captions on
   in `project.json`: `"captions": {"burn": true, "style": "word-highlight"}`.
7. **Preview:** `./render.py -P work/<name> --preview --frames auto` and look at the frames.
   - **APPROVAL:** the person watches `out/preview.mp4`.
8. **Render:** `./render.py -P work/<name>` → `out/video.mp4` (+ `video_16x9.mp4`) and `out/video.srt`.
9. **Metadata (optional):** `python meta.py work/<name>/out/video.mp4 -o work/<name>/out`.

## Checks
`./lky check -P work/<name>`, plus frames of every line: text inside the frame, nothing covered by
captions, code readable.

## Limits
- A cloned or synthetic voice may need YouTube's "altered or synthetic content" flag: tell the person.
- Scenes render in headless Chrome: about 10 s per scene second on a machine with 8 GB of RAM and no
  GPU, much faster with a GPU and more memory. Use `--draft` while iterating.
