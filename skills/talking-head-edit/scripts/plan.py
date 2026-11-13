#!/usr/bin/env python3
"""Snap each planned piece to the measured audio envelope and compress in-line pauses.

Usage: plan.py PLAN.json [--out cuts.json] [--peek SRC LO HI] [--bursts SRC [--gap 0.5]]

PLAN.json:
{
  "sources": {"A": "/abs/hook.MOV", "B": "/abs/body.MOV"},
  "settings": {... optional overrides of common.DEFAULTS ...},
  "pieces": [
    {"label": "hook", "src": "A", "lo": 0.0, "ws": 0.0, "we": 4.46, "hi": 5.3,
     "text": "expected words", "compress": true, "zoom": 1.0 (optional override)}
  ]
}
  lo/hi : hard search bounds (never cut outside them; set them in the silence between takes)
  pin   : optional [[a,b],...] exact cuts (frame-quantized, no envelope snapping) or "whole" (= ws..we,
          for an insert: the whole file). Use it for a source that is already an edit (e.g. a hand-made
          re-cut) so every join in it stays exactly where it was made.
  ws/we : Whisper start of first word / end of last word for the chosen attempt
          (approximate; the envelope decides the real cut)
  story / story_key : optional, interview reels: the beat this piece plays (hook / question / bridge /
          answer / payoff) and the word it lands on; passed through to cuts.json for verify.py's story gate

Writes cuts.json: [{"label","src","zoom","text","cuts":[[a,b],...]}] with frame-quantized times.
Whisper word edges are imprecise (often 50-300ms off). The cut is ALWAYS taken from the
envelope: onset = first frame above thresh within [ws-onset_slack, ...], offset = last frame
above thresh within [..., we+offset_slack], both clamped to [lo, hi].
"""
import argparse, json, math, sys, os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (load_plan, load_audio, load_source, envelope, band_envelopes, Floors,
                    decay_end, attack_start, quiet_mask)


def soft_edges(tt, dd, on, off, lo, hi, S, floor):
    """Follow a soft attack back from `on` and a soft tail forward from `off` (envelope above
    soft_db = max(thresh-6, source noise floor + soft_margin_db); in a noisy room this is ~thresh,
    so room tone is never mistaken for a tail), so tight handles never shave a breathy ending or a fricative attack."""
    soft = max(S["thresh_db"] - 6, floor + S.get("soft_margin_db", 6.0))
    if soft > S["thresh_db"] - 3:  # noisy room: no headroom between room tone and thresh
        return on, off, soft
    hop = S["env_hop"]
    i = int(np.searchsorted(tt, on)); k = 0
    while i - 1 >= 0 and dd[i - 1] > soft and tt[i - 1] >= lo and k * hop < S["attack_max"]:
        i -= 1; k += 1
    son = tt[i] if i < len(tt) else on
    j = int(np.searchsorted(tt, off)); k = 0
    while j + 1 < len(tt) and dd[j + 1] > soft and tt[j + 1] <= hi and k * hop < S["tail_max"]:
        j += 1; k += 1
    soff = tt[j] if j < len(tt) else off
    return min(son, on), max(soff, off), soft


def snap(db, t, p, S, floor=-60.0):
    lo, hi, ws, we = p["lo"], p["hi"], p["ws"], p["we"]
    m = (t >= lo) & (t <= hi)
    tt, dd = t[m], db[m]
    loud = dd > S["thresh_db"]
    c_on = loud & (tt >= ws - S["onset_slack"])
    c_off = loud & (tt <= we + S["offset_slack"])
    if not c_on.any() or not c_off.any():
        raise SystemExit(f"piece {p.get('label')}: no audio above {S['thresh_db']} dB in window")
    on = tt[np.argmax(c_on)]
    off = tt[len(tt) - 1 - np.argmax(c_off[::-1])]
    son, soff, soft = soft_edges(tt, dd, on, off, lo, hi, S, floor)
    s = max(lo, son - S["pre_onset"])
    e = min(hi, soff + S["post_offset"])
    cuts = [[s, e]]
    if p.get("compress", True):
        mm = (tt >= s) & (tt <= e)
        t2, d2 = tt[mm], dd[mm]
        quiet = d2 < S["thresh_db"]
        cuts, cur, i = [], s, 0
        k = S["pause_keep_each"]
        while i < len(t2):
            if quiet[i]:
                j = i
                while j < len(t2) and quiet[j]:
                    j += 1
                a, b = t2[i], t2[j - 1]
                if b - a >= S["pause_min"] and a > s + k and b < e - k:
                    # keep the soft tail of the word before and the soft attack of the word after
                    _, a2, _ = soft_edges(t2, d2, a, a, a, b, {**S, "attack_max": 0}, floor)
                    b2, _, _ = soft_edges(t2, d2, b, b, a2, b, {**S, "tail_max": 0}, floor)
                    if (b2 - k) - (a2 + k) > 0.02:
                        cuts.append([cur, a2 + k]); cur = b2 - k
                i = j
            else:
                i += 1
        cuts.append([cur, e])
    return on, off, son, soff, cuts


