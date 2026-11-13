#!/usr/bin/env python3
"""Verify a finished talking-head edit.

Usage: verify.py OUT.mp4 cuts.json [--model medium.en] [--sheet joins.jpg] [--stills s.jpg] [--names Co,Guest] [--quick]

Checks and prints:
  1. stream specs (size, fps, codecs), audio vs video duration
  2. integrated loudness and true peak of the final file
  3. full transcription of the output with word timestamps, and a word diff against the
     concatenated expected "text" of each piece (missing / extra words = clipped or leftover)
  4. every piece join: output time, transcription of +/-2s around it, and the envelope level
     at each source cut edge (a cut edge above threshold means a word may be clipped)
  5. a contact sheet of the last frame before / first frame after every piece join so posture,
     hand and framing jumps (a tell-tale of stitched retakes) can be eyeballed
  6. residual silence at every join (time below thresh spliced in from the source envelopes:
     previous piece's tail after its last loud frame + next piece's head before its first)
  7. repeated-word scan: adjacent identical words, repeated 2-5 word phrases within ~12 words,
     any phrase of >= 2 content words used twice anywhere (e.g. two CTAs), and words longer
     than 0.6s (Whisper hides an immediate repeat inside one long word). Then every piece is
     re-transcribed IN ISOLATION from the output (Whisper merges repeats in long context) and
     scanned again. Anything flagged must be probed (probe_regions.py) or fixed.
  8. --stills: one still per piece with face box / center line / eye band (reframe.sheet)
  9. last-word check (v8): the last expected word of EVERY piece must be heard in the FULL-output
     pass at the end of that piece (fuzzy match, so "Claude"/"clawed" passes). The full pass is what
     a viewer hears in context; Whisper in isolation happily "hears" a clipped word from its first
     fricative ("free" from just the f-). A FAIL means a clipped ending: inspect the spectrogram.
 10. true join gaps (v8): room tone between the previous word's real decay (broadband AND 2-8 kHz
     near the local floor) and the next word's real attack, not just time below -47 dB.
 11. framing (v10): every talking piece of the OUTPUT is measured with the same YuNet detector:
     face height and eye line vs its role target (settings.frame_targets, +/-frame_tol and
     +/-eye_tol), plus the upscale factor and the real source pixels each role keeps.
 12. audibility (v10): active speech loudness (K-weighted, silence gated) of every piece and every
     sentence of the output vs the median (FLAG more than audible_piece_lu below), the spread, and
     every word whose peak sits less than audible_word_db over the local room tone or whose tail
     (last 80 ms) at a sentence or piece end drops to within audible_tail_db of it.
 13. story beats (v11, interview reels): pieces tagged `story` (hook / question / bridge / answer / payoff,
     optional `story_key` = the word the beat lands on) get their land time in the output printed, plus
     gates: the hook is the first piece, the question lands by story_question_by (10 s), the first
     answer piece starts after story_answer_after (60%) of the reel, and no `spoilers` word (the answer
     words, e.g. "fired") is heard between the question and the answer.
 14. repeated lines (v12, WARN only): every 3+ word phrase (case / punctuation insensitive, stopword-only phrases
     ignored) said more than once in the output, plus the count of each --names / settings.interview_names entry
     (company, guest). Candidates for "one statement per idea" cuts.
     --quick: only specs, loudness, full transcript, diff, last-word gate story beats and repeated lines (use it on a
     final with music and captions, where the audibility / join numbers no longer mean anything).
"""


def similar(a, b):
    a, b = a.replace("'", ""), b.replace("'", "")
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.6 or (len(a) > 3 and (a in b or b in a))


def last_word_check(words, C):
    """For each piece: is its last expected word in the full-output transcript near its end?"""
    rows, t, fails = [], 0.0, 0
    for p in C["pieces"]:
        d = sum(e - s for s, e in p["cuts"]); t1 = t + d
        exp = norm(p.get("text", ""))
        if exp:
            want = exp[-1]
            near = [(a, b, w) for a, b, x in words for w in norm(x) if a < t1 + 0.05 and b > t1 - 1.5]
            hit = [(a, w) for a, b, w in near if similar(w, want)]
            ok = bool(hit)
            fails += not ok
            heard = " ".join(w for _, _, w in near[-4:])
            rows.append(f"  {'OK  ' if ok else 'FAIL'} {p['label']:>6} ends {t1:6.2f}s  last word '{want}'"
                        + (f" heard @{hit[-1][0]:.2f}" if ok else f"  <-- NOT heard in the full pass (heard: {heard})"))
        t = t1
    return rows, fails


