# YouTube toolkit

From an OBS recording to a folder that's ready to upload, plus Shorts.

| Script | What it does |
|---|---|
| `process-voice.sh` | Cleans the voice (high-pass, RNNoise, EQ, de-esser, compressor) and normalizes to -14 LUFS. `-c` = clean only, `-L` = loudness only. |
| `subtitles.py` | Whisper transcription -> SRT/VTT. |
| `meta.py` | Local LLM writes chapters, 5 title options, description and tags. |
| `clips.py` | Picks the best moments and renders vertical 9:16 Shorts with word-by-word captions. |
| `publish.sh` | Runs all of the above in one command, into one folder. |
| `upload.py` | Uploads that folder to YouTube (private), with subtitles and thumbnail. |
| `yt_common.py` | Shared code (transcript cache, Ollama client, encoder). Not run directly. |

Whisper runs once per video: the transcript is cached in `<video>.words.json` and
shared by every script. GPU: Whisper uses CUDA, video is encoded with NVENC,
`publish.sh` runs GPU steps through `prime-run`, and Ollama uses `ollama-cuda`.

## Workflow

1. **Record** in OBS: MKV, peaks around -12 to -6 dB. If you also capture desktop/music audio,
   put the mic on its own track (e.g. track 2) and desktop/music on track 1.
2. **Cut by hand** (Kdenlive, Shotcut...). Silent moments in a screen recording often show
   something important (typing, a command running), so cutting is left to you.
   - Mic only: edit the raw recording and export.
   - Mic + desktop/music on separate tracks: editors mix all tracks into one on export, so clean
     the voice first: `./process-voice.sh -t 1 -m 0 -c rec.mkv` -> edit `rec_clean.mkv`, export,
     and use `-L` below.
3. **Process everything**:
   ```
   ./publish.sh -F links.txt edit.mp4          # mic-only export
   ./publish.sh -L -F links.txt edit.mp4       # export of rec_clean.mkv (voice already cleaned)
   ./publish.sh -s -C "--cam 934:384:346:303" -p "PM, Neovim, Lua" edit.mp4   # + Shorts
   ```
   - `-p` gives Whisper the names and jargon it would otherwise misspell (it wrote "Neobim").
   - `-C "--cam x:y:w:h"` is your facecam box in the recording, in pixels. Shorts put it under the
     screen, with captions below it. Check the layout with `python clips.py edit.mp4 --cam ... --preview`.
     If your OBS layout doesn't change, the box doesn't either.
   Output in `edit_publish/`: `video.mp4`, `video.srt`, `titles.txt`, `description.txt`
   (with chapters), `tags.txt`, `thumbs/`, `shorts/`.
4. **Review**: reorder `titles.txt` (first line is the title), fix `description.txt`
   and `video.srt`, and save your thumbnail as `thumbnail.jpg` in the folder.
   Re-running `publish.sh` skips steps that are already done. Delete a file to redo its step.
5. **Upload**: `python upload.py edit_publish/` (private), check it in Studio and publish there.
   Or upload by hand.

## Install (Arch Linux)

```
sudo pacman -S ffmpeg curl ttf-dejavu ollama-cuda nvidia-prime
sudo systemctl enable --now ollama
ollama pull qwen2.5:7b            # 4 GB VRAM: runs partly on CPU; qwen2.5:3b fits fully (--llm qwen2.5:3b)
python -m venv ~/.venvs/subs
~/.venvs/subs/bin/pip install faster-whisper nvidia-cublas-cu12 'nvidia-cudnn-cu12==9.*'
~/.venvs/subs/bin/pip install google-api-python-client google-auth-oauthlib   # only for upload.py
```

`publish.sh` uses `~/.venvs/subs/bin/python` automatically. To run the Python scripts directly,
`source ~/.venvs/subs/bin/activate` first. The CUDA libraries from pip are found automatically.
Without a GPU everything falls back to the CPU (use `-m small` for Whisper and `--encoder libx264`).

With 4 GB of VRAM, Whisper and the Ollama model don't fit together: before transcribing, the
scripts ask Ollama to unload its model.

For `upload.py`, a one-time Google Cloud setup is described in `python upload.py -h`.

Each script documents its options with `-h`.
Always review the output: transcription, retake detection and the LLM all make mistakes.