def snap_decay(B, p, S, F):
    """v8 edge finder (tail_mode "decay"). B = (bb, hf, t) envelopes, F = Floors.
    In-point: thresh onset -> soft attack (attack_start) -> keep pre_onset.
    Out-point: thresh offset -> true decay (decay_end: both bands near the local floor, or the dip
    before the next word) -> keep post_offset of safety tail. Never cut on the -47 crossing."""
    bb, hf, t = B
    lo, hi, ws, we = p["lo"], p["hi"], p["ws"], p["we"]
    m = (t >= lo) & (t <= hi)
    tt, dd = t[m], bb[m]
    loud = dd > S["thresh_db"]
    c_on = loud & (tt >= ws - S["onset_slack"])
    c_off = loud & (tt <= we + S["offset_slack"])
    if not c_on.any() or not c_off.any():
        raise SystemExit(f"piece {p.get('label')}: no audio above {S['thresh_db']} dB in window")
    on = float(tt[np.argmax(c_on)])
    off = float(tt[len(tt) - 1 - np.argmax(c_off[::-1])])
    fl_in, fl_out = F.at(on), F.at(off)
    att = attack_start(bb, hf, t, on, lo, fl_in, S)
    dec, why = decay_end(bb, hf, t, off, hi, fl_out, S)
    if why == "bound":
        # hi was placed at the dip before the next word (no silence between them in the source):
        # if the source gets loud right after hi, this is a next-word ending, not a cut-off tail
        k0 = int(np.searchsorted(t, hi)); k1 = int(np.searchsorted(t, hi + 0.04))
        if k1 > k0 and bb[k0:k1].max() > max(S["thresh_db"], bb[max(0, k0 - 2)] + S.get("rise_db", 8.0) / 2):
            why = "next-word"
    s = max(lo, att - S["pre_onset"])
    e = min(hi, dec + S["post_offset"])
    if why == "next-word":  # the next word follows directly: end at the dip, no handle into it
        e = min(e, dec + 0.005)
    cuts, pauses = [[s, e]], []
    if p.get("compress", True):
        mm = (t >= s) & (t <= e)
        t2, d2 = t[mm], bb[mm]
        quiet = d2 < S["thresh_db"]
        cuts, cur, i = [], s, 0
        k1, k2 = S.get("pause_keep_tail", 0.05), S["pre_onset"] + 0.01
        while i < len(t2):
            if quiet[i]:
                j = i
                while j < len(t2) and quiet[j]:
                    j += 1
                a, b = float(t2[i]), float(t2[j - 1])
                if b - a >= S["pause_min"] and a > att + 0.05 and b < off - 0.05:
                    fl = F.at(a)
                    a2, _ = decay_end(bb, hf, t, a, b, fl, S)
                    b2 = attack_start(bb, hf, t, b, a2, fl, S)
                    if (b2 - k2) - (a2 + k1) > 0.02:
                        cuts.append([cur, a2 + k1]); cur = b2 - k2
                        pauses.append([round(a2, 3), round(b2, 3)])
                i = j
            else:
                i += 1
        cuts.append([cur, e])
    info = {"on": round(on, 4), "off": round(off, 4), "attack": round(att, 4), "decay": round(dec, 4),
            "decay_why": why, "floor_out": [round(x, 1) for x in fl_out], "pauses": pauses}
    if why == "next-word":
        # cut at the dip with a short fade so the word ends like a natural decay instead of a click,
        # and no crossfade spill into the next word (assemble_audio zeroes past the cut end)
        info["fade_out"] = S.get("bounded_fade", 0.025)
    if bb[max(0, int(np.searchsorted(t, s)) - 2)] > S["thresh_db"] - 3:
        info["fade_in"] = 0.012  # in-point right after other speech: short fade-in, no spill
    return info, cuts


