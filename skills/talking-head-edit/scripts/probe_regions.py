#!/usr/bin/env python3
"""Transcribe short source windows IN ISOLATION (retake audit helper).

Usage: probe_regions.py SRC.MOV LO-HI [LO-HI ...] [--model medium.en]

Whisper collapses an immediate repeat ("so then we re- so then we
rebuilt it") into one long word when it hears both, which is exactly how v1 shipped a
stitched retake. Transcribing each sub-region on its own (0.5s silence padding) exposes
what is really inside a planned cut. Use it on every sub-cut of a piece and on any gap
or envelope burst you are unsure about. Times printed are source times.
"""
import argparse, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_audio


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("ranges", nargs="+")
    ap.add_argument("--model", default="medium.en")
    a = ap.parse_args()
    from faster_whisper import WhisperModel
    m = WhisperModel(a.model, device="cpu", compute_type="int8")
    x = load_audio(a.src, channels=1, sr=16000)
    pad = np.zeros(8000, np.float32)
    for r in a.ranges:
        lo, hi = map(float, r.split("-"))
        seg = np.concatenate([pad, x[int(lo * 16000):int(hi * 16000)], pad])
        s, _ = m.transcribe(seg, beam_size=5, word_timestamps=True, condition_on_previous_text=False)
        print(f"{lo:.2f}-{hi:.2f} | " + " ".join(f"{w.word.strip()}@{w.start - 0.5 + lo:.2f}" for g in s for w in g.words), flush=True)


if __name__ == "__main__":
    main()
