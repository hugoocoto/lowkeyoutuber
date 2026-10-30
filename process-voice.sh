#!/usr/bin/env bash
# =============================================================================
# process-voice.sh - Voice post-processing for YouTube recordings
# =============================================================================
#
# PURPOSE
#   Takes a recording made in OBS and produces a finished video whose voice
#   audio is cleaned, tonally balanced and normalized to YouTube's loudness
#   target. The video stream is never re-encoded. Only ffmpeg is required.
#
# WORKFLOW: FROM OBS TO A FINISHED VIDEO
#
#   1. OBS SETUP (done once)
#      - Settings > Audio: sample rate 48 kHz.
#      - Add the Blue Yeti as an Audio Input Capture source, pattern set to
#        cardioid, mono enabled in Advanced Audio Properties.
#      - Settings > Output > Recording: format MKV (survives crashes), and
#        enable two audio tracks. Assign the microphone to its own track
#        (for example Track 2) and desktop/music audio to Track 1.
#      - Mic filters, kept minimal: high-pass at about 80 Hz and a limiter at
#        -3 dB as a safety net. Noise suppression and compression are applied
#        later by this script, where they can be adjusted and redone.
#
#   2. RECORDING (every session)
#      - Record 10 seconds of silence first (room tone).
#      - Keep the mic 5-10 cm from the mouth, speaking into the front of it.
#      - Set the gain so that peaks land between -12 and -6 dB in OBS.
#        Clipping cannot be repaired afterwards.
#      - Monitor with closed-back headphones.
#
#   3. EDITING
#      - Edit by hand (Kdenlive, Shotcut, DaVinci Resolve, ...). Editors
#        usually mix all audio tracks into one on export. To still get a clean
#        voice, run this script with -c on the RAW recording first (cleans the
#        voice, keeps the tracks, no loudness change), edit that file, then run
#        it with -L on the export (loudness normalization only).
#      - Loudness is always measured last, on the final content.
#
#   4. PROCESSING (this script)
#      Voice track, in this order:
#        a. High-pass at 80 Hz       removes rumble and desk thumps
#        b. Noise reduction          RNNoise model (afftdn as fallback)
#        c. EQ                       -3 dB at 250 Hz (mud), +2 dB at 4 kHz
#                                    (presence), +2 dB shelf at 10 kHz (air)
#        d. De-esser                 reduces harsh "s" sounds
#        e. Compressor               3:1, evens out volume differences
#      Optional: the music track is lowered and ducked under the voice.
#      Final: two-pass loudness normalization to -14 LUFS, true peak -1 dBTP,
#      resampled to 48 kHz. Output is MP4 (video copied, audio AAC 320k) or
#      24-bit WAV with -a.
#
#   5. VERIFICATION
#      - Run once with -s (first 30 seconds) while tuning the settings.
#      - Listen to the result on earbuds and phone speakers, since that is how
#        most viewers listen. If the voice sounds watery, reduce or disable
#        noise reduction (-n). If it sounds harsh, lower the 4 kHz boost or
#        strengthen the de-esser in the CHAIN section below.
#
#   6. UPLOAD
#      - Upload the processed MP4. YouTube normalizes around -14 LUFS, so no
#        further loudness adjustment is needed.
#
# ---USAGE---
# Usage: process-voice.sh [options] file.mkv [file2.mkv ...]
#
#   -t N     audio track with the mic, 0-based            (default 0)
#   -m N     audio track with music/desktop audio to mix under the voice
#   -M dB    music level before ducking                   (default -22)
#   -c       clean only: voice chain without loudness normalization, output
#            <name>_clean.mkv with the cleaned voice as track 0 and the -m track
#            (untouched) as track 1, both lossless. Use before editing by hand.
#   -L       loudness only: skip the voice chain (audio already cleaned with -c)
#   -n       no noise reduction (good if your room is quiet/treated)
#   -a       audio-only output (24-bit WAV) instead of video
#   -s       sample mode: process only the first 30 s (for tuning)
#   -d DIR   output directory                             (default: next to input)
#   -o FILE  output file name (single input only)
#   -f       overwrite existing output files
#   -l LUFS  loudness target                              (default -14)
#
# Examples:
#   process-voice.sh -t 1 edit.mkv
#   process-voice.sh -t 1 -m 0 -M -24 edit.mkv       # voice on track 2, music on track 1
#   process-voice.sh -t 1 -s edit.mkv                # quick 30 s preview
#   process-voice.sh -t 1 -d out/ *.mkv              # batch
#   process-voice.sh -t 1 -m 0 -c raw.mkv            # clean before editing -> raw_clean.mkv
#   process-voice.sh -L edit.mkv                     # after editing: loudness only
#
# Dependencies (Arch): sudo pacman -S ffmpeg curl
# ---END USAGE---

set -euo pipefail