STORY_DEFAULTS = {"story_question_by": 10.0, "story_answer_after": 0.6, "spoilers": []}


def story_check(words, C, S):
    """Interview reels: where does each tagged story beat land in the output, and does the structure hold?

    Pieces carry `story` (hook / question / bridge / answer / payoff) and optionally `story_key`, the word
    the beat lands on ("cut", "didn't"). Gates: the hook is the first piece; the question lands by
    story_question_by s (the cliffhanger has to be planted before viewers scroll); the first answer piece
    starts after story_answer_after of the reel (the answer is held for the end); no spoiler word is heard
    between the question and the answer."""
    S = {**STORY_DEFAULTS, **S}
    rows, t, fails, beats = [], 0.0, 0, []
    total = sum(e - s for p in C["pieces"] for s, e in p["cuts"])
    for i, p in enumerate(C["pieces"]):
        d = sum(e - s for s, e in p["cuts"]); t1 = t + d
        if p.get("story"):
            key = norm(p.get("story_key", ""))
            land, how = t, "piece start"
            if key:
                hit = [a for a, b, x in words for w in norm(x) if t - 0.15 <= a <= t1 + 0.05 and similar(w, key[0])]
                land, how = (hit[0], f"'{key[0]}' heard") if hit else (t, f"'{key[0]}' NOT heard, piece start")
            beats.append((p["story"], i, p["label"], t, t1, land))
            rows.append(f"  {p['story']:>8} {p['label']:>6}  {t:6.2f}-{t1:6.2f}s  lands @{land:6.2f}s ({how})  "
                        f"| {p.get('text', '')[:70]}")
        t = t1
    if not beats:
        return [], 0
    kinds = {}
    for b in beats:
        kinds.setdefault(b[0], []).append(b)
    def gate(ok, msg):
        nonlocal fails
        fails += not ok
        rows.append(f"  {'OK  ' if ok else 'FAIL'} {msg}")
    if "hook" in kinds:
        gate(kinds["hook"][0][1] == 0, f"hook is the first piece ({kinds['hook'][0][2]})")
    q = kinds.get("question"); a = kinds.get("answer")
    if q:
        gate(q[0][5] <= S["story_question_by"],
             f"question lands @{q[0][5]:.2f}s (by {S['story_question_by']:.0f}s), ends @{q[-1][4]:.2f}s")
    if a:
        gate(a[0][3] >= S["story_answer_after"] * total,
             f"answer starts @{a[0][3]:.2f}s = {a[0][3] / total:.0%} of {total:.1f}s (after {S['story_answer_after']:.0%}); "
             f"first answer word @{a[0][5]:.2f}s, last answer piece ends @{a[-1][4]:.2f}s")
    if q and a and S["spoilers"]:  # exact or prefix match: fuzzy matching turns "laid" into "ai"
        sp = [norm(x)[0] for x in S["spoilers"] if norm(x)]
        leak = [(st, w) for st, en, x in words for w in norm(x)
                if q[-1][4] <= st < a[0][3] and any(w == k or (len(k) >= 4 and w.startswith(k)) for k in sp)]
        gate(not leak, "no spoiler between question and answer" if not leak else
             "spoiler heard before the answer: " + ", ".join(f"'{w}'@{st:.2f}" for st, w in leak))
    if "payoff" in kinds:
        pf = kinds["payoff"][-1]
        rows.append(f"       payoff lands @{pf[5]:.2f}s, reel speech ends @{total:.2f}s")
    return rows, fails
import argparse, difflib, json, os, re, subprocess, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (FFMPEG, FFPROBE, load_audio, load_source, envelope, band_envelopes, Floors, decay_end,
                    attack_start, quiet_mask, speech_loudness, room_floor, DEFAULTS)