def tight_edges(B, p, S, F, info, cuts):
    """v9 tail_mode "tight" (hand-made re-cut style): butt-cut ON the word. Out-point = last frame where the
    tail is still tight_edge_db above the room tone (max(local floor, thresh-6)), searched back from the
    -47 dB crossing at most tight_max_trim; in-point = first frame where the attack reaches that level.
    A word that never gets that loud keeps its -47 dB crossing (a quiet word is never dropped).
    In-line pause cuts from snap_decay are kept. Returns (info, cuts) with the outer edges replaced."""
    bb, hf, t = B
    on, off, mt, th = info["on"], info["off"], S.get("tight_max_trim", 0.2), S["thresh_db"]
    lv_o = max(F.at(off)[0], th - 6) + S.get("tight_edge_db", 15.0)
    lv_i = max(F.at(on)[0], th - 6) + S.get("tight_edge_db", 15.0)
    m = np.where((t >= off - mt) & (t <= off) & (bb > lv_o))[0]
    e = (float(t[m[-1]]) if len(m) else off) + S.get("tight_out_offset", 0.0)
    m = np.where((t >= on) & (t <= on + mt) & (bb > lv_i))[0]
    s = (float(t[m[0]]) if len(m) else on) + S.get("tight_in_offset", 0.0)
    if info["decay_why"] == "next-word":
        e = min(e, info["decay"] + 0.005)
    s, e = max(s, p["lo"]), min(e, p["hi"])
    cuts = [list(c) for c in cuts]
    cuts[0][0] = s; cuts[-1][1] = e
    cuts = [c for c in cuts if c[1] - c[0] > 0.02]
    info = dict(info, tight={"out_vs_thresh": round(e - off, 4), "in_vs_thresh": round(s - on, 4),
                             "edge_db": [round(lv_o, 1), round(lv_i, 1)]})
    info.pop("fade_in", None)
    if bb[max(0, int(np.searchsorted(t, s)) - 4)] > th:  # previous word runs into this in-point
        info["fade_in"] = 0.005
    return info, cuts


def fit_frames_tight(s, e, lo, hi, fps):
    """Nearest whole-frame duration, adjusted on the tail (at most half a frame), inside lo/hi."""
    n = max(1, round((e - s) * fps)); d = n / fps
    if s + d > hi + 0.004:
        s = max(lo, hi - d)
    return [s, s + d]


def fit_frames_grow(s, e, lo, hi, fps, decay, attack, post_min=0.065):
    """Whole-frame cut that never eats into a word. Round to the nearest frame count; a shrink
    comes off the safety tail only while it stays >= post_min after the decay, otherwise the cut
    grows instead. Growth goes on the tail (room tone after the decay) when hi allows, else on the
    head. If both bounds pin the cut it shrinks from the head handle (never past `attack`) and
    then from the tail."""
    n = round((e - s) * fps)
    if n / fps < e - s and e - (e - s - n / fps) >= decay + post_min - 1e-9:
        return [s, s + n / fps]
    hi = hi + 0.004  # a few ms past a bound is inaudible; shrinking a whole frame off a word is not
    n = math.ceil((e - s) * fps - 1e-6)
    d = n / fps; ex = d - (e - s)
    if e + ex <= hi + 1e-9:
        return [s, e + ex]
    if s - ex >= lo - 1e-9:
        return [s - ex, e]
    room_t, room_h = hi - e, s - lo
    if room_t + room_h >= ex - 1e-9:
        return [s - (ex - room_t), hi]
    d = (n - 1) / fps; cut = (e - s) - d
    s2 = s + min(cut, max(0.0, attack - s))
    return [s2, s2 + d]