TRACK=0
MUSIC=""
MUSIC_DB=-22
NO_NR=0
AUDIO_ONLY=0
SAMPLE=0
OUTDIR=""
OUT=""
FORCE=0
CLEAN=0
LOUD_ONLY=0
TARGET_LUFS=-14
TARGET_TP=-1
TARGET_LRA=11

RNN_URL="https://raw.githubusercontent.com/GregorR/rnnoise-models/master/beguiling-drafter-2018-08-30/bd.rnnn"
RNN_MODEL="${XDG_DATA_HOME:-$HOME/.local/share}/process-voice/bd.rnnn"

usage() { sed -n '/^# ---USAGE---$/,/^# ---END USAGE---$/p' "$0" | sed '1d;$d;s/^# \{0,1\}//'; exit 1; }
die()   { echo "Error: $*" >&2; exit 1; }

while getopts "t:m:M:cLnasd:o:fl:h" opt; do
  case $opt in
    t) TRACK="$OPTARG" ;;
    m) MUSIC="$OPTARG" ;;
    M) MUSIC_DB="$OPTARG" ;;
    c) CLEAN=1 ;;
    L) LOUD_ONLY=1 ;;
    n) NO_NR=1 ;;
    a) AUDIO_ONLY=1 ;;
    s) SAMPLE=1 ;;
    d) OUTDIR="$OPTARG" ;;
    o) OUT="$OPTARG" ;;
    f) FORCE=1 ;;
    l) TARGET_LUFS="$OPTARG" ;;
    *) usage ;;
  esac
