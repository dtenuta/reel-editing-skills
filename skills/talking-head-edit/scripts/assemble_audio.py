#!/usr/bin/env python3
"""Assemble the audio from cuts.json with short crossfades, then loudness-normalize.

Usage:
  assemble_audio.py cuts.json --out-dir DIR            -> DIR/audio_raw.wav, DIR/audio_norm.wav
  assemble_audio.py cuts.json --out-dir DIR --trial    -> DIR/trial_XX.wav per piece + full-length
                                                          transcription of each trial clip
Every join gets an equal-length linear crossfade (settings.xfade, default 10ms). Each cut is
extended by the crossfade length, so total duration equals the sum of cut durations and stays
frame-locked to the video built from the same cuts.
Speech leveling (v10, settings.level_pieces, default on): before the final loudnorm every talking
piece gets ONE constant gain so its active speech loudness (K-weighted, silence gated) sits at the
median of the pieces; pieces within settings.level_tol LU are untouched. Boosts are capped by
level_max_boost_db and by the room tone (the boosted floor may rise at most level_floor_rise_db
over the median piece floor, level_floor_rise_masked_db when the piece has no exposed pause).
No compression and no new fades, so butt cuts stay butt cuts and word endings are untouched.
Per-piece opt-out: "level": false. Inserts are matched to their neighbours instead (after leveling).
The table is printed and written to OUT_DIR/level_report.json.
Loudness: two-pass ffmpeg loudnorm (linear) to settings.lufs / settings.tp; result is measured
and printed. If linear mode cannot hit the true-peak target, an alimiter pass follows.
"""
import argparse, json, os, re, subprocess, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import FFMPEG, SR, load_audio, load_source, write_wav, speech_loudness, room_floor, longest_quiet


def piece_segs(p, gain=True):
    """[src, a, b, fade_in, fade_out, gain] per cut; fades only on the piece's outer edges; gain =
    the piece's speech-leveling gain (linear, constant over the whole piece)."""
    e = p.get("edge", {}); n = len(p["cuts"])
    g = 10 ** (p.get("_level_db", 0.0) / 20) if gain else 1.0
    return [[p["src"], a, b, e.get("fade_in", 0) if i == 0 else 0, e.get("fade_out", 0) if i == n - 1 else 0, g]
            for i, (a, b) in enumerate(p["cuts"])]

PY_NOTE = "run with the venv python that has faster-whisper (scripts/find_python.sh)"


def splice(A, segs, xf):
    """segs: [src, a, b] or [src, a, b, fade_in, fade_out]. A fade_out (a word cut at the dip
    before the next word in the source) ramps to zero AT b and silences the crossfade spill past b,
    so the next source word never leaks in; a fade_in ramps up from a."""
    X = max(1, int(xf * SR))
    out = None
    for sg in segs:
        k, a, b = sg[:3]
        fi, fo = (sg[3], sg[4]) if len(sg) > 4 else (0, 0)
        p = A[k][int(round(a * SR)):int(round(b * SR)) + X].copy()
        if len(sg) > 5 and sg[5] != 1.0:
            p *= sg[5]
        if fo:
            n = int(fo * SR); L = len(p) - X
            p[max(0, L - n):L] *= np.linspace(1, 0, min(n, L))[:, None] if p.ndim > 1 else np.linspace(1, 0, min(n, L))
            p[L:] = 0
        if fi:
            n = min(int(fi * SR), len(p))
            p[:n] *= np.linspace(0, 1, n)[:, None] if p.ndim > 1 else np.linspace(0, 1, n)
        if out is None:
            out = p; continue
        f = np.linspace(0, 1, X)[:, None]
        ov = out[-X:] * (1 - f) + p[:X] * f
        out = np.concatenate([out[:-X], ov, p[X:]])
    return out[:-X]


def loudness(path):
    r = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", path, "-af",
                        "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True).stderr
    summ = r[r.rfind("Summary:"):]
    I = float(re.search(r"I:\s+(-?[\d.]+) LUFS", summ).group(1))
    TP = float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", summ).group(1))
    return I, TP