def fit_frames(s, e, lo, hi, fps, level=None):
    """Make a cut a whole number of frames without snapping each edge to the frame grid
    (that could add or remove up to a frame of silence at every join). Growing is split over
    both edges (<= half a frame each); shrinking comes off the quieter edge (`level(t)` = dB),
    so a cut that starts on a word attack is never shaved. Stays inside [lo, hi]."""
    n = max(1, round((e - s) * fps))
    if n / fps > hi - lo + 1e-9:
        n = max(1, int((hi - lo) * fps))
    d = n / fps
    if d < e - s and level is not None:
        s2 = s if level(e) <= level(s) else e - d
    else:
        s2 = (s + e) / 2 - d / 2
    s2 = min(max(s2, lo), hi - d)
    return [s2, s2 + d]


def prepare_inserts(P):
    """Insert pieces: {"label": "INS1", "insert": true, "audio": "/abs/line.wav", "video": "/abs/broll.MOV",
    "video_start": 12.3, "text": "...", optional "video_x" (0-1 horizontal crop center), "video_zoom"}.
    A line recorded elsewhere (e.g. a pickup line recorded later) dropped in between two pieces with
    B-roll picture over it. If the audio file does not exist yet the piece is skipped with a notice, so
    the same plan builds now and, once the file is dropped at that path, with one rebuild.
    The audio becomes source "INS_<label>", padded with 0.5s of silence (settings.source_pad) so the
    edge finder can keep its handles; lo/hi/ws/we default to the whole file."""
    keep = []
    S = P["settings"]; S.setdefault("source_pad", {}); S.setdefault("insert_sources", [])
    for p in P["pieces"]:
        if not p.get("insert"):
            keep.append(p); continue
        if not p.get("audio") or not os.path.isfile(p["audio"]):
            print(f"insert {p.get('label')}: PENDING, audio {p.get('audio')} not found -> skipped "
                  f"(drop the file there and rerun plan/assemble/build)")
            continue
        if not p.get("video") or not os.path.isfile(p["video"]):
            raise SystemExit(f"insert {p.get('label')}: B-roll video {p.get('video')} not found")
        k = p.get("src") or f"INS_{p.get('label', len(keep))}"
        pad = 0.5
        P["sources"][k] = p["audio"]; S["source_pad"][k] = pad
        if k not in S["insert_sources"]:
            S["insert_sources"].append(k)
        dur = len(load_audio(p["audio"], channels=1)) / 48000
        p.setdefault("lo", 0.0); p.setdefault("hi", dur + 2 * pad)
        p.setdefault("ws", pad); p.setdefault("we", pad + dur)
        p["src"] = k
        keep.append(p)
        print(f"insert {p.get('label')}: {p['audio']} ({dur:.2f}s) with B-roll {p['video']} @ {p.get('video_start', 0)}s")
    P["pieces"] = keep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--out", default="cuts.json")
    ap.add_argument("--peek", nargs="+", metavar="SRC LO HI",
                    help="SRC LO HI [LO HI ...]: print the envelope (time:dB every --step s) and exit")
    ap.add_argument("--step", type=float, default=0.04)
    ap.add_argument("--bursts", metavar="SRC", help="list every voiced burst in SRC (merged across gaps < "
                    "--gap s) for the retake audit, then exit; probe each one with probe_regions.py")
    ap.add_argument("--gap", type=float, default=0.5)
    a = ap.parse_args()
    P = load_plan(a.plan); S = P["settings"]; fps = S["fps"]
    q = lambda x: round(x * fps) / fps
    prepare_inserts(P)
    used = {a.bursts} if a.bursts else ({a.peek[0]} if a.peek else {p["src"] for p in P["pieces"]})
    E = {}
    for k in used:
        x = load_source(P, k, channels=1, highpass=S["highpass"])
        E[k] = envelope(x, win=S["env_win"], hop=S["env_hop"])
    if a.bursts:
        db, t = E[a.bursts]; loud = db > S["thresh_db"]; segs = []; i = 0
        while i < len(db):
            if loud[i]:
                j = i
                while j < len(db) and loud[j]:
                    j += 1
                if segs and t[i] - segs[-1][1] < a.gap:
                    segs[-1][1] = t[j - 1]; segs[-1][2] = max(segs[-1][2], db[i:j].max())
                else:
                    segs.append([t[i], t[j - 1], db[i:j].max()])
                i = j
            else:
                i += 1
        for s0, s1, pk in segs:
            if s1 - s0 >= 0.06:
                print(f"{s0:7.2f}-{s1:7.2f} ({s1 - s0:.2f}s) peak {pk:.0f} dB")
        print("ranges for probe_regions.py:", " ".join(f"{max(0, s0 - 0.15):.2f}-{s1 + 0.15:.2f}" for s0, s1, _ in segs if s1 - s0 >= 0.06))
        return
    if a.peek:
        bb, hf, t = band_envelopes(P, a.peek[0], S); F = Floors(bb, hf, t, S)
        k = max(1, round(a.step / S["env_hop"]))
        for lo, hi in zip(a.peek[1::2], a.peek[2::2]):
            m = (t >= float(lo)) & (t <= float(hi))
            fb, fh = F.at((float(lo) + float(hi)) / 2)
            print(f"== {a.peek[0]} {lo}-{hi}  time:broadband/2-8kHz dB  (local floor {fb:.0f}/{fh:.0f}; "
                  f"'decayed' = both below {fb + S['decay_margin_db']:.0f}/{fh + S['decay_margin_db']:.0f})")
            print(" ".join(f"{x:.2f}:{int(y)}/{int(z)}" for x, y, z in list(zip(t[m], bb[m], hf[m]))[::k]))
        return
    if S.get("tail_mode", "tight") == "thresh":
        return legacy(P, S, E, a, fps, q)
    # ---- v8: decay-aware edges, inserts, join top-up ----
    B = {k: band_envelopes(P, k, S) for k in P["sources"] if any(pp["src"] == k for pp in P["pieces"])}
    Fl = {k: Floors(*B[k], S) for k in B}
    tight = S.get("tail_mode", "tight") == "tight"
    out, zi = [], 0
    for p in P["pieces"]:
        bb, hf, t = B[p["src"]]
        if p.get("pin"):
            # pinned cuts: no envelope snapping. For a source that is already an edit (a hand-made
            # re-cut, where every in-line join is final) or an insert that must play whole ("whole").
            pc = [[p["ws"], p["we"]]] if p["pin"] == "whole" else p["pin"]
            cuts = [[q(x), q(y)] for x, y in pc]
            info = {"on": cuts[0][0], "off": cuts[-1][1], "attack": cuts[0][0], "decay": cuts[-1][1],
                    "decay_why": "pinned", "pauses": [], "fade_in": p.get("fade_in", 0.005),
                    "fade_out": p.get("fade_out", 0.005),
                    "tight": {"out_vs_thresh": 0.0, "in_vs_thresh": 0.0, "edge_db": "pinned"}}
        else:
            info, cuts = snap_decay(B[p["src"]], p, S, Fl[p["src"]])
        if p.get("pin"):
            pass
        elif tight:
            info, cuts = tight_edges(B[p["src"]], p, S, Fl[p["src"]], info, cuts)
            cuts = [fit_frames_tight(x, y, p["lo"], p["hi"], fps) for x, y in cuts]
        else:
            cuts = [fit_frames_grow(x, y, p["lo"], p["hi"], fps, info["decay"], info["attack"],
                                    p.get("post_min", S.get("post_min", 0.065)))
                    for x, y in cuts]
        if p.get("insert"):
            zoom = p.get("zoom", 1.0)
        else:
            zoom = p.get("zoom", S["zooms"][zi % len(S["zooms"])]); zi += 1
        dur = sum(y - x for x, y in cuts)
        tail = cuts[-1][1] - info["decay"]; head = info["attack"] - cuts[0][0]
        print(f"{p.get('label',''):>10} {p['src']} whisper {p['ws']:.2f}-{p['we']:.2f} env {info['on']:.3f}-{info['off']:.3f}"
              f" attack {info['attack']:.3f} decay {info['decay']:.3f} ({info['decay_why']}, +{(info['decay']-info['off'])*1000:.0f}ms"
              f" past thresh) -> {len(cuts)} cut(s) {cuts[0][0]:.3f}-{cuts[-1][1]:.3f} ({dur:.2f}s) head {head*1000:.0f}ms tail {tail*1000:.0f}ms")
        if tight:
            print(f"   tight: out {info['tight']['out_vs_thresh']*1000:+.0f}ms vs -47 crossing, in "
                  f"{info['tight']['in_vs_thresh']*1000:+.0f}ms vs -47 onset (edge level {info['tight']['edge_db']})")
        elif info["decay_why"] in ("bound", "max"):
            print(f"   WARN tail still decaying at the {'hi bound' if info['decay_why']=='bound' else 'decay_max'}: "
                  f"move hi out if the next attempt allows, never shorten a word ending")
        if info["decay_why"] == "next-word" and not tight:
            print(f"   NOTE next word follows directly ({info['decay']:.3f}): out-point at the dip, no safety tail; "
                  f"the join gap comes from the neighbour")
        if info["attack"] - p["lo"] < 0.01 and not p.get("pin"):
            print(f"   WARN in-point hugs lo bound; check the first word is not clipped")
        o = {"label": p.get("label", ""), "src": p["src"], "zoom": zoom, "text": p.get("text", ""), "cuts": cuts,
             "edge": {"head": round(head, 4), "tail": round(tail, 4), **info}}
        for kk in ("crop", "track", "face", "insert", "video", "video_start", "video_x", "video_zoom",
                   "story", "story_key"):
            if kk in p:
                o[kk] = p[kk]
        o["_lo"], o["_hi"] = p["lo"], p["hi"]
        out.append(o)
    # join top-up: every join gets at least join_gap[0] of true silence (prev room tone after its
    # decay + next room tone before its attack). Short sides (a word running straight into the next
    # attempt) are topped up from the neighbour's quiet room tone, whole frames, inside lo/hi.
    f1 = 1 / fps; gmin, gmax = S.get("tight_join_gap", [0.0, 0.04]) if tight else S.get("join_gap", [0.06, 0.11])
    def is_quiet(k, a0, a1):
        bb, hf, t = B[k]; m = (t >= a0) & (t <= a1)
        if not m.any():
            return True
        fl = Fl[k].at((a0 + a1) / 2)
        return bool(quiet_mask(bb[m], hf[m], fl, S).mean() > 0.8)
    for A_, Bp in ([] if tight else zip(out, out[1:])):  # tight mode: butt cuts, no top-up
        for _ in range(4):
            g = A_["edge"]["tail"] + Bp["edge"]["head"]
            if g >= gmin:
                break
            s0 = Bp["cuts"][0][0]; e0 = A_["cuts"][-1][1]
            if A_["edge"]["decay_why"] != "next-word" and e0 + f1 <= A_["_hi"] + 1e-6 and is_quiet(A_["src"], e0, e0 + f1):
                A_["cuts"][-1][1] += f1; A_["edge"]["tail"] = round(A_["edge"]["tail"] + f1, 4)
            elif s0 - f1 >= Bp["_lo"] - 1e-6 and is_quiet(Bp["src"], s0 - f1, s0):
                Bp["cuts"][0][0] -= f1; Bp["edge"]["head"] = round(Bp["edge"]["head"] + f1, 4)
            else:
                print(f"   WARN join {A_['label']} -> {Bp['label']}: only {g*1000:.0f}ms of silence available"); break
    print(f"\n== joins: true silence = prev room tone after its decay + next room tone before its attack "
          f"(target {gmin*1000:.0f}-{gmax*1000:.0f} ms)" + (" -- tight mode: negative = the cut sits on the word "
          "(butt cut), see 'tight:' lines for the edge vs the -47 dB crossing" if tight else ""))
    for A_, Bp in zip(out, out[1:]):
        g = A_["edge"]["tail"] + Bp["edge"]["head"]
        print(f"  {A_['label']:>6} -> {Bp['label']:<6} {g*1000:4.0f} ms  (tail {A_['edge']['tail']*1000:.0f} + head "
              f"{Bp['edge']['head']*1000:.0f}){'  <-- long' if g > gmax + 0.02 else ''}{'  <-- short' if g < gmin - 0.005 and not tight else ''}")
    for o in out:
        o.pop("_lo"); o.pop("_hi")
    json.dump({"sources": P["sources"], "settings": S, "pieces": out}, open(a.out, "w"), indent=1)
    print(f"wrote {a.out}; total {sum(y-x for p in out for x,y in p['cuts']):.2f}s")


