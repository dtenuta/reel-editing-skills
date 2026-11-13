#!/usr/bin/env python3
"""Speaker check for two-camera interviews: who is actually talking in each span?

usage: mouth.py CAM1.mp4 CAM2.mp4 a-b [a-b ...] [--strip OUTDIR] [--names A,D]

Both files must share one timeline (synced camera files, same source seconds). Per span it prints:
  - mouth activity for each camera: mean abs frame diff inside a YuNet mouth box (re-detected every
    frame), the same divided by the upper-face diff (head motion cancels), and "art", the frame-to-frame
    change of mouth openness (dark gap + bright teeth share), which tracks articulation best;
  - the voice pitch of the span (median F0, YIN on the shared audio). Two-camera shoots usually carry ONE
    shared audio track, so per-camera loudness says nothing; pitch separates a low and a high voice
    (e.g. host ~80-120 Hz, guest ~170-240 Hz). YIN can halve a high voice, so read p25/p75 too;
  - a verdict from "art": the camera whose articulation is 1.5x the other's is talking.
--strip writes a JPEG per span (top row camera 1 mouth crops, bottom row camera 2, 6 fps) to eyeball.
Short spans (< 0.6 s, e.g. "Did they?") and smiles/listening nods often read "both/unclear": decide those
with the strip, the pitch, the isolated Whisper read (probe_regions.py) and the conversation context.
"""
import argparse, os, subprocess, sys
import numpy as np
import cv2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import FFMPEG, YUNET
W, H, FPS = 540, 960, 15


def frames(src, a, b):
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{a:.3f}", "-i", src, "-t", f"{b - a:.3f}",
                          "-vf", f"fps={FPS},scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                         capture_output=True, check=True).stdout
    n = len(raw) // (W * H * 3)
    return np.frombuffer(raw[: n * W * H * 3], np.uint8).reshape(n, H, W, 3)


def audio(src, a, b, sr=16000):
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{a:.3f}", "-i", src, "-t", f"{b - a:.3f}",
                          "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).astype(np.float64)


def pitch(x, sr=16000, win=640, hop=160, fmin=70, fmax=400, thr=0.25):
    """Median / p25 / p75 F0 (Hz) of the voiced frames (YIN cumulative-mean-normalised difference)."""
    lo, hi = int(sr / fmax), int(sr / fmin)
    floor = max(0.002, 0.25 * float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.002)
    out = []
    for i in range(0, len(x) - win - hi, hop):
        f = x[i:i + win + hi]
        if np.sqrt(np.mean(f[:win] ** 2)) < floor:
            continue
        d = np.array([np.sum((f[:win] - f[t:t + win]) ** 2) for t in range(1, hi + 1)])
        cm = d * np.arange(1, hi + 1) / np.maximum(np.cumsum(d), 1e-12)
        c = np.where(cm[lo - 1:] < thr)[0]
        if len(c):
            t = c[0] + lo - 1
            while t + 1 < len(cm) and cm[t + 1] < cm[t]:
                t += 1
            out.append(sr / (t + 1))
    if not out:
        return None
    v = np.array(out)
    return float(np.median(v)), float(np.percentile(v, 25)), float(np.percentile(v, 75)), len(v)


def boxes(fr, det):
    out, last = [], None
    for f in fr:
        _, faces = det.detect(f)
        if faces is not None and len(faces):
            last = max(faces, key=lambda q: q[2] * q[3])
        out.append(last)
    return out


def roi(r, kind):
    x, y, bw, bh = r[:4]
    if kind == "mouth":
        mx = (r[10] + r[12]) / 2; my = (r[11] + r[13]) / 2
        hw = 0.30 * bw
        return int(mx - hw), int(my - 0.14 * bh), int(mx + hw), int(my + 0.20 * bh)
    return int(x + 0.15 * bw), int(y + 0.10 * bh), int(x + 0.85 * bw), int(y + 0.45 * bh)  # eyes/brow band