from assemble_audio import loudness

norm = lambda s: re.sub(r"[^a-z0-9' ]", " ", s.lower().replace("-", " ")).split()


STOP = set("a an the i i'd i've i'm you to of and or but so is it it's that this in on for my me "
           "we we're can do just like be are was there's here's here there what with your".split())


def residual(env, cut, S, side):
    """Seconds below thresh between a cut edge and the nearest loud frame inside the cut."""
    db, t = env
    s, e = cut
    m = (t >= s) & (t <= e)
    loud = np.where(db[m] > S["thresh_db"])[0]
    if not len(loud):
        return e - s
    tt = t[m]
    return max(0.0, (tt[loud[0]] - s) if side == "head" else (e - tt[loud[-1]]))


def repeat_scan(words, tag=""):
    """words: [(start, end, word)]. Returns list of human-readable flags."""
    w = [(a, b, n) for a, b, x in words for n in norm(x)[:1]]
    flags = []
    for (a0, b0, x), (a1, b1, y) in zip(w, w[1:]):
        if x == y:
            flags.append(f"{tag}adjacent repeat '{x}' @{a0:.2f}")
    for a, b, x in w:
        if b - a > 0.6 and x not in ("automatically",):
            flags.append(f"{tag}long word '{x}' {b - a:.2f}s @{a:.2f} (possible merged repeat)")
    toks = [x for _, _, x in w]
    for n in (2, 3, 4, 5):
        for i in range(len(toks) - n + 1):
            g = toks[i:i + n]
            for j in range(i + 1, min(len(toks) - n + 1, i + 12)):
                if toks[j:j + n] == g and (n >= 3 or any(t not in STOP for t in g)):
                    flags.append(f"{tag}near repeat '{' '.join(g)}' @{w[i][0]:.2f} and @{w[j][0]:.2f}")
    seen = {}
    for i in range(len(toks) - 1):
        g = tuple(toks[i:i + 2])
        if all(t not in STOP for t in g):
            if g in seen and i - seen[g] >= 12:
                flags.append(f"{tag}phrase used twice '{' '.join(g)}' @{w[seen[g]][0]:.2f} and @{w[i][0]:.2f}")
            seen.setdefault(g, i)
    return flags


REPEAT_STOP = STOP | set("not no as at by if its our they them he she his her their then than when which who "
                          "would could should will have has had been being did does don't didn't really very "
                          "some more about from out up how why all".split())


def repeats_report(words, names=()):
    """Interview reels (v12): WARN-level report of repeated lines in the final transcript.
    words: [(start, end, word)]. Lists every 3+ word phrase (case / punctuation insensitive, not made only of
    stopwords like "it was just") spoken more than once (longest phrase wins; sub-phrases of a reported phrase
    with the same count are folded in), plus how often each name (company / guest) is said. A hit is a candidate
    cut (one statement per idea), never a hard fail: a repeat may be deliberate. Returns (rows, n_warn)."""
    w = [(a, n) for a, _, x in words for n in norm(x)[:1]]
    toks = [n for _, n in w]
    rows, found = [], []
    for n in range(8, 2, -1):
        at = {}
        for i in range(len(toks) - n + 1):
            g = tuple(toks[i:i + n])
            if all(t in REPEAT_STOP for t in g):
                continue
            if at.get(g) and i < at[g][-1][0] + n:
                continue  # overlapping with its own previous hit (a run like "very very very")
            at.setdefault(g, []).append((i, w[i][0]))
        for g, hits in at.items():
            if len(hits) < 2:
                continue
            gs = " ".join(g)
            if any(gs in f and len(h) >= len(hits) for f, h in found):
                continue
            found.append((gs, hits))
    for gs, hits in sorted(found, key=lambda f: f[1][0][1]):
        rows.append(f"  WARN x{len(hits)} '{gs}' @" + ", @".join(f"{t:.2f}s" for _, t in hits))
    nwarn = len(rows)
    for nm in names:
        parts = norm(nm)
        if not parts:
            continue
        hits = [w[i][0] for i in range(len(toks) - len(parts) + 1) if toks[i:i + len(parts)] == parts]
        flag = "  WARN" if len(hits) > 2 else "  info"
        nwarn += len(hits) > 2
        rows.append(f"{flag} name '{nm}' said {len(hits)}x" + (" @" + ", @".join(f"{t:.2f}s" for t in hits) if hits else "")
                    + ("  (more than hook + one repeat: cut the re-said ones)" if len(hits) > 2 else ""))
    return rows, nwarn


