#!/bin/zsh
# Music + SFX stage (default after render). Mixes a tone-mapped, beat-aware, ducked music bed and graphic SFX
# under the rendered reel's speech, muxes it onto the picture with a video stream copy, makes the phone copy
# and verifies loudness, clipping, speech-unchanged and intelligibility.
#   music_stage.sh <rendered.mp4> <workdir> <out.mp4> [--no-music] [--no-whisper]
# Needs <workdir>/music_plan.json (start from $SK/music_plan.example.json), <workdir>/words_raw.json and
# <workdir>/edit.json. --no-music (or MUSIC=0) skips the stage and leaves the render as is.
set -e
SK=${0:A:h:h}
AUDIO_PY=${AUDIO_PY:-$HOME/.cache/reel-audio/venv/bin/python}   # numpy scipy librosa matplotlib (setup_audio.sh)
PY=${PY:-$("$SK/scripts/find_python.sh")}  # faster-whisper, for the WER check
IN=${1:?usage: music_stage.sh <rendered.mp4> <workdir> <out.mp4> [--no-music] [--no-whisper]}; IN=${IN:A}
WD=${2:?workdir}; WD=${WD:A}; OUT=${3:?out.mp4}; OUT=${OUT:A}
NOMUSIC=${MUSIC:-1}; WHISPER=1
for a in "${@:4}"; do
  [[ $a == --no-music ]] && NOMUSIC=0
  [[ $a == --no-whisper ]] && WHISPER=0
done
if [[ $NOMUSIC == 0 ]]; then echo "music stage skipped (--no-music)"; exit 0; fi
[[ -x $AUDIO_PY ]] || "$SK/scripts/setup_audio.sh"
PLAN=$WD/music_plan.json
[[ -f $PLAN ]] || { echo "no $PLAN; copy $SK/music_plan.example.json there and fill in the tone map"; exit 2; }
[[ -e $OUT && -z $FORCE ]] && { echo "$OUT exists; pick a new name (FORCE=1 to overwrite)"; exit 2; }
ST=$WD/music_out; mkdir -p "$ST"
$AUDIO_PY -I "$SK/scripts/music_mix.py" "$PLAN" --reel "$IN" --words "$WD/words_raw.json" --edit "$WD/edit.json" --out "$ST"
# picture untouched (stream copy); new AAC audio trimmed to the video's duration (same length as the input); moov up front
VDUR=$(ffprobe -v error -select_streams v:0 -show_entries stream=duration -of csv=p=0 "$IN")
ffmpeg -v error -y -i "$IN" -i "$ST/mix.wav" -map 0:v:0 -map 1:a:0 -c:v copy -af "atrim=end=$VDUR" -c:a aac -b:a 256k \
  -ar 48000 -movflags +faststart "$OUT"
# phone copy: re-encode the picture only and COPY the mastered AAC. Re-encoding the audio a second time at 128k
# lifted true peak from -1.3 to -0.7 dBTP on one reel, so the phone file broke the -1 dBTP ceiling.
MOB=${MOBILE_OUT:-${OUT:r}_mobile.mp4}
ffmpeg -v error -y -i "$OUT" -vf scale=720:1280:flags=lanczos -c:v libx264 -preset slow -crf 22 -maxrate 2800k \
  -bufsize 5600k -pix_fmt yuv420p -c:a copy -movflags +faststart "$MOB"
for f in "$OUT" "$MOB"; do
  TPK=$(ffmpeg -nostats -hide_banner -i "$f" -map 0:a -af ebur128=peak=true -f null - 2>&1 | sed -n '/True peak/,$p' | awk '/Peak:/{print $2; exit}')
  [[ -n $TPK ]] || { echo "FAIL: could not read the true peak of ${f:t}"; exit 3; }
  echo "true peak ${f:t}: $TPK dBTP"
  (( TPK > -1.0 )) && { echo "FAIL: ${f:t} true peak $TPK dBTP is over -1 dBTP"; exit 3; }
done
WARGS=(); [[ $WHISPER == 1 ]] && WARGS=(--whisper-py "$PY")
$AUDIO_PY -I "$SK/scripts/music_verify.py" --ref "$IN" --out "$OUT" --stems "$ST" --words "$WD/words_raw.json" \
  --qc "$WD/qc" --label music "${WARGS[@]}"
echo "final: $OUT   phone: $MOB ($(du -m "$MOB" | cut -f1) MB)   sections image: $WD/qc/music_sections.png"