def clip(b):
    x0, y0, x1, y1 = b
    return max(0, x0), max(0, y0), min(W, x1), min(H, y1)


def activity(fr, bx):
    g = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32) for f in fr]
    m, u = [], []
    for i in range(1, len(g)):
        r = bx[i] if bx[i] is not None else bx[i - 1]
        if r is None:
            continue
        for kind, acc in (("mouth", m), ("upper", u)):
            x0, y0, x1, y1 = clip(roi(r, kind))
            if x1 - x0 >= 4 and y1 - y0 >= 4:
                acc.append(float(np.mean(np.abs(g[i][y0:y1, x0:x1] - g[i - 1][y0:y1, x0:x1]))))
    if not m:
        return None, None, None
    op = []
    for i in range(len(g)):
        if bx[i] is None:
            continue
        x0, y0, x1, y1 = clip(roi(bx[i], "mouth"))
        z = g[i][y0:y1, x0:x1]
        if z.size >= 16:
            med = float(np.median(z)) or 1.0
            op.append(float(np.mean(z < 0.6 * med) + np.mean(z > 1.4 * med)))
    art = float(np.mean(np.abs(np.diff(op)))) * 100 if len(op) > 2 else None
    return float(np.mean(m)), float(np.mean(m)) / max(0.5, float(np.mean(u)) if u else 1.0), art


def strip(fr, bx, every=FPS // 6, size=(96, 64)):
    tiles = []
    for i in range(0, len(fr), every):
        if bx[i] is None:
            tiles.append(np.zeros((size[1], size[0], 3), np.uint8)); continue
        x0, y0, x1, y1 = clip(roi(bx[i], "mouth"))
        tiles.append(cv2.resize(fr[i][y0:y1, x0:x1], size))
    return np.hstack(tiles) if tiles else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cam1"); ap.add_argument("cam2"); ap.add_argument("spans", nargs="+")
    ap.add_argument("--strip"); ap.add_argument("--names", default="A,D")
    a = ap.parse_args()
    n1, n2 = a.names.split(",")
    if not os.path.isfile(YUNET):
        sys.exit(f"YuNet model not found at {YUNET}: set YUNET_MODEL or REELKIT_HOME (see README)")
    det = cv2.FaceDetectorYN_create(YUNET, "", (W, H), score_threshold=0.6)
    if a.strip:
        os.makedirs(a.strip, exist_ok=True)
    for sp in a.spans:
        s, e = map(float, sp.split("-"))
        res, rows = {}, []
        for k, src in ((n1, a.cam1), (n2, a.cam2)):
            fr = frames(src, s, e); bx = boxes(fr, det)
            res[k] = activity(fr, bx)
            if a.strip:
                rows.append(strip(fr, bx))
        r1, r2 = res[n1][2], res[n2][2]
        who = "?" if r1 is None or r2 is None else (n1 if r1 > 1.5 * r2 else n2 if r2 > 1.5 * r1 else "both/unclear")
        f0 = pitch(audio(a.cam1, s, e))
        fmt = lambda v: "none" if v[0] is None else f"diff {v[0]:.1f} norm {v[1]:.2f} art {v[2]:.2f}"
        ps = "F0 none" if f0 is None else f"F0 {f0[0]:.0f} Hz (p25 {f0[1]:.0f} p75 {f0[2]:.0f}, n {f0[3]})"
        print(f"{sp:>17} | {n1} {fmt(res[n1])} | {n2} {fmt(res[n2])} | {ps} | talking: {who}", flush=True)
        if a.strip and all(r is not None for r in rows):
            wmax = max(r.shape[1] for r in rows)
            rows = [np.pad(r, ((0, 0), (0, wmax - r.shape[1]), (0, 0))) for r in rows]
            img = np.vstack([rows[0], np.full((4, wmax, 3), 255, np.uint8), rows[1]])
            cv2.imwrite(os.path.join(a.strip, f"mouth_{sp}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 80])


if __name__ == "__main__":
    main()
