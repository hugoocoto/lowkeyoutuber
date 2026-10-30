#!/usr/bin/env bash
# =============================================================================
# publish.sh - From a cut recording to a folder ready to upload to YouTube
# =============================================================================
#
# Runs, in order: process-voice.sh (clean audio, -14 LUFS), subtitles.py,
# meta.py (chapters, titles, description, tags), thumbnail.py, optionally
# clips.py (Shorts), then checks everything for upload.py. GPU work (Whisper
# on CUDA, NVENC) runs through prime-run on PRIME laptops. Defaults such as
# language, jargon, facecam box and footer come from ~/.config/lowkeyoutuber/config.
#
# ---USAGE---
# Usage: publish.sh [options] video.mkv
#
#   -t N     audio track with the voice, 0-based          (default 0)
#   -m N     audio track with music/desktop audio to mix under the voice
#   -L       audio already cleaned (process-voice.sh -c): only normalize loudness
#   -n       no noise reduction
#   -l LANG  language code for Whisper                    (default: config LANGUAGE)
#   -p TEXT  names/jargon for Whisper, e.g. "PipeWire, OBS" (default: config JARGON)
#   -W NAME  Whisper model                                (default large-v3-turbo)
#   -F FILE  text appended to the description (default: config FOOTER)
#   -s       also make Shorts (asks which clips to render)
#   -C OPTS  extra clips.py options, e.g. -C "--count 3 --max 45"
#   -u       upload to YouTube as private at the end (see upload.py -h)
#   -o DIR   output folder                                (default: <video>_publish)
#   -f       redo every step, even if its output exists
#
# Output folder:
#   video.mp4        final video
#   video.srt        subtitles
#   titles.txt       5 title options; the first line is the one uploaded
#   description.txt  description with chapters
#   tags.txt         comma-separated tags
#   chapters.txt, summary.txt
#   thumbs/          thumbnail designs; thumbnail.jpg = design 1 (replace with your pick)
#   shorts/          vertical clips (with -s)
#
# Steps whose output already exists are skipped, so after fixing something
# (editing description.txt, deleting video.srt to redo it...) just run it again.
#
# Examples:
#   publish.sh edit.mp4                       # mic-only export from your editor
#   publish.sh -L edit.mp4                    # export of a process-voice.sh -c file
#
# Python: uses $PYTHON, else ~/.venvs/subs/bin/python, else python3.
# ---END USAGE---

set -euo pipefail

TRACK=0
MUSIC=""
LOUD_ONLY=0
NO_NR=0
LANGCODE=""
PROMPT=""
MODEL=""
FOOTER=""
SHORTS=0
CLIPS_OPTS=""
UPLOAD=0
OUT=""
FORCE=0

usage() { sed -n '/^# ---USAGE---$/,/^# ---END USAGE---$/p' "$0" | sed '1d;$d;s/^# \{0,1\}//'; exit 1; }
die()   { echo "Error: $*" >&2; exit 1; }
step()  { echo; echo "=== $* ==="; }
todo()  { [[ $FORCE -eq 1 || ! -s "$1" ]]; }   # true if the step has to run

while getopts "t:m:Lnl:p:W:F:sC:uo:fh" opt; do
  case $opt in
    t) TRACK="$OPTARG" ;;
    m) MUSIC="$OPTARG" ;;
    L) LOUD_ONLY=1 ;;
    n) NO_NR=1 ;;
    l) LANGCODE="$OPTARG" ;;
    p) PROMPT="$OPTARG" ;;
    W) MODEL="$OPTARG" ;;
    F) FOOTER="$OPTARG" ;;
    s) SHORTS=1 ;;
    C) CLIPS_OPTS="$OPTARG" ;;
    u) UPLOAD=1 ;;
    o) OUT="$OPTARG" ;;
    f) FORCE=1 ;;
    *) usage ;;
  esac
done
shift $((OPTIND - 1))
[[ $# -eq 1 ]] || usage
IN="$1"
[[ -f "$IN" ]] || die "file not found: $IN"
[[ -z "$FOOTER" || -f "$FOOTER" ]] || die "footer file not found: $FOOTER"
command -v ffmpeg >/dev/null || die "ffmpeg not installed"

HERE="$(dirname "$(readlink -f "$0")")"
OUT="${OUT:-${IN%.*}_publish}"
mkdir -p "$OUT"

PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  if [[ -x "$HOME/.venvs/subs/bin/python" ]]; then PY="$HOME/.venvs/subs/bin/python"; else PY=python3; fi
fi
# Hybrid-graphics laptops: run GPU work on the NVIDIA card
GPU=()
command -v prime-run >/dev/null && GPU=(prime-run)

WHISPER=()
[[ -z "$LANGCODE" ]] || WHISPER+=(-l "$LANGCODE")
[[ -z "$PROMPT" ]]   || WHISPER+=(--prompt "$PROMPT")
[[ -z "$MODEL" ]]    || WHISPER+=(-m "$MODEL")

VIDEO="$OUT/video.mp4"

if todo "$VIDEO"; then
  step "1/5 Audio: clean voice, -14 LUFS"
  pv=(-f -t "$TRACK" -o "$VIDEO")
  [[ -z "$MUSIC" ]]       || pv+=(-m "$MUSIC")
  [[ $LOUD_ONLY -eq 0 ]]  || pv+=(-L)
  [[ $NO_NR -eq 0 ]]      || pv+=(-n)
  "$HERE/process-voice.sh" "${pv[@]}" "$IN"
fi

if todo "$OUT/video.srt"; then
  step "2/5 Subtitles"
  "${GPU[@]}" "$PY" "$HERE/subtitles.py" "${WHISPER[@]}" "$VIDEO" > "$OUT/video.srt.tmp" \
    || { rm -f "$OUT/video.srt.tmp"; die "subtitles failed"; }
  mv "$OUT/video.srt.tmp" "$OUT/video.srt"
fi

if todo "$OUT/description.txt"; then
  step "3/5 Chapters, titles, description, tags"
  meta=(-o "$OUT")
  [[ -z "$FOOTER" ]] || meta+=(--footer "$FOOTER")
  "${GPU[@]}" "$PY" "$HERE/meta.py" "${WHISPER[@]}" "${meta[@]}" "$VIDEO"
fi

if todo "$OUT/thumbs/design_1.jpg"; then
  step "4/5 Thumbnail designs"
  "$PY" "$HERE/thumbnail.py" -o "$OUT" "$VIDEO"
fi

if [[ $SHORTS -eq 1 ]]; then
  if [[ $FORCE -eq 1 ]] || ! compgen -G "$OUT/shorts/*.mp4" >/dev/null; then
    step "5/5 Shorts"
    read -r -a extra <<<"$CLIPS_OPTS"
    "${GPU[@]}" "$PY" "$HERE/clips.py" "${WHISPER[@]}" "${extra[@]}" -o "$OUT/shorts" "$VIDEO"
  fi
fi

step "Ready to upload?"
"$PY" "$HERE/upload.py" --dry-run "$OUT" || true
echo
echo "Done: $OUT"
echo "Check before uploading:"
echo "  titles.txt       first line is the title"
echo "  description.txt  text, chapters, links"
echo "  video.srt        names and jargon"
echo "  thumbnail.jpg    design 1; copy another from thumbs/ or make your own"

if [[ $UPLOAD -eq 1 ]]; then
  step "Upload (private)"
  "$PY" "$HERE/upload.py" "$OUT"
else
  echo "Then: $PY $HERE/upload.py \"$OUT\"   (or upload by hand in YouTube Studio)"
fi
