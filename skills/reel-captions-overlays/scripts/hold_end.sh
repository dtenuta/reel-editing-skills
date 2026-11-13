#!/bin/zsh
# Hold the last frame of a talking-head cut for N extra frames, so an end card (CTA + brand offer) stays up long enough
# to read after the speaker stops talking. The cut itself is untouched: its video and audio packets are stream-copied, and only
# the held tail is encoded (x264 with the source's own settings so the joined stream decodes cleanly) with digital
# silence under it. The music stage then runs the bed under the hold (a sponsor's offer line needed about 3 s).
#   hold_end.sh <cut.mp4> <frames> <out.mp4>
# Pick <frames> so the reel ends just before a music beat (beat k at k * 60 / bpm with cue_at 0), for a clean cut.
set -e
IN=${1:?usage: hold_end.sh <cut.mp4> <frames> <out.mp4>}; IN=${IN:A}
N=${2:?frames}; OUT=${3:?out.mp4}; OUT=${OUT:A}
[[ -e $OUT ]] && { echo "$OUT exists; pick a new name"; exit 2; }
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
FPS=$(ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate -of csv=p=0 "$IN")
TB=$(ffprobe -v error -select_streams v:0 -show_entries stream=time_base -of csv=p=0 "$IN" | cut -d/ -f2)
AR=$(ffprobe -v error -select_streams a:0 -show_entries stream=sample_rate -of csv=p=0 "$IN")
NF=$(ffprobe -v error -select_streams v:0 -count_packets -show_entries stream=nb_read_packets -of csv=p=0 "$IN")
# the last decoded frame, lossless
ffmpeg -v error -sseof -1 -i "$IN" -map 0:v:0 -c:v ffv1 -pix_fmt yuv420p "$T/end.mkv"
LAST=$(( $(ffprobe -v error -count_frames -show_entries stream=nb_read_frames -of csv=p=0 "$T/end.mkv") - 1 ))
DUR=$(python3 -c "f='$FPS'.split('/'); print(f'{$N * int(f[1]) / int(f[0]):.6f}')")
# x264 settings of the source (written in its SEI); fall back to preset slow / CRF 16 (talking-head-edit's export)
X264=$(head -c 4000000 "$IN" | strings -n 20 | grep -m1 -o 'options: .*' | sed 's/^options: //' | tr ' ' '\n' |
  grep -E '^(ref|bframes|b_pyramid|b_adapt|direct|weightp|keyint|keyint_min|subme|trellis|me|rc_lookahead)=' | tr '\n' ':' |
  sed -e 's/b_pyramid=2/b-pyramid=normal/' -e 's/rc_lookahead/rc-lookahead/' -e 's/keyint_min/min-keyint/' -e 's/b_adapt/b-adapt/' -e 's/direct=3/direct=auto/' -e 's/:$//' || true)
ffmpeg -v error -i "$T/end.mkv" -f lavfi -i "anullsrc=r=${AR}:cl=stereo" \
  -filter_complex "[0:v]select=eq(n\,$LAST),setpts=N/($FPS)/TB,tpad=stop_mode=clone:stop=$((N - 1)),fps=${FPS}[v]" \
  -map "[v]" -map 1:a -t "$DUR" -c:v libx264 -preset slow -crf 16 ${X264:+-x264-params} ${X264:+$X264} -profile:v high \
  -pix_fmt yuv420p -color_range tv -colorspace bt709 -color_trc bt709 -color_primaries bt709 \
  -video_track_timescale "$TB" -c:a aac -b:a 192k -ar "$AR" "$T/tail.mp4"
printf "file '%s'\nfile '%s'\n" "$IN" "$T/tail.mp4" > "$T/list.txt"
# Join video and audio separately: a joint concat shifts the whole picture by the AAC priming (21.3 ms, a one-frame lag
# against the captions). The audio's own priming offset (first packet pts, e.g. -0.021333) is put back with -itsoffset,
# so the speech decodes sample-identical to the input.
ffmpeg -v error -f concat -safe 0 -i "$T/list.txt" -map 0:v -c copy "$T/v.mp4"
ffmpeg -v error -f concat -safe 0 -i "$T/list.txt" -map 0:a -c copy "$T/a.m4a"
PRIME=$(ffprobe -v error -select_streams a:0 -show_entries packet=pts_time -of csv=p=0 -read_intervals %+#1 "$IN" | head -1 | tr -d ,)
ffmpeg -v error -i "$T/v.mp4" -itsoffset "${PRIME:-0}" -i "$T/a.m4a" -map 0:v -map 1:a -c copy -movflags +faststart "$OUT"
GOT=$(ffprobe -v error -select_streams v:0 -count_packets -show_entries stream=nb_read_packets -of csv=p=0 "$OUT")
echo "held frame $NF -> $GOT frames ($N held, $DUR s): $OUT"
[[ $GOT == $((NF + N)) ]] || { echo "FAIL: expected $((NF + N)) frames"; exit 1; }
VS=$(ffprobe -v error -select_streams v:0 -show_entries stream=start_time -of csv=p=0 "$OUT")
[[ $VS == $(ffprobe -v error -select_streams v:0 -show_entries stream=start_time -of csv=p=0 "$IN") ]] || { echo "FAIL: video start moved to $VS"; exit 1; }
# the speech must decode sample-identical up to the old end
NB=$(ffmpeg -v error -i "$IN" -map 0:a -f s16le - | wc -c | tr -d ' ')
A=$(ffmpeg -v error -i "$IN" -map 0:a -f s16le - | md5)
B=$(ffmpeg -v quiet -i "$OUT" -map 0:a -f s16le - 2>/dev/null | head -c "$NB" | md5)
[[ $A == $B ]] && echo "speech: sample-identical to the input" || { echo "FAIL: speech differs from the input"; exit 1; }
