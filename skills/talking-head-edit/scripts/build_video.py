#!/usr/bin/env python3
"""Build the final video from cuts.json + normalized audio, alternating punch-ins.

Usage: build_video.py cuts.json --audio DIR/audio_norm.wav --out OUT.mp4 [--work DIR]

- Each cut is rendered frame-exact (input -ss, -frames:v round(dur*fps)) to a near-lossless
  intermediate, at the piece's zoom (1.0 = full frame scaled to WxH; >1 = center crop around
  settings.zoom_center then lanczos upscale). ffmpeg autorotate applies the source rotation
  matrix (iPhone portrait clips are stored 3840x2160 with rotation -90); probe() checks the
  rotated size matches the output aspect so a wrong-orientation source fails loudly.
- Intermediates are content-addressed (source, start, frames, filter) so a rerun after a
  plan tweak only re-renders the changed cuts.
- Intermediates are concatenated and encoded once: libx264 crf (default 16), yuv420p,
  AAC 192k, +faststart, no captions.
"""
import argparse, hashlib, json, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import FFMPEG, probe


def crop_filter(p, s, e, W, H):
    """Face-centered crop window (reframe.py) -> lanczos to WxH. The window is stored in output
    pixels and applied in SOURCE pixels (crop first, one resample), so a 1080x1920 test render of a
    4K source keeps the same detail ratio as the 4K master. A tracked (handheld) piece pans
    piecewise-linearly between its smoothed keys; t is time since the cut start."""
    x, y, w, h = p["crop"]
    tr = [k for k in p.get("track") or []]
    if tr:
        keys = [(k[0] - s, k[1], k[2]) for k in tr]
        def expr(i):
            ks = [k for k in keys if -1.0 <= k[0] <= (e - s) + 1.0] or keys
            if len(ks) == 1:
                return str(ks[0][i])
            out = str(ks[-1][i])
            for (t0, *v0), (t1, *v1) in reversed(list(zip(ks, ks[1:]))):
                a, b = v0[i - 1], v1[i - 1]
                out = f"if(lt(t\\,{t1:.3f})\\,{a}+({b - a})*clip((t-{t0:.3f})/{t1 - t0:.3f}\\,0\\,1)\\,{out})"
            return out
        xs, ys = expr(1), expr(2)
        return f"crop=iw*{w}/{W}:ih*{h}/{H}:iw*({xs})/{W}:ih*({ys})/{H},scale={W}:{H}:flags=lanczos"
    return f"crop=iw*{w}/{W}:ih*{h}/{H}:iw*{x}/{W}:ih*{y}/{H},scale={W}:{H}:flags=lanczos"