def framing_check(video, C, S):
    """Measure the face in every talking piece of the output (median of 3 frames) vs its role target."""
    from reframe import Detector, grab
    det = Detector(S.get("face_model")) if S.get("face_model") else Detector()
    target = S.get("frame_mode", "target") == "target"
    rows, fails, t = [], 0, 0.0
    for row in S.get("frame_report", []):
        rows.append(f"  source {row['src']} {row['role']:>5}: zoom {row['zoom']:.3f}, keeps {row['kept_px'][0]}x"
                    f"{row['kept_px'][1]} of {row['src_px'][0]}x{row['src_px'][1]} source px, upscale "
                    f"{row['upscale']:.2f}x to {S['width']}x{S['height']}"
                    + ("  WARN capped by zoom_max" if row.get("capped") else "")
                    + (f"  WARN < {S['upscale_warn_px']} px real height" if row["kept_px"][1] < S["upscale_warn_px"] else ""))
    for p in C["pieces"]:
        d = sum(e - s for s, e in p["cuts"]); t0 = t; mid = t + d / 2; t += d
        fc = p.get("face") or {}
        if p.get("insert") or not fc.get("role"):
            continue
        # median of 3 frames (25/50/75 %): a speaker sways +-0.06 in x within a shot, one frame is noise
        got = [g for g in (det(grab(video, t0 + d * q)) for q in (0.25, 0.5, 0.75)) if g]
        f = [float(np.median([g[k] for g in got])) for k in range(3)] if got else None
        if not f:
            rows.append(f"  FLAG {p['label']:>6} @{mid:6.2f}s no face found in the output"); fails += 1; continue
        tag = fc["role"] + (" handheld" if fc.get("handheld") else "")
        line = f"{p['label']:>6} @{mid:6.2f}s {tag:>14} zoom {p.get('zoom', 1):.2f}  face {f[2]:.3f}  eye {f[1]:.3f}  x {f[0]:.3f}"
        if target and not fc.get("handheld"):
            tg = S["frame_targets"][("wide", "punch").index(fc["role"])]
            dh = f[2] / tg["face_h"] - 1; de = f[1] - tg["eye_y"]
            bad = abs(dh) > S["frame_tol"] or abs(de) > S["eye_tol"] or abs(f[0] - 0.5) > 0.04
            fails += bad
            line += f"  vs target face {tg['face_h']} ({dh * 100:+.0f}%) eye {tg['eye_y']} ({de:+.3f})"
            rows.append(("  FLAG " if bad else "  OK   ") + line)
        else:
            rows.append("  --   " + line)
    return rows, fails


