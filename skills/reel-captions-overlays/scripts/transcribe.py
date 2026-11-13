"""Word-level transcript of a reel with faster-whisper large-v3 (CPU, int8; the model downloads on first use).
Usage: python3 transcribe.py <video> <words_raw.json>   writes {"lang", "words": [{"w", "s", "e", "p"}]}
"""
import json, sys
if len(sys.argv) < 3 or sys.argv[1] in ("-h", "--help"):
    print(__doc__.strip()); sys.exit(0 if sys.argv[1:2] in (["-h"], ["--help"]) else 2)
from faster_whisper import WhisperModel
m = WhisperModel("large-v3", device="cpu", compute_type="int8")
segs, info = m.transcribe(sys.argv[1], word_timestamps=True, vad_filter=False, language=None, beam_size=5)
out = []
for s in segs:
    for w in s.words:
        out.append({"w": w.word.strip(), "s": round(w.start,3), "e": round(w.end,3), "p": round(w.probability,3)})
    print(f"[{s.start:6.2f}-{s.end:6.2f}] {s.text}", flush=True)
json.dump({"lang": info.language, "words": out}, open(sys.argv[2], "w"), indent=1)
