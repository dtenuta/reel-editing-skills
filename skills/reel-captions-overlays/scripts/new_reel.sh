#!/bin/zsh
# Set up a reel for captions + overlays in BOTH tools from one source video.
#   new_reel.sh <input.mp4> <workdir> [overlays.json]
# 1st run (no overlays.json): transcribes, detects faces + shot cuts, writes <workdir>/overlays.json from the
#   example (plus music_plan.json) and stops so you can fill in corrections / overlays / the tone map. 2nd run (with overlays.json): builds edit.json
#   and two ready-to-render projects: <workdir>/remotion and <workdir>/hyperframes.
set -e
SK=${0:A:h:h}
PY=${PY:-$("$SK/scripts/find_python.sh")}   # has faster-whisper + opencv ($REELKIT_PYTHON, repo .venv, or python3)
IN=${1:A}; WD=${2:?usage: new_reel.sh <input.mp4> <workdir> [overlays.json]}; OV=${3:-}
mkdir -p "$WD"; WD=${WD:A}; cd "$WD"
NAME=${IN:t}
FRAMES=$(ffprobe -v error -select_streams v:0 -count_packets -show_entries stream=nb_read_packets -of csv=p=0 "$IN")
FPS=$(ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate -of csv=p=0 "$IN" | awk -F/ '{printf "%d", $1/$2}')
[[ -f words_raw.json ]] || $PY -I "$SK/scripts/transcribe.py" "$IN" words_raw.json
[[ -f faces_src.json ]] || $PY -I "$SK/scripts/faces.py" "$IN" faces_src.json
if [[ ! -f cuts.txt ]]; then
  ffmpeg -hide_banner -i "$IN" -vf "scale=135:240,select='gt(scene\,0.08)',showinfo" -an -f null - 2>&1 \
    | grep -o "pts_time:[0-9.]*" | cut -d: -f2 | paste -sd, - > cuts.txt
fi
if [[ -z "$OV" ]]; then
  [[ -f overlays.json ]] || sed -E "s/\"source\": \"[^\"]*\"/\"source\": \"$NAME\"/" "$SK/overlays.example.json" > overlays.json
  [[ -f music_plan.json ]] || cp "$SK/music_plan.example.json" music_plan.json
  echo "Transcript: $WD/words_raw.json   shot cuts: $(cat cuts.txt)"
  echo "Now edit $WD/overlays.json (corrections, sansWindows = B-roll/takeover spans, overlays) and re-run:"
  echo "  $0 $IN $WD $WD/overlays.json"; exit 0
fi
$PY -I "$SK/scripts/build_edit.py" words_raw.json "$OV" edit.json --frames "$FRAMES" --fps "$FPS" \
  --faces faces_src.json --cuts "$(cat cuts.txt)" --video "$IN"
# Remotion project
mkdir -p remotion/public/fonts && rsync -a "$SK/templates/remotion/" remotion/ && cp "$SK/templates/fonts/"* remotion/public/fonts/
cp "$SK/templates/style.css" remotion/src/style.css && cp edit.json remotion/src/edit.json
[[ -f remotion/public/$NAME ]] || cp -c "$IN" "remotion/public/$NAME"
(cd remotion && [[ -d node_modules ]] || npm ci --ignore-scripts --no-audit --no-fund)
# HyperFrames project
mkdir -p hyperframes/assets/fonts && rsync -a "$SK/templates/hyperframes/" hyperframes/ && cp "$SK/templates/fonts/"* hyperframes/assets/fonts/
cp "$SK/templates/style.css" hyperframes/assets/style.css && cp edit.json hyperframes/edit.json
[[ -f hyperframes/assets/$NAME ]] || cp -c "$IN" "hyperframes/assets/$NAME"
(cd hyperframes && node scripts/gen.mjs && npx --yes hyperframes@0.8.214 lint | tail -2)
echo "Ready. Render (needs Chrome outside any sandbox):"
echo "  $WD/hyperframes/render.sh <out_hyperframes.mp4>   # default tool"
echo "  $WD/remotion/render.sh <out_remotion.mp4>"
echo "Verify: $PY -I $SK/scripts/verify.py $IN <out.mp4> $WD/edit.json $WD/faces_src.json $WD/qc <label>"
echo "Music + SFX (default; fill in $WD/music_plan.json first, or pass --no-music to skip):"
echo "  $SK/scripts/music_stage.sh <out_hyperframes.mp4> $WD <out>_music.mp4"
