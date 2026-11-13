#!/usr/bin/env python3
"""Word-timestamped transcription of raw talking-head clips (faster-whisper).

Usage:
  transcribe.py MEDIA [MEDIA ...] [--model medium.en] [--out-dir DIR] [--chunk 30] [--overlap 6]
                [--start S --end E]

Writes <out-dir>/<basename>.words.json  ({"segments":[...], "words":[[start,end,word],...]})
and prints a timestamped, attempt-friendly transcript (one line per Whisper segment).

Why chunked: on long raw takes with many retakes and silences, a single Whisper pass
silently skips whole minutes (v4 lost 99s-183s). Chunked mode transcribes overlapping
windows independently and keeps each word only from the window whose core contains it,
so every attempt of every line is seen. Use --chunk 0 for a single pass.
"""
import argparse, json, os, shutil, subprocess, sys
import numpy as np

FFMPEG = os.environ.get("FFMPEG") or shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
SR = 16000


def load16k(path, start=None, end=None):
    cmd = [FFMPEG, "-v", "error"]
    if start is not None:
        cmd += ["-ss", str(start)]
    cmd += ["-i", path]
    if end is not None:
        cmd += ["-t", str(end - (start or 0))]
    cmd += ["-vn", "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).copy()


def run(model, audio, offset=0.0):
    segs, _ = model.transcribe(audio, word_timestamps=True, beam_size=5,
                               condition_on_previous_text=False, vad_filter=False)
    out = []
    for s in segs:
        out.append({"s": s.start + offset, "e": s.end + offset, "t": s.text.strip(),
                    "w": [[w.start + offset, w.end + offset, w.word.strip()] for w in (s.words or [])]})
    return out


def transcribe(model, path, chunk=30.0, overlap=6.0, start=None, end=None):
    audio = load16k(path, start, end)
    base = start or 0.0
    dur = len(audio) / SR
    if chunk <= 0 or dur <= chunk:
        return run(model, audio, base)
    words, t = [], 0.0
    step = chunk - overlap
    while t < dur:
        a, b = t, min(dur, t + chunk)
        core_lo = a + (overlap / 2 if a > 0 else 0)
        core_hi = b - (overlap / 2 if b < dur else 0)
        for s in run(model, audio[int(a * SR):int(b * SR)], a):
            for w in s["w"]:
                mid = (w[0] + w[1]) / 2
                if core_lo <= mid < core_hi:
                    words.append([w[0] + base, w[1] + base, w[2]])
        if b >= dur:
            break
        t += step
    words.sort()
    # regroup into pseudo-segments on gaps >= 0.7s so attempts read as separate lines
    segs, cur = [], []
    for w in words:
        if cur and w[0] - cur[-1][1] >= 0.7:
            segs.append(cur); cur = []
        cur.append(w)
    if cur:
        segs.append(cur)
    return [{"s": g[0][0], "e": g[-1][1], "t": " ".join(x[2] for x in g), "w": g} for g in segs]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("media", nargs="+")
    ap.add_argument("--model", default="medium.en")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--chunk", type=float, default=30.0)
    ap.add_argument("--overlap", type=float, default=6.0)
    ap.add_argument("--start", type=float)
    ap.add_argument("--end", type=float)
    ap.add_argument("--words", action="store_true", help="print word@time lines too")
    a = ap.parse_args()
    from faster_whisper import WhisperModel
    model = WhisperModel(a.model, device="cpu", compute_type="int8")
    os.makedirs(a.out_dir, exist_ok=True)
    for p in a.media:
        segs = transcribe(model, p, a.chunk, a.overlap, a.start, a.end)
        name = os.path.splitext(os.path.basename(p))[0]
        fn = os.path.join(a.out_dir, name + ".words.json")
        json.dump({"source": os.path.abspath(p), "model": a.model, "segments": segs,
                   "words": [w for s in segs for w in s["w"]]}, open(fn, "w"), indent=0)
        print(f"## {p} -> {fn}")
        for s in segs:
            print(f"{s['s']:7.2f}-{s['e']:7.2f}  {s['t']}")
            if a.words:
                print("         " + " ".join(f"{w[2]}@{w[0]:.2f}" for w in s["w"]))
        sys.stdout.flush()


if __name__ == "__main__":
    main()
