#!/bin/zsh
# Re-render the Remotion version: ./render.sh [output.mp4]
set -e
cd "$(dirname "$0")"
OUT="${1:?usage: ./render.sh <output.mp4>}"
START=$(date +%s)
# Chrome-side capture on macOS returns ~0.5-2% of 4K frames smeared or with black tiles at ANY concurrency,
# GL backend or OffthreadVideo setting (tested: 5x=43 bad, 2x=12, angle=10). concurrency=2 keeps the count low;
# repair_remotion.py then re-renders each bad frame as a still and splices it in (verify.py: corrupt_frames=0).
npx remotion render Reel out/raw.mp4 --scale=2 --concurrency=2 --codec=h264 --crf=16 --pixel-format=yuv420p \
  --audio-codec=aac --audio-bitrate=192k
# faststart (moov up front) without re-encoding
ffmpeg -v error -y -i out/raw.mp4 -c copy -movflags +faststart "$OUT"
SRC="public/$(python3 -c 'import json;print(json.load(open("src/edit.json"))["source"])')"
SK="${REEL_SKILL:-$HOME/.claude/skills/reel-captions-overlays}"
PY="${PY:-$("$SK/scripts/find_python.sh")}"   # opencv + numpy
"$PY" -I "$SK/scripts/repair_remotion.py" "$SRC" "$OUT" src/edit.json .
echo "render seconds: $(( $(date +%s) - START ))"