def normalize(src, dst, lufs, tp, lra):
    tgt_tp = tp - 0.5  # margin for AAC encode
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", src, "-af",
                        f"loudnorm=I={lufs}:TP={tgt_tp}:LRA={lra}:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    m = json.loads(r[r.rfind("{"):r.rfind("}") + 1])
    af = (f"loudnorm=I={lufs}:TP={tgt_tp}:LRA={lra}:linear=true:measured_I={m['input_i']}:"
          f"measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:"
          f"offset={m['target_offset']}")
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", src, "-af", af, "-ar", str(SR), "-c:a", "pcm_s16le", dst], check=True)
    I, TP = loudness(dst)
    if TP > tgt_tp + 0.2 or abs(I - lufs) > 0.3:
        # linear mode could not reach both targets: apply gain to the LUFS target, then limit peaks
        tmp = dst + ".tmp.wav"
        g = lufs - loudness(src)[0]
        lim = 10 ** ((tgt_tp - 0.3) / 20)
        for _ in range(5):  # limiting lowers loudness; iterate the make-up gain
            subprocess.run([FFMPEG, "-v", "error", "-y", "-i", src, "-af",
                            f"volume={g:.2f}dB,aresample=192000,alimiter=limit={lim}:level=false:attack=2:release=60,"
                            f"aresample={SR}", "-c:a", "pcm_s16le", tmp], check=True)
            I, TP = loudness(tmp)
            if abs(I - lufs) <= 0.2:
                break
            g += lufs - I
        os.replace(tmp, dst)
        I, TP = loudness(dst)
    return I, TP