def legacy(P, S, E, a, fps, q):
    """tail_mode "thresh": v2-v7 edge logic (cut near the -47 dB crossing). Kept so
    older plans (and plan.example.json, which pins it) reproduce their original exports."""
    floors = {k: float(np.percentile(E[k][0], 10)) for k in E}
    out, zi = [], 0
    for p in P["pieces"]:
        db, t = E[p["src"]]
        on, off, son, soff, cuts = snap(db, t, p, S, floors[p["src"]])
        if S.get("quantize", "duration") == "edges":  # legacy: snap each edge to the frame grid
            qs = lambda x: q(x) if q(x) >= p["lo"] - 1e-6 else math.ceil(x * fps) / fps
            qe = lambda y: q(y) if q(y) <= p["hi"] + 1e-6 else math.floor(y * fps) / fps
            cuts = [[qs(x), qe(y)] for x, y in cuts if qe(y) > qs(x)]
        else:
            lv = lambda tt_, db=db, t=t: float(db[min(len(t) - 1, int(np.searchsorted(t, tt_)))])
            cuts = [fit_frames(x, y, p["lo"], p["hi"], fps, lv) for x, y in cuts]
        zoom = p.get("zoom", S["zooms"][zi % len(S["zooms"])]); zi += 1
        dur = sum(y - x for x, y in cuts)
        print(f"{p.get('label',''):>10} {p['src']} whisper {p['ws']:.2f}-{p['we']:.2f} "
              f"env {on:.3f}-{off:.3f} (soft {son:.3f}-{soff:.3f}) -> {len(cuts)} cut(s) "
              f"{cuts[0][0]:.3f}-{cuts[-1][1]:.3f} ({dur:.2f}s)")
        if on - p["lo"] < 0.03:
            print(f"   WARN onset hugs lo bound; check that the word start is not clipped")
        if p["hi"] - off < 0.04:
            print(f"   WARN offset hugs hi bound; check that a soft ending is not clipped")
        long_p = [(round(y0, 2), round(x1 - y0, 3)) for (x0, y0), (x1, y1) in zip(cuts, cuts[1:])]
        edge = {"head": round(on - cuts[0][0], 4), "tail": round(cuts[-1][1] - off, 4),
                "soft_head": round(on - son, 4), "soft_tail": round(soff - off, 4)}
        out.append({"label": p.get("label", ""), "src": p["src"], "zoom": zoom,
                    "text": p.get("text", ""), "cuts": cuts, "edge": edge})
    # joins that ended up (nearly) on sound because a bound forced it get one frame of silence
    # back from whichever side has room (next piece's lo, else previous piece's hi)
    for a_, b_, pa, pb in zip(out, out[1:], P["pieces"], P["pieces"][1:]):
        f1 = 1 / fps
        for _ in range(3):  # up to 3 frames
            g = a_["edge"]["tail"] + b_["edge"]["head"]
            if g >= S.get("join_min", 0.025):
                break
            if b_["cuts"][0][0] - f1 >= pb["lo"] - 1e-6:
                b_["cuts"][0][0] -= f1; b_["edge"]["head"] = round(b_["edge"]["head"] + f1, 4)
            elif a_["cuts"][-1][1] + f1 <= pa["hi"] + 1e-6:
                a_["cuts"][-1][1] += f1; a_["edge"]["tail"] = round(a_["edge"]["tail"] + f1, 4)
            else:
                break
    print("\n== joins: residual time below thresh spliced in (prev tail + next head; incl. soft tail/attack)")
    for a_, b_ in zip(out, out[1:]):
        g = a_["edge"]["tail"] + b_["edge"]["head"]
        sil = g - a_["edge"]["soft_tail"] - b_["edge"]["soft_head"]
        print(f"  {a_['label']:>6} -> {b_['label']:<6} {g*1000:4.0f} ms below {S['thresh_db']:.0f} dB "
              f"(true silence after soft edges {sil*1000:3.0f} ms){'  <-- long' if g > 0.12 else ''}")
    json.dump({"sources": P["sources"], "settings": S, "pieces": out}, open(a.out, "w"), indent=1)
    print(f"wrote {a.out}; total {sum(y-x for p in out for x,y in p['cuts']):.2f}s")




if __name__ == "__main__":
    main()