def render_insert(p, work, lst, W, H, fps, n):
    """Insert piece: picture = B-roll (p["video"] from p["video_start"]) for exactly the insert's
    audio duration, scaled to cover WxH (landscape B-roll is center-cropped at video_x, default 0.5),
    optional extra video_zoom. The audio comes from the insert source via assemble_audio."""
    vz = float(p.get("video_zoom", 1.0)); vx = float(p.get("video_x", 0.5))
    W2, H2 = round(W * vz / 2) * 2, round(H * vz / 2) * 2
    vf = (f"scale={W2}:{H2}:force_original_aspect_ratio=increase:flags=lanczos,"
          f"crop={W}:{H}:(iw-{W})*{vx}:(ih-{H})/2")
    t0 = float(p.get("video_start", 0.0)); c0 = p["cuts"][0][0]; k = 0
    for s, e in p["cuts"]:
        fr = round((e - s) * fps); ss = t0 + (s - c0)
        key = hashlib.sha1(f"{p['video']}|{ss:.4f}|{fr}|{vf}|{fps}".encode()).hexdigest()[:12]
        fn = f"c_{key}.mp4"; k += 1
        lst.write(f"file '{fn}'\n")
        if not os.path.exists(os.path.join(work, fn)):
            subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", f"{ss:.4f}", "-i", p["video"], "-frames:v", str(fr),
                            "-an", "-vf", vf + f",setsar=1,fps={fps}", "-c:v", "libx264", "-crf", "10", "-preset",
                            "veryfast", "-pix_fmt", "yuv420p", os.path.join(work, fn + ".tmp.mp4")], check=True)
            os.replace(os.path.join(work, fn + ".tmp.mp4"), os.path.join(work, fn))
    return k


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cuts")
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", default="vwork")
    a = ap.parse_args()
    C = json.load(open(a.cuts)); S = C["settings"]
    W, H, fps = S["width"], S["height"], S["fps"]
    os.makedirs(a.work, exist_ok=True)
    for k, path in C["sources"].items():
        if not any(p["src"] == k for p in C["pieces"]) or k in S.get("insert_sources", []):
            continue
        info = probe(path)
        print(f"source {k}: {path} rotated size {info['width']}x{info['height']} rot={info['rotation']} fps={info['fps']}")
        if (info["width"] > info["height"]) != (W > H):
            raise SystemExit(f"orientation mismatch for {path}: {info} vs output {W}x{H}. "
                             "Check rotation metadata; add a transpose filter if the clip is untagged.")
    if S.get("reframe", True) and any("crop" not in p for p in C["pieces"] if not p.get("insert")):
        from reframe import annotate
        annotate(C)
        json.dump(C, open(a.cuts, "w"), indent=1)
        print("reframe crops written to", a.cuts)
    lst = open(os.path.join(a.work, "list.txt"), "w"); n = 0; total = 0
    cx, cy = S["zoom_center"]
    for pi, p in enumerate(C["pieces"]):
        z = float(p["zoom"])
        if p.get("insert"):
            n += render_insert(p, a.work, lst, W, H, fps, n)
            total += sum(round((e - s) * fps) for s, e in p["cuts"])
            print(f"piece {pi:02d} {p['label']:>10} INSERT B-roll {p['video']} @ {p.get('video_start', 0)}s")
            continue
        if S.get("reframe", True) and "crop" in p:
            vf = None  # per cut below (a tracked piece pans)
        elif abs(z - 1.0) < 1e-6:
            vf = f"scale={W}:{H}:flags=lanczos"
        else:
            w, h = round(W / z / 2) * 2, round(H / z / 2) * 2
            x = max(0, min(W - w, int(cx * W - w / 2))); y = max(0, min(H - h, int(cy * H - h / 2)))
            vf = f"scale={W}:{H}:flags=lanczos,crop={w}:{h}:{x}:{y},scale={W}:{H}:flags=lanczos"
        for s, e in p["cuts"]:
            fr = round((e - s) * fps); total += fr
            if vf is None or "crop" in p and S.get("reframe", True):
                vf = crop_filter(p, s, e, W, H)
            key = hashlib.sha1(f"{C['sources'][p['src']]}|{s:.4f}|{fr}|{vf}|{fps}".encode()).hexdigest()[:12]
            fn = f"c_{key}.mp4"  # content-addressed: survives an insert shifting the order
            lst.write(f"file '{fn}'\n"); n += 1
            if os.path.exists(os.path.join(a.work, fn)):
                continue  # identical intermediate from a previous run: reuse
            subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", f"{s:.4f}", "-i", C["sources"][p["src"]],
                            "-frames:v", str(fr), "-an", "-vf", vf + f",setsar=1,fps={fps}",
                            "-c:v", "libx264", "-crf", "10", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                            os.path.join(a.work, fn + ".tmp.mp4")], check=True)
            os.replace(os.path.join(a.work, fn + ".tmp.mp4"), os.path.join(a.work, fn))
        print(f"piece {pi:02d} {p['label']:>10} zoom {z} cuts {len(p['cuts'])}")
    lst.close()
    print(f"video frames {total} = {total/fps:.3f}s")
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", os.path.join(a.work, "list.txt"),
                    "-i", a.audio, "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-crf", str(S["crf"]),
                    "-preset", S["preset"], "-pix_fmt", "yuv420p", "-r", str(fps), "-c:a", "aac", "-b:a", S["abitrate"],
                    "-movflags", "+faststart", "-shortest", a.out], check=True)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
