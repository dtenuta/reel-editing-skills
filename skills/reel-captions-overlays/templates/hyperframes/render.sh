#!/bin/zsh
# Re-render the HyperFrames version: ./render.sh [output.mp4]
# (re-run `node scripts/gen.mjs` first if edit.json changed)
set -e
cd "$(dirname "$0")"
OUT="${1:?usage: ./render.sh <output.mp4>}"
START=$(date +%s)
node scripts/gen.mjs
# Stall timeouts raised (once, on a loaded Mac in Low Power Mode FFmpeg was killed after 600 s with no new frame,
# after every frame had been captured, and a later run died on the 60 s capture-stall limit). They only matter when
# something stalls; see SKILL.md "Slow machine".
export FFMPEG_ENCODE_TIMEOUT_MS=${FFMPEG_ENCODE_TIMEOUT_MS:-5400000} HF_DE_STALL_MS=${HF_DE_STALL_MS:-1800000}
npx --yes hyperframes@0.8.214 render --resolution portrait-4k --crf 16 --fps 30 --video-frame-format png \
  --player-ready-timeout 600000 --protocol-timeout 1800000 --browser-timeout 900 -o renders/raw.mp4
# make sure moov is up front (no re-encode)
ffmpeg -v error -y -i renders/raw.mp4 -c copy -movflags +faststart "$OUT"
echo "render seconds: $(( $(date +%s) - START ))"
