#!/usr/bin/env python3
"""Prepare a one-take emotion-arc demo clip (e.g. sad -> excited -> sad -> excited) for a demo insert.

    emotion_ride.py GEN.wav OUT.wav --labels "sad,excited!,sad,excited!" --gaps "1.55-2.15,3.2-3.85,4.5-4.95"
                    [--quiet sad] [--rel -4.5] [--room ROOM.wav --bedded OUT_bedded.wav]

Why: TTS emotion models render sad / calm / deflated parts 13-20 dB under the excited parts. On a phone speaker next
to the speaker's own voice those parts vanish (one test: the calm half sat 6.7 dB under the speech). A "[normal speaking
volume]" direction flattens the emotion instead, so the level is fixed here, in the clip:

  * the take is split at the quietest 10 ms point inside each --gaps window (the silent pauses between phrases; read
    them off a word-level transcript of the take);
  * each segment's active level is measured (frames within 20 dB of its loudest);
  * a segment whose label contains a --quiet word (default "sad", "calm") gets ONE static gain that puts it --rel dB
    (default -4.5) under the mean of the other segments; loud segments are only ever turned down, never up;
  * gains change on 40 ms dB ramps centred on the gap minimum (silence, so inaudible). No compression, no pitch or
    speed change, timbre untouched;
  * then trim at -45 dB, 5 ms / 30 ms fades, soxr resample to 48 kHz, peak capped at -1 dBFS.

Writes OUT.wav (ridden), OUT_unridden.wav (same trim, no riding, for comparison), optional OUT_bedded (the ridden clip
on a crossfade-looped room tone, for the talking-head insert) and OUT.json (segment times inside the trimmed clip,
raw level and gain per segment). Label the segments on the promptDemo card from the reel transcript, and
reel-captions-overlays music_verify.py checks each labelled segment sits within 6 LU of the normal speech.
"""
import argparse, json, os
import numpy as np, soundfile as sf, librosa

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("gen"); ap.add_argument("out")
ap.add_argument("--labels", required=True, help="comma-separated segment labels, in order")
ap.add_argument("--gaps", required=True, help="comma-separated a-b windows (s) holding the pause between segments")
ap.add_argument("--quiet", default="sad,calm", help="label words that mark a segment to ride up (default sad,calm)")
ap.add_argument("--rel", type=float, default=-4.5, help="quiet segments end this many dB under the loud mean")
ap.add_argument("--room"); ap.add_argument("--bedded")
a = ap.parse_args()

labels = [s.strip() for s in a.labels.split(",")]
gaps = [tuple(float(v) for v in g.split("-")) for g in a.gaps.split(",")]
assert len(gaps) == len(labels) - 1, "need one gap window between each pair of segments"
quiet_words = [w.strip().lower() for w in a.quiet.split(",") if w.strip()]
is_quiet = [any(w in l.lower() for w in quiet_words) for l in labels]
assert any(is_quiet) and not all(is_quiet), "need at least one quiet and one loud segment"

x, sr = sf.read(a.gen, dtype="float64")
if x.ndim > 1:
    x = x.mean(1)
h = int(0.01 * sr)
env = np.sqrt(np.convolve(x ** 2, np.ones(h) / h, "same"))
cuts = [int(g0 * sr) + int(np.argmin(env[int(g0 * sr):int(g1 * sr)])) for g0, g1 in gaps]
bounds = [0] + cuts + [len(x)]


def active_db(y):
    e = np.sqrt(np.convolve(y ** 2, np.ones(441) / 441, "same"))
    m = e > e.max() * 10 ** (-20 / 20)
    return 20 * np.log10(np.sqrt((y[m] ** 2).mean()) + 1e-12)


n = len(labels)
lv = [active_db(x[bounds[i]:bounds[i + 1]]) for i in range(n)]
ref = 10 * np.log10(np.mean([10 ** (lv[i] / 10) for i in range(n) if not is_quiet[i]]))
gains = [(ref + a.rel - lv[i]) if is_quiet[i] else min(0.0, ref - lv[i]) for i in range(n)]
curve = np.zeros(len(x)); r = int(0.04 * sr)
for i in range(n):
    curve[bounds[i]:bounds[i + 1]] = gains[i]
for c in cuts:
    s0, s1 = c - r // 2, c + r // 2
    curve[s0:s1] = np.linspace(curve[s0 - 1], curve[s1], s1 - s0)
y = x * 10 ** (curve / 20)


def trim(z, db=-45, lead=0.03, tail=0.08):
    e = librosa.feature.rms(y=z, frame_length=1024, hop_length=256)[0]; d = 20 * np.log10(e + 1e-9)
    idx = np.where(d > db)[0]
    s = max(0, idx[0] * 256 - int(lead * sr)); t = min(len(z), idx[-1] * 256 + 1024 + int(tail * sr))
    return z[s:t], s


def fade(z, fi, fo):
    z = z.copy(); i, o = int(fi * sr), int(fo * sr)
    z[:i] *= np.sin(np.linspace(0, np.pi / 2, i)) ** 2; z[-o:] *= np.cos(np.linspace(0, np.pi / 2, o)) ** 2
    return z


root, _ = os.path.splitext(a.out)
res = {}
for path, z in ((a.out, y), (root + "_unridden.wav", x)):
    zt, s0 = trim(z); zt = fade(zt, 0.005, 0.03)
    zz = librosa.resample(zt, orig_sr=sr, target_sr=48000, res_type="soxr_vhq")
    pk = np.abs(zz).max()
    if pk > 10 ** (-1 / 20):
        zz *= 10 ** (-1 / 20) / pk
    sf.write(path, zz.astype(np.float32), 48000, subtype="PCM_24")
    res[os.path.basename(path)] = dict(dur=round(len(zz) / 48000, 3), start_trim=round(s0 / sr, 3),
                                       peak_db=round(20 * np.log10(np.abs(zz).max()), 2))
    if path == a.out:
        res["segments"] = [dict(label=labels[i], start=round(max(0, bounds[i] - s0) / sr, 3),
                                end=round(min(len(zt), bounds[i + 1] - s0) / sr, 3), raw_db=round(lv[i], 1),
                                gain_db=round(gains[i], 1)) for i in range(n)]
        if a.room and a.bedded:
            room, rsr = sf.read(a.room, dtype="float64")
            if room.ndim > 1:
                room = room.mean(1)
            if rsr != 48000:
                room = librosa.resample(room, orig_sr=rsr, target_sr=48000, res_type="soxr_vhq")
            xf = int(0.1 * 48000); w = np.sin(np.linspace(0, np.pi / 2, xf)) ** 2
            bed = room.copy()
            while len(bed) < len(zz):
                bed = np.concatenate([bed[:-xf], bed[-xf:] * (1 - w) + room[:xf] * w, room[xf:]])
            sf.write(a.bedded, (zz + bed[:len(zz)]).astype(np.float32), 48000, subtype="PCM_24")
            res["bedded"] = a.bedded
json.dump(res, open(root + ".json", "w"), indent=1)
print(json.dumps(res, indent=1))
