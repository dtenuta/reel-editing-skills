"""Repair sporadic corrupt frames in a Remotion 4K render.

On macOS (Remotion 4.0.539, chrome-for-testing, --scale=2) roughly 0.5-2% of frames come back smeared
(one pixel row stretched over the frame) or with black tiles, whatever the concurrency, GL backend or
OffthreadVideo settings. Frames are deterministic, so: find them, re-render each as a still, check the
still is clean, overlay it on exactly that frame and re-encode (CRF 16, audio copied, faststart).

Usage: python3 repair_remotion.py <source.mp4> <render.mp4> <edit.json> <remotion_project_dir>
Rewrites <render.mp4> in place when frames were repaired. Run outside the Bash sandbox (Chrome).
"""
import json, os, subprocess, sys, tempfile
import numpy as np

if len(sys.argv) < 5 or sys.argv[1] in ("-h", "--help"):
    print(__doc__.strip()); sys.exit(0 if sys.argv[1:2] in (["-h"], ["--help"]) else 2)
src, out, editp, proj = sys.argv[1:5]
edit = json.load(open(editp))
tk = [(o["from"], o["to"]) for o in edit["overlays"] if o["type"] == "takeover"]
W, H = 90, 160

def grey(p):
    pr = subprocess.Popen(["ffmpeg", "-v", "error", "-i", p, "-vf", f"scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                          stdout=subprocess.PIPE)
    while True:
        b = pr.stdout.read(W * H)
        if len(b) < W * H: break
        yield np.frombuffer(b, np.uint8).astype(np.int16).reshape(H, W)

def bad_score(a, b):  # middle band 30-55% height: no cards, no captions
    return float(np.abs(a[48:88] - b[48:88]).mean())

src_frames = list(grey(src))
bad = [i for i, b in enumerate(grey(out)) if i < len(src_frames) and not any(f <= i < t for f, t in tk)
       and bad_score(src_frames[i], b) > 12]
print("corrupt frames:", bad)
if not bad: sys.exit(0)

tmp = tempfile.mkdtemp(prefix="remotion_fix_")
fixes = []
for n in bad:
    png = f"{tmp}/f{n}.png"
    for attempt in range(4):
        subprocess.run(["npx", "remotion", "still", "Reel", png, f"--frame={n}", "--scale=2"], cwd=proj, check=True,
                       stdout=subprocess.DEVNULL)
        g = subprocess.run(["ffmpeg", "-v", "error", "-i", png, "-vf", f"scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                           capture_output=True).stdout
        if bad_score(src_frames[n], np.frombuffer(g, np.uint8).astype(np.int16).reshape(H, W)) <= 12: break
    else:
        sys.exit(f"frame {n}: still is corrupt after 4 attempts")
    fixes.append((n, png))

inputs, chain, last = ["-i", out], [], "0:v"
for k, (n, png) in enumerate(fixes, start=1):
    inputs += ["-i", png]
    chain.append(f"[{last}][{k}:v]overlay=0:0:enable='eq(n,{n})'[v{k}]")
    last = f"v{k}"
fixed = out + ".fixed.mp4"
subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(chain), "-map", f"[{last}]", "-map", "0:a?",
                "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "copy",
                "-movflags", "+faststart", fixed], check=True)
os.replace(fixed, out)
print(f"repaired {len(fixes)} frames -> {out}")