done
shift $((OPTIND - 1))
[[ $# -ge 1 ]] || usage
[[ -z "$OUT" || $# -eq 1 ]] || die "-o only works with a single input file"
[[ $CLEAN -eq 0 || $LOUD_ONLY -eq 0 ]] || die "-c and -L can't be combined"

command -v ffmpeg  >/dev/null || die "ffmpeg not installed"
command -v ffprobe >/dev/null || die "ffprobe not installed"
[[ -z "$OUTDIR" ]] || mkdir -p "$OUTDIR"

# ---------------------------------------------------------------------------
# Noise reduction: RNNoise model (auto-download once), fallback to afftdn
# ---------------------------------------------------------------------------
NR_FILTER=""
if [[ $NO_NR -eq 0 && $LOUD_ONLY -eq 0 ]]; then
  if [[ ! -s "$RNN_MODEL" ]]; then
    echo ">> Downloading RNNoise model..."
    mkdir -p "$(dirname "$RNN_MODEL")"
    if command -v curl >/dev/null && curl -fsSL "$RNN_URL" -o "$RNN_MODEL.tmp"; then
      mv "$RNN_MODEL.tmp" "$RNN_MODEL"
    else
      rm -f "$RNN_MODEL.tmp"
      echo "   download failed, falling back to afftdn" >&2
    fi
  fi
  if [[ -s "$RNN_MODEL" ]] && ffmpeg -hide_banner -filters 2>/dev/null | grep -q " arnndn "; then
    NR_FILTER="arnndn=m=${RNN_MODEL}"
  else
    NR_FILTER="afftdn=nr=10:nf=-35:tn=1"
  fi
fi

# ---------------------------------------------------------------------------
# Voice chain (tweak values here)
# ---------------------------------------------------------------------------
CHAIN=("highpass=f=80")
[[ -z "$NR_FILTER" ]] || CHAIN+=("$NR_FILTER")
CHAIN+=(
  "equalizer=f=250:t=q:w=1:g=-3"          # cut mud
  "equalizer=f=4000:t=q:w=1:g=2"          # presence
  "highshelf=f=10000:g=2"                 # air
  "deesser=i=0.4:m=0.5:f=0.5"             # tame sibilance
  "acompressor=threshold=-18dB:ratio=3:attack=6:release=60:makeup=3dB"
)
VOICE=$(IFS=,; echo "${CHAIN[*]}")
[[ $LOUD_ONLY -eq 0 ]] || VOICE="anull"

LN_BASE="I=${TARGET_LUFS}:TP=${TARGET_TP}:LRA=${TARGET_LRA}"

# build_graph "<tail filters>" -> filtergraph ending in [out]
build_graph() {
  local tail="$1"
  if [[ -n "$MUSIC" ]]; then
    # Voice is split: one copy is mixed, one drives the music's sidechain (ducking)
    echo "[0:a:${TRACK}]${VOICE},asplit=2[v1][v2];" \
         "[0:a:${MUSIC}]volume=${MUSIC_DB}dB[m0];" \
         "[m0][v2]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=500[md];" \
         "[v1][md]amix=inputs=2:duration=first:normalize=0[mix];" \
         "[mix]${tail}[out]" | tr -d '\n' | sed 's/; /;/g'
  else
    echo "[0:a:${TRACK}]${VOICE},${tail}[out]"
  fi
}

# extract a quoted JSON value from loudnorm output
jval() { sed -n "s/.*\"$1\" *: *\"\([^\"]*\)\".*/\1/p" <<<"$STATS" | head -1; }

process_one() {
  local IN="$1"
  [[ -f "$IN" ]] || { echo "Skipping (not found): $IN" >&2; return 1; }

  local n_audio
  n_audio=$(ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$IN" | wc -l)
  [[ $TRACK -lt $n_audio ]] || { echo "Skipping $IN: no audio track $TRACK (has $n_audio)" >&2; return 1; }
  [[ -z "$MUSIC" || $MUSIC -lt $n_audio ]] || { echo "Skipping $IN: no music track $MUSIC" >&2; return 1; }

  local base ext suffix dest
  base="$(basename "${IN%.*}")"
  suffix=processed
  if [[ $AUDIO_ONLY -eq 1 ]]; then ext=wav
  elif [[ $CLEAN -eq 1 ]]; then ext=mkv
  else ext=mp4; fi
  [[ $CLEAN -eq 0 ]] || suffix=clean
  if [[ -n "$OUT" ]]; then dest="$OUT"
  else dest="${OUTDIR:-$(dirname "$IN")}/${base}_${suffix}.${ext}"; fi

  if [[ -e "$dest" && $FORCE -eq 0 ]]; then
    echo "Skipping (exists, use -f): $dest"; return 0
  fi

  local limit=()
  [[ $SAMPLE -eq 0 ]] || limit=(-t 30)

  echo ">> $IN  (voice track $TRACK${MUSIC:+, music track $MUSIC})"

  if [[ $CLEAN -eq 1 ]]; then
    local maps=(-map "[out]") codecs=(-c:a pcm_s24le)
    if [[ $AUDIO_ONLY -eq 0 ]]; then
      maps=(-map 0:v:0 "${maps[@]}")
      [[ -z "$MUSIC" ]] || maps+=(-map "0:a:${MUSIC}")
      codecs=(-c:v copy -c:a flac)
    fi
    ffmpeg -hide_banner -loglevel error -nostdin -y -i "$IN" "${limit[@]}" \
      -filter_complex "[0:a:${TRACK}]${VOICE},aresample=48000[out]" "${maps[@]}" "${codecs[@]}" "$dest" \
      || { echo "Cleaning failed: $IN" >&2; rm -f "$dest"; return 1; }
    echo "   -> $dest  (voice = track 0${MUSIC:+, music = track 1})"
    return 0
  fi

  # Pass 1: measure
  local log; log=$(mktemp)
  trap 'rm -f "$log"' RETURN
  ffmpeg -hide_banner -nostdin -i "$IN" "${limit[@]}" \
    -filter_complex "$(build_graph "loudnorm=${LN_BASE}:print_format=json")" \
    -map "[out]" -f null - 2>"$log" || { tail -n 15 "$log" >&2; echo "Pass 1 failed: $IN" >&2; return 1; }
  STATS=$(sed -n '/^{/,/^}/p' "$log")
  [[ -n "$STATS" ]] || { echo "Could not measure loudness: $IN" >&2; return 1; }

  local m_i m_tp m_lra m_th m_off
  m_i=$(jval input_i); m_tp=$(jval input_tp); m_lra=$(jval input_lra)
  m_th=$(jval input_thresh); m_off=$(jval target_offset)
  echo "   before normalization: ${m_i} LUFS, ${m_tp} dBTP, LRA ${m_lra}"
  if [[ "$m_i" == "-inf" ]]; then echo "   silent audio, skipping" >&2; return 1; fi

  # Pass 2: render with linear normalization
  local ln="loudnorm=${LN_BASE}:measured_I=${m_i}:measured_TP=${m_tp}:measured_LRA=${m_lra}:measured_thresh=${m_th}:offset=${m_off}:linear=true,aresample=48000"
  local graph; graph="$(build_graph "$ln")"

  if [[ $AUDIO_ONLY -eq 1 ]]; then
    ffmpeg -hide_banner -loglevel error -nostdin -y -i "$IN" "${limit[@]}" \
      -filter_complex "$graph" -map "[out]" -c:a pcm_s24le "$dest" \
      || { echo "Pass 2 failed: $IN" >&2; rm -f "$dest"; return 1; }
  else
    ffmpeg -hide_banner -loglevel error -nostdin -y -i "$IN" "${limit[@]}" \
      -filter_complex "$graph" -map 0:v:0 -map "[out]" \
      -c:v copy -c:a aac -b:a 320k -movflags +faststart "$dest" \
      || { echo "Pass 2 failed: $IN" >&2; rm -f "$dest"; return 1; }
  fi
  echo "   -> $dest"
}

fail=0
for f in "$@"; do process_one "$f" || fail=1; done
exit $fail