def add_room_tone(A, C, S):
    """An insert recorded elsewhere (studio pickup, synthesized line) is dead quiet between words, which sounds
    like a dropout next to the room. Bed it on the main source's room tone: the quietest 1s of the
    source with the most kept material (after level matching), looped under the whole insert."""
    ins = [k for k in S.get("insert_sources", []) if k in A]
    if not ins or not S.get("insert_room_tone", True):
        return
    main_k = max((k for k in A if k not in ins), key=lambda k: sum(e - s for p in C["pieces"] if p["src"] == k
                                                                 for s, e in p["cuts"]))
    x = A[main_k]; w = SR; m = x.mean(axis=1) if x.ndim > 1 else x
    c = np.cumsum(np.concatenate([[0.0], m.astype(np.float64) ** 2]))
    starts = np.arange(0, len(m) - w, SR // 10)
    rms = (c[starts + w] - c[starts]) / w
    s0 = int(starts[np.argmin(rms)]); tone = x[s0:s0 + w]
    lvl = 10 * np.log10(rms.min() + 1e-12)
    if lvl > S.get("room_tone_max_db", -35.0):
        # a source that is already a tight edit (a hand-made re-cut) has no silent second: its
        # "quietest 1s" is speech, and looping it under the insert would put words under the demo.
        # Bed the insert yourself (raw camera room tone) and set insert_room_tone false.
        print(f"WARN insert room tone: quietest 1s of {main_k} is {lvl:.0f} dBFS (speech, not room tone) -> "
              f"NOT bedded; pre-bed the insert with raw room tone and set insert_room_tone false")
        return
    for k in ins:
        n = len(A[k]); reps = int(np.ceil(n / w))
        bed = np.concatenate([tone] * reps)[:n]
        A[k] = A[k] + bed
        print(f"insert {k}: room tone bed from {main_k} @ {s0 / SR:.1f}s ({10 * np.log10(rms.min() + 1e-12):.0f} dBFS)")


def level_pieces(A, C, S, out_dir=None, verbose=True):
    """Constant per-piece gain so every talking piece's active speech loudness sits at the median
    (see module doc). Sets p["_level_db"]; returns the report rows."""
    ins = set(S.get("insert_sources", []))
    rows = []
    for i, p in enumerate(C["pieces"]):
        if p.get("insert") or p["src"] in ins:
            continue
        x = splice(A, piece_segs(p, gain=False), S["xfade"])
        src = A[p["src"]]; w = S.get("floor_win", 2.5)
        a0 = max(0, int((p["cuts"][0][0] - w) * SR)); a1 = min(len(src), int((p["cuts"][-1][1] + w) * SR))
        fl = room_floor(src[a0:a1])
        L, act = speech_loudness(x, SR, fl)
        rows.append({"i": i, "label": p.get("label", ""), "L": L, "speech_s": act, "floor": fl,
                     "pause": longest_quiet(x, SR, fl), "skip": p.get("level") is False})
    ok = [r for r in rows if not r["skip"] and r["speech_s"] >= S["level_min_speech"] and np.isfinite(r["L"])]
    if len(ok) < 2:
        return rows
    tgt = float(np.median([r["L"] for r in ok])); fmed = float(np.median([r["floor"] for r in ok]))
    for r in rows:
        r["gain"], r["why"] = 0.0, ""
        if r["skip"]:
            r["why"] = "level: false"; continue
        if r not in ok:
            r["why"] = "too little speech to measure"; continue
        d = tgt - r["L"]
        if abs(d) <= S["level_tol"]:
            r["why"] = "within tolerance"; continue
        g = d; r["why"] = "leveled"
        if g > 0:
            rise = S["level_floor_rise_db"] if r["pause"] >= 0.15 else S["level_floor_rise_masked_db"]
            cap = min(S["level_max_boost_db"], fmed + rise - r["floor"])
            if g > cap:
                g = max(0.0, cap)
                r["why"] = ("boost capped by level_max_boost_db" if cap >= S["level_max_boost_db"] - 1e-9 else
                            f"boost capped: room tone would rise > {rise:.0f} dB over the median floor")
        else:
            g = max(g, -S["level_max_cut_db"])
        r["gain"] = round(g, 2)
        C["pieces"][r["i"]]["_level_db"] = r["gain"]
    for r in rows:
        r["after"] = r["L"] + r["gain"] if np.isfinite(r["L"]) else r["L"]
    if verbose:
        print(f"speech leveling: target {tgt:.1f} (median active speech loudness of {len(ok)} pieces), "
              f"median room floor {fmed:.1f} dBFS, tolerance +/-{S['level_tol']} LU")
        for r in rows:
            print(f"  {r['label']:>8} speech {r['L'] - tgt:+5.1f} LU  floor {r['floor']:6.1f}  longest pause "
                  f"{r['pause']*1000:4.0f} ms  gain {r['gain']:+5.1f} dB -> {r['after'] - tgt:+5.1f} LU  {r['why']}")
        b = [r["L"] - tgt for r in ok]; af = [r["after"] - tgt for r in ok]
        print(f"  spread before {min(b):+.1f}..{max(b):+.1f} ({max(b) - min(b):.1f} LU), after {min(af):+.1f}..{max(af):+.1f}"
              f" ({max(af) - min(af):.1f} LU)")
    if out_dir:
        json.dump({"target": tgt, "median_floor": fmed, "rows": rows}, open(os.path.join(out_dir, "level_report.json"), "w"),
                  indent=1, default=float)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cuts")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--trial", action="store_true")
    ap.add_argument("--model", default="medium.en")
    a = ap.parse_args()
    C = json.load(open(a.cuts)); S = C["settings"]
    os.makedirs(a.out_dir, exist_ok=True)
    A = {k: load_source(C, k, channels=2) for k in C["sources"]
         if any(p["src"] == k for p in C["pieces"])}
    if a.trial:
        from faster_whisper import WhisperModel
        m = WhisperModel(a.model, device="cpu", compute_type="int8")
        for i, p in enumerate(C["pieces"]):
            x = splice(A, piece_segs(p), S["xfade"])
            fn = os.path.join(a.out_dir, f"trial_{i:02d}.wav"); write_wav(fn, x)
            segs, _ = m.transcribe(fn, beam_size=5, word_timestamps=True, condition_on_previous_text=False)
            words = " ".join(f"{w.word.strip()}@{w.start:.2f}" for g in segs for w in g.words)
            print(f"[{i:02d}] {p['label']} {len(x)/SR:.2f}s | {words}")
            if p.get("text"):
                print(f"      expected: {p['text']}")
        return
    if S.get("match_sources", True) and len(A) > 1:
        # level-match sources (e.g. a hook shot on a different day/mic) so the joins between
        # them do not jump: each source gets a gain so its kept speech has the same integrated
        # loudness as the source with the most kept material. Capped at +/-max_match_db.
        I, dur = {}, {}
        for k in A:
            sk = [sg for p in C["pieces"] if p["src"] == k for sg in piece_segs(p)]
            xk = splice(A, sk, S["xfade"]); dur[k] = len(xk) / SR
            fn = os.path.join(a.out_dir, f"_src_{k}.wav"); write_wav(fn, xk)
            I[k] = loudness(fn)[0]; os.remove(fn)
        ins = set(S.get("insert_sources", []))
        ref = max((k for k in dur if k not in ins), key=dur.get)
        G = {}
        for k in A:
            if k in ins:
                continue
            cap = S.get("max_match_db", 6.0)
            G[k] = max(-cap, min(cap, I[ref] - I[k]))
            print(f"source {k}: kept {dur[k]:.1f}s at {I[k]:.1f} LUFS -> gain {G[k]:+.1f} dB (ref {ref})")
            A[k] = A[k] * (10 ** (G[k] / 20))
    if S.get("level_pieces", True):
        level_pieces(A, C, S, a.out_dir)
    if S.get("match_sources", True) and len(A) > 1:
        for k in A:
            if k not in ins:
                continue
            # an insert is matched to the speech right around it (the piece before and after, after
            # their own source gain), not to the whole source: a dense mastered line measured
            # against a source full of pauses lands ~2 LU hot. Mastered/synthesized lines usually arrive at
            # ~-14 LUFS next to raw phone speech (~-28), so any cut is allowed; only boost is capped.
            idx = [i for i, p in enumerate(C["pieces"]) if p["src"] == k]
            nb = [C["pieces"][j] for i in idx for j in (i - 1, i + 1)
                  if 0 <= j < len(C["pieces"]) and C["pieces"][j]["src"] not in ins]
            target = I[ref]
            if nb:
                xn = splice(A, [sg for p in nb for sg in piece_segs(p)], S["xfade"])
                fn = os.path.join(a.out_dir, f"_nb_{k}.wav"); write_wav(fn, xn)
                target = loudness(fn)[0]; os.remove(fn)
            trim = sum(p.get("gain_db", 0.0) for p in C["pieces"] if p["src"] == k) / max(1, len(idx))
            g = max(-40.0, min(S.get("insert_match_db", 12.0), target - I[k])) + trim
            print(f"insert {k}: {I[k]:.1f} LUFS, neighbours {target:.1f} LUFS -> gain {g:+.1f} dB"
                  + (f" (incl. gain_db {trim:+.1f})" if trim else ""))
            A[k] = A[k] * (10 ** (g / 20))
    add_room_tone(A, C, S)
    segs = [sg for p in C["pieces"] for sg in piece_segs(p)]
    x = splice(A, segs, S["xfade"])
    pk = float(np.abs(x).max())
    if pk > 0.98:  # a leveled piece may exceed full scale before loudnorm: scale, never clip
        x = x * (0.98 / pk)
    raw = os.path.join(a.out_dir, "audio_raw.wav"); write_wav(raw, x)
    norm = os.path.join(a.out_dir, "audio_norm.wav")
    I, TP = normalize(raw, norm, S["lufs"], S["tp"], S["lra"])
    print(f"audio {len(x)/SR:.3f}s  -> {norm}  I={I:.1f} LUFS  TP={TP:.1f} dBTP")


if __name__ == "__main__":
    main()
