# Project format (version 1)

The tools that work on a timeline share a project folder made by `lky new <dir>`. Agents edit
`project.json` and `script.json`, and `render.py` turns them into video. Tools that work on plain
files (`cut.py`, `subtitles.py`, `meta.py`, `thumbnail.py`, `clips.py`, `upload.py`) don't need one;
keep their results in the project's `out/`, each in its own way: `meta.py -o out`, `thumbnail.py -o out`
and `clips.py -o out/shorts` take a folder; `cut.py -o out/cut.mkv` takes a file; `subtitles.py` prints
to stdout (`> out/video.srt`); `upload.py out/` takes the folder as an argument.

```
myproject/
  project.json   formats, media, timeline, state (+ optional flow, inputs)
  script.json    the lines (videos with a script)
  media/         inputs (or referenced by absolute path: big recordings aren't copied)
  narration/     tts.py output: <id>.wav, <id>.words.json, <id>.json
  proposals/     waiting for a person's OK: cuts.txt, takes.json, ...
  checks/        report.json, frames/
  out/           video.mp4, video_9x16.mp4, video.srt
```

## project.json

```json
{
  "version": 1,
  "flow": "generated-short",
  "lang": "es",
  "fps": 30,
  "formats": {"9:16": [1080, 1920]},
  "inputs": {"topic": "the comma operator"},
  "state": {"tts": "done", "render": "pending"},
  "media": {
    "rec":         {"file": "/home/hugo/rec/rec.mkv", "kind": "video", "track": 1, "words": "/home/hugo/rec/rec.words.json"},
    "card":        {"file": "media/card.png", "kind": "image"},
    "vo:01-hook":  {"file": "narration/01-hook.wav", "words": "narration/01-hook.words.json", "kind": "audio"}
  },
  "timeline": {
    "video": [
      {"src": "card", "dur": 2.5},
      {"src": "rec", "in": 312.0, "out": 340.5, "zooms": [{"at": 4.0, "dur": 6.0, "rect": [0.05, 0.4, 0.5, 0.3]}]},
      {"color": "#101010", "dur": 1.0}
    ],
    "audio": [
      {"src": "vo:01-hook", "at": 0.3},
      {"src": "media/music.mp3", "at": 0, "duck": true, "gain_db": -22}
    ],
    "captions": {"burn": true, "style": "word-highlight", "highlight": "FFD700"}
  }
}
```

- **flow**, **inputs**: optional notes: the example in `flows/` the project started from, and what
  the person asked for. No tool requires them.
- **lang**: language code (`en`, `es`...), from `lky new --lang`, else the config's `LANGUAGE`, else
  `LKY_LANG`; `null` means unknown: ask the person or detect it from the recording before narration (TTS
  backends refuse to run without it; Whisper auto-detects).
- **formats**: name → `[width, height]` (even numbers). `lky new --format` knows `16:9` (YouTube,
  presentations), `9:16` (Shorts, Reels, TikTok), `1:1` (X, LinkedIn) and `4:5` (LinkedIn, Instagram
  feeds); any other size can be written here by hand. The first one renders to `out/video.mp4`, the
  others to `out/video_<name>.mp4` (`9:16` → `video_9x16.mp4`).
- **media**: id → file. Paths are relative to the project, or absolute. `words` is a transcript in the
  `*.words.json` shape (`{"words": [{"w", "s", "e"}]}`); `track` is the voice's audio track;
  `variants` gives one file per format (`{"16:9": "scenes/01_16x9.mp4"}`, written by scenes.py);
  `events` is capture.py's action log. Ids by convention: `vo:<line>` narration, `scene:<line>`,
  `take:<line>` teleprompter takes, `cap` an app capture.
- **state**: step → `pending | proposed | approved | done | skipped | needs-human | failed`
  (or `{"state": ..., "note": ...}`). `proposed` and `needs-human` mean: ask the person.

### timeline.video (played one after another)
| field | meaning |
|---|---|
| `src` | media id or file (video, image) |
| `color` | instead of `src`: a solid colour (`"#000000"`, `"black"`) |
| `in`, `out` | seconds in the source (videos) |
| `dur` | length in seconds (images, colours; or a video from its start) |
| `audio` | keep the clip's own sound (default `true`), on its `track` |
| `gain_db` | volume of the clip's sound |
| `fit` | `pad` (black bars), `blur` (blurred copy behind), `fill` (crop). Default: `blur` when the shape differs by more than 15 %, else `pad` |
| `zooms` | `[{"at", "dur", "rect": [x, y, w, h]}]`: `at` from the item's start, `rect` as fractions of the source frame (0-1). Eased in and out over 0.45 s; zooms of one item must not overlap |

### timeline.audio (placed at a time, mixed)
| field | meaning |
|---|---|
| `src` | media id or file |
| `at` | start, seconds on the timeline |
| `in`, `out` | part of the source (default: all of it) |
| `gain_db` | volume (default 0; -22 for `duck`) |
| `duck` | music: goes down while anything else sounds (sidechain) |
| `track` | audio track of the source |

Audio past the end of the video is cut. The mix is normalized to -14 LUFS / -1 dBTP.

### timeline.captions
Word timings come from the media's `words` (TTS lines, transcribed recordings), moved to where they
play. `out/video.srt` is always written from them. `burn: true` draws them on the picture;
`style`: `word-highlight` (current word highlighted, like clips.py) or `plain`.

## script.json

```json
{
  "title": "El operador coma",
  "lang": "es",
  "lines": [
    {"id": "01-hook", "text": "¿Sabías que esta línea compila?", "visual": "media/01.png"},
    {"id": "02-code", "text": "int x = (1, 2, 3);", "spoken": "int x igual a uno, dos, tres", "visual": {"src": "rec", "in": 12.0}},
    {"id": "03-why",  "text": "La coma evalúa todo y se queda con lo último.", "at": 9.5}
  ]
}
```

| field | used by |
|---|---|
| `id` | file names (`narration/<id>.wav`), media ids (`vo:<id>`) |
| `text` | captions and subtitles (exact spelling) |
| `spoken` | what TTS says, when different from `text` |
| `visual` | `tts.py --place`: what's on screen during the line (a file or media id, or an object like a timeline video item) |
| `at` | `tts.py --place`: start time for voice-overs on an existing video |
| `pad_before`, `pad_after` | first/last line: seconds of picture before the voice starts / after it ends (0.3 / 0.8) |
| `scene` | `scenes.py`: the line's animated scene (`{"type": "code", ...}`, see `scenes.py -h`); becomes media `scene:<id>` and the line's picture |
| `actions` | `capture.py`: what to do in the app during the line (see `capture.py -h`) |
| anything else | free for your own notes (`action`, `notes`, ...) |