def audibility(x, sr, C, words, S):
    """Per piece / per sentence active speech loudness of the output vs the median, and per-word
    level vs the local room tone. Returns (lines, n_flags, summary dict)."""
    env, et = envelope(x, sr=sr, win=0.02, hop=0.005)
    def floor_at(a, b, w=4.0):
        i0, i1 = max(0, int((a - w) * sr)), min(len(x), int((b + w) * sr))
        return room_floor(x[i0:i1], sr, pct=5)
    lines, nflag = [], 0
    pcs, t = [], 0.0
    for p in C["pieces"]:
        d = sum(e - s for s, e in p["cuts"])
        fl = floor_at(t, t + d)
        L, act = speech_loudness(x[int(t * sr):int((t + d) * sr)], sr, fl)
        pcs.append([p["label"], t, t + d, L, act, fl]); t += d
    good = [q for q in pcs if q[4] >= 0.3 and np.isfinite(q[3])]
    med = float(np.median([q[3] for q in good])) if good else float("nan")
    lim = S["audible_piece_lu"]
    lines.append(f"  pieces (active speech loudness vs median {med:.1f}; FLAG < -{lim} LU):")
    for lab, a0, a1, L, act, fl in pcs:
        bad = np.isfinite(L) and act >= 0.3 and L - med < -lim
        nflag += bad
        lines.append(f"    {'FLAG' if bad else 'ok  '} {lab:>8} {a0:6.2f}-{a1:6.2f}s  {L - med:+5.1f} LU  (speech {act:.1f}s, "
                     f"local floor {fl:.0f} dBFS, speech {L - fl:.0f} dB over it)")
    dv = [q[3] - med for q in good]
    summ = {"piece_min": min(dv), "piece_max": max(dv), "piece_range": max(dv) - min(dv),
            "piece_iqr": float(np.subtract(*np.percentile(dv, [75, 25])))} if dv else {}
    # sentences: Whisper words split at . ? ! or a gap > 0.6 s
    sents, cur = [], []
    for w in words:
        if cur and w[0] - cur[-1][1] > 0.6:
            sents.append(cur); cur = []
        cur.append(w)
        if re.search(r"[.?!]$", w[2]):
            sents.append(cur); cur = []
    if cur:
        sents.append(cur)
    sl = []
    for sg in sents:
        a0, a1 = sg[0][0], sg[-1][1]
        L, act = speech_loudness(x[int(a0 * sr):int(a1 * sr)], sr, floor_at(a0, a1))
        if act >= 0.3 and np.isfinite(L):
            sl.append((a0, a1, L, " ".join(w[2] for w in sg)))
    if sl:
        smed = float(np.median([q[2] for q in sl]))
        sd = [q[2] - smed for q in sl]
        summ.update({"sent_min": min(sd), "sent_max": max(sd), "sent_range": max(sd) - min(sd)})
        lines.append(f"  sentences: {len(sl)}, spread {min(sd):+.1f}..{max(sd):+.1f} LU around the median")
        for a0, a1, L, txt in sl:
            if L - smed < -lim:
                nflag += 1
                lines.append(f"    FLAG sentence {a0:6.2f}-{a1:6.2f}s {L - smed:+.1f} LU: {txt[:70]}")
    # words: peak over the local room tone; tails at sentence / piece ends
    ends = {round(sg[-1][1], 3) for sg in sents}
    pend = [q[2] for q in pcs]
    wfl, tfl = 0, 0
    for a0, a1, wd in words:
        # Whisper sometimes stacks two words on one timestamp ("And@18.66 I@18.66"): a 0-5 ms window reads a single
        # envelope sample, so measure short words over 120 ms from their start and skip their (unknown) tail
        short = a1 - a0 < 0.06
        m = (et >= a0) & (et <= (a0 + 0.12 if short else a1))
        if not m.any():
            continue
        fl = floor_at(a0, a1)
        pk = float(env[m].max())
        if pk < fl + S["audible_word_db"]:
            wfl += 1; nflag += 1
            lines.append(f"    FLAG word '{wd}' @{a0:.2f}: peak only {pk - fl:.0f} dB over the local floor")
        last = not short and (round(a1, 3) in ends or any(abs(a1 - e_) < 0.25 for e_ in pend))
        if last:
            mt = (et >= max(a0, a1 - 0.08)) & (et <= a1)
            tl = float(env[mt].max()) if mt.any() else pk
            if tl < fl + S["audible_tail_db"]:
                tfl += 1; nflag += 1
                lines.append(f"    FLAG tail of '{wd}' @{a1:.2f}: last 80 ms only {tl - fl:.0f} dB over the local floor")
    summ.update({"word_flags": wfl, "tail_flags": tfl})
    lines.append(f"  words: {wfl} below the audibility floor, {tfl} sentence/piece-final tails into the room tone")
    return lines, nflag, summ


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video"); ap.add_argument("cuts")
    ap.add_argument("--model", default="medium.en")
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--stills", default=None, help="per-piece face-centering stills sheet (jpg)")
    ap.add_argument("--names", default=None,
                    help="comma list of the interview's company / guest names to count (default: settings.interview_names)")
    ap.add_argument("--quick", action="store_true",
                    help="specs, loudness, transcript, diff, last-word gate and story beats only (finals with music)")
    a = ap.parse_args()
    C = json.load(open(a.cuts)); S = {**DEFAULTS, **C["settings"]}; C["settings"] = S; fps = S["fps"]
    pr = json.loads(subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                                    "stream=codec_type,codec_name,width,height,r_frame_rate,duration,bit_rate",
                                    "-of", "json", a.video], capture_output=True, text=True).stdout)["streams"]
    for st in pr:
        print("stream", {k: st.get(k) for k in ("codec_type", "codec_name", "width", "height", "r_frame_rate", "duration", "bit_rate")})
    I, TP = loudness(a.video)
    ok_loud = abs(I - S["lufs"]) <= 0.5 and TP <= S["tp"]
    print(f"loudness I={I:.1f} LUFS TP={TP:.1f} dBTP  -> {'OK' if ok_loud else 'FAIL'}")

    # joins in output time
    joins, t = [], 0.0
    for i, p in enumerate(C["pieces"]):
        if i:
            joins.append((t, i))
        t += sum(e - s for s, e in p["cuts"])
    print(f"planned duration {t:.3f}s")

    from faster_whisper import WhisperModel
    m = WhisperModel(a.model, device="cpu", compute_type="int8")
    x16 = load_audio(a.video, channels=1, sr=16000)
    segs, _ = m.transcribe(x16, beam_size=5, word_timestamps=True, condition_on_previous_text=False)
    words = [(w.start, w.end, w.word.strip()) for g in segs for w in g.words]
    if a.quick:
        S = {**S, "reframe": False}
    print("\n== audibility: can every part of the speech be heard (per piece / sentence / word)")
    al, an, asum = audibility(x16, 16000, C, words, S) if not a.quick else (["  skipped (--quick)"], 0, None)
    print("\n".join(al))
    if asum:
        print(f"  -> piece spread {asum['piece_min']:+.1f}..{asum['piece_max']:+.1f} LU (range {asum['piece_range']:.1f}, "
              f"IQR {asum['piece_iqr']:.1f}); {an} FLAG(s)" + ("" if an else ", all audible"))
    if S.get("reframe", True):
        print("\n== framing vs target (output measured with YuNet)")
        fr, ff = framing_check(a.video, C, S)
        print("\n".join(fr))
        print(f"  -> {'all talking pieces on target' if not ff else f'{ff} FLAG(s): check the stills'}")
    print("\n== output transcript")
    print(" ".join(w[2] for w in words))
    print(" ".join(f"{w[2]}@{w[0]:.2f}" for w in words))
    exp = " ".join(p.get("text", "") for p in C["pieces"]).strip()
    if exp:
        ew, gw = norm(exp), norm(" ".join(w[2] for w in words))
        diff = [d for d in difflib.ndiff(ew, gw) if d[0] in "+-"]
        print("\n== diff vs expected (- missing, + extra):", " ".join(diff) if diff else "none")

    print("\n== last word of every piece in the FULL-output pass (clipped-ending gate)")
    rows, nfail = last_word_check(words, C)
    print("\n".join(rows))
    print(f"  -> {'all last words heard' if not nfail else f'{nfail} FAIL: inspect spectrogram + listen'}")

    srows, sfail = story_check(words, C, S)
    if srows:
        print("\n== story beats (interview reels: hook / question / answer land times in the output)")
        print("\n".join(srows))
        print(f"  -> {'story structure holds' if not sfail else f'{sfail} FAIL: the cliffhanger structure is broken'}")
    names = [n.strip() for n in (a.names.split(",") if a.names else S.get("interview_names", [])) if n.strip()]
    rrows, rwarn = repeats_report(words, names)
    print("\n== repeated lines (v12: 3+ word phrases said more than once, name counts; WARN only)")
    print("\n".join(rrows) if rrows else "  none")
    print(f"  -> {'no repeated phrases' if not rwarn else f'{rwarn} WARN: cut restatements / elaborations / re-said names, keep hedges (see SKILL.md, Cut the guest repeats)'}")
    if a.quick:
        return

    print("\n== repeated-word scan (full output)")
    fl = repeat_scan(words)
    print("\n".join("  " + f for f in fl) if fl else "  none")
    print("\n== per-piece isolated transcription of the output + repeat scan")
    tp, pflags = 0.0, []
    pad = np.zeros(8000, np.float32)
    for p in C["pieces"]:
        d = sum(e - s for s, e in p["cuts"])
        seg = np.concatenate([pad, x16[int(tp * 16000):int((tp + d) * 16000)], pad])
        sg, _ = m.transcribe(seg, beam_size=5, word_timestamps=True, condition_on_previous_text=False)
        pw = [(w.start - 0.5 + tp, w.end - 0.5 + tp, w.word.strip()) for g in sg for w in g.words]
        print(f"  {tp:6.2f}s {p['label']:>6} | {' '.join(x for _, _, x in pw)}")
        pflags += repeat_scan(pw, f"[{p['label']}] ")
        tp += d
    print("\n".join("  " + f for f in pflags) if pflags else "  per-piece scan: none")
    print("  (a leading 'So'/'But'/'And' on an isolated clip can be a Whisper insertion; trust the full pass)")

    print("\n== joins (listen to each)")
    envs = {}
    for k in {p["src"] for p in C["pieces"]}:
        envs[k] = envelope(load_source(C, k, channels=1, highpass=S["highpass"]),
                           win=S["env_win"], hop=S["env_hop"])
    def lvl(k, tt):
        db, t = envs[k]; return float(db[np.argmin(abs(t - tt))])
    gaps = []
    for jt, i in joins:
        lo, hi = max(0, jt - 2), jt + 2
        seg, _ = m.transcribe(x16[int(lo * 16000):int(hi * 16000)], beam_size=5, word_timestamps=True,
                              condition_on_previous_text=False)
        txt = " ".join(f"{w.word.strip()}@{w.start + lo:.2f}" for g in seg for w in g.words)
        prev, cur = C["pieces"][i - 1], C["pieces"][i]
        e_out = lvl(prev["src"], prev["cuts"][-1][1]); e_in = lvl(cur["src"], cur["cuts"][0][0])
        flag = "" if max(e_out, e_in) < S["thresh_db"] + 3 else "  <-- cut edge on sound, check clipping"
        gap = residual(envs[prev["src"]], prev["cuts"][-1], S, "tail") + residual(envs[cur["src"]], cur["cuts"][0], S, "head")
        gaps.append((jt, prev["label"], cur["label"], gap))
        print(f"{jt:6.2f}s  {prev['label']} -> {cur['label']}  residual silence {gap*1000:.0f} ms  "
              f"edge dB out {e_out:.0f} / in {e_in:.0f}{flag}\n        {txt}")
    mode = S.get("tail_mode", "tight")
    if mode in ("decay", "tight"):
        B = {k: band_envelopes(C, k, S) for k in envs}; Fl = {k: Floors(*B[k], S) for k in B}
        tg, sg = [], []
        def run_edge(k, x, d):  # end (d=+1) / start (d=-1) of the loud run (> thresh, 30ms dips bridged) at x
            bb_, _, t_ = B[k]; j = int(np.searchsorted(t_, x)); q = 0; last = None
            while 0 <= j < len(t_) and abs(t_[j] - x) < 0.6:
                if bb_[j] > S["thresh_db"]: last = float(t_[j]); q = 0
                else:
                    q += 1
                    if q >= 6: break
                j += d
            if last is None:  # cut in silence: nearest loud frame on the word's side
                bb_, _, t_ = B[k]; m_ = (t_ < x) & (t_ > x - 1.0) if d > 0 else (t_ > x) & (t_ < x + 1.0)
                w_ = t_[m_ & (bb_ > S['thresh_db'])]
                last = (float(w_[-1]) if d > 0 else float(w_[0])) if len(w_) else None
            return last
        for jt, i in joins:
            pv, cu = C["pieces"][i - 1], C["pieces"][i]
            e0, s0 = pv["cuts"][-1][1], cu["cuts"][0][0]
            o = run_edge(pv["src"], e0 - 0.03, 1); n = run_edge(cu["src"], s0 + 0.03, -1)
            sg.append(((e0 - o) if o is not None else 0.0, (n - s0) if n is not None else 0.0))
            prev, cur = C["pieces"][i - 1], C["pieces"][i]
            bb, hf, tt = B[prev["src"]]; e0 = prev["cuts"][-1][1]
            m = (tt <= e0) & (tt >= e0 - 1.0); loud = np.where(m & (bb > S["thresh_db"]))[0]
            off = float(tt[loud[-1]]) if len(loud) else e0
            dec, _ = decay_end(bb, hf, tt, off, e0, Fl[prev["src"]].at(off), S)
            bb2, hf2, tt2 = B[cur["src"]]; s0 = cur["cuts"][0][0]
            m2 = (tt2 >= s0) & (tt2 <= s0 + 1.0); loud2 = np.where(m2 & (bb2 > S["thresh_db"]))[0]
            on = float(tt2[loud2[0]]) if len(loud2) else s0
            att = attack_start(bb2, hf2, tt2, on, s0, Fl[cur["src"]].at(on), S)
            tg.append((prev["label"], cur["label"], max(0.0, e0 - dec) + max(0.0, att - s0)))
        jg = S.get("tight_join_gap", [0.0, 0.04]) if mode == "tight" else S.get("join_gap", [0.06, 0.11])
        print("\n== TRUE join gaps (room tone between real decay and real attack, target "
              f"{jg[0]*1000:.0f}-{jg[1]*1000:.0f} ms): " +
              ", ".join(f"{a_}->{b_} {g*1000:.0f}" for a_, b_, g in tg))
        gg = [g for *_, g in tg]
        print(f"   min {min(gg)*1000:.0f} / median {np.median(gg)*1000:.0f} / max {max(gg)*1000:.0f} ms")
        so, si = [x * 1000 for x, _ in sg], [y * 1000 for _, y in sg]
        print("== signed edges vs the -47 dB crossings (out: cut - word offset, negative = cut on the tail; "
              "in: word onset - cut, negative = cut into the attack): " +
              ", ".join(f"{C['pieces'][i-1]['label']}->{C['pieces'][i]['label']} {a:+.0f}/{b:+.0f}" for (_, i), a, b in zip(joins, so, si)))
        print(f"   out median {np.median(so):+.0f} ms, in median {np.median(si):+.0f} ms "
              "(hand-made reference re-cut: out -58, in -37)")
    print("\n== residual join gaps (ms below thresh at each join): " +
          ", ".join(f"{lt}->{lb} {g*1000:.0f}" for _, lt, lb, g in gaps))
    if gaps:
        gg = [g for *_, g in gaps]
        print(f"   min {min(gg)*1000:.0f} / median {np.median(gg)*1000:.0f} / max {max(gg)*1000:.0f} ms")
    if a.stills:
        from reframe import sheet
        sheet(a.video, C, a.stills)
    if a.sheet:
        tiles = []
        tmp = os.path.splitext(a.sheet)[0] + "_frames"; os.makedirs(tmp, exist_ok=True)
        for n, (jt, i) in enumerate(joins):
            for side, tt in (("a", jt - 1.5 / fps), ("b", jt + 0.5 / fps)):
                fn = f"{tmp}/j{n:02d}{side}.jpg"
                subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", f"{tt:.3f}", "-i", a.video, "-frames:v", "1",
                                "-vf", "scale=270:480",
                                fn], check=True)
                tiles.append(fn)
        cols = 8
        ins = sum([["-i", f] for f in tiles], [])
        fc = "".join(f"[{j}:v]" for j in range(len(tiles)))
        layout = "|".join(f"{(j % cols) * 270}_{(j // cols) * 480}" for j in range(len(tiles)))
        subprocess.run([FFMPEG, "-v", "error", "-y", *ins, "-filter_complex",
                        f"{fc}xstack=inputs={len(tiles)}:layout={layout}:fill=black", "-frames:v", "1", a.sheet], check=True)
        print("join contact sheet, 8 per row, read as pairs (before, after) for joins in order:", a.sheet)


if __name__ == "__main__":
    main()
