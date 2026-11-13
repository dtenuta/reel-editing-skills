#!/usr/bin/env python3
"""Verify a music + SFX mix against the speech-only reel it was built on.

    music_verify.py --ref <captioned_no_music.mp4> --out <with_music.mp4> --stems <music_mix out dir>
                    --words <words_raw.json> --qc <dir> [--whisper-py <python with faster-whisper>]

Checks (exits 1 and prints FAIL on any hard failure):
  * loudness: integrated -14 +-0.5 LUFS, true peak <= -1.0 dBTP (ffmpeg ebur128 on the final file)
  * clipping: no decoded sample at or above 0.999
  * timing: the speech is not shifted (cross-correlation lag 0 +-1 sample against the reference)
  * speech level: per sentence, the speech recovered from the final mix (final - music - sfx) matches the
    reference times the master gain within 0.5 dB; the raw final-vs-reference per-sentence delta (music
    included) is also reported. A null residual (final - (ref*G + music + sfx)) shows codec noise only.
  * demo ducks: under each audible product demo (plan_resolved duck_windows) the bed is extra_db (+-2) below its
    section level; those spans are left out of the section and style bed checks
  * demo segment audibility (v6): every labelled segment of a promptDemo row (e.g. sad / excited! in an emotion arc)
    must sit within 6 LU of the speaker's normal speech (median sentence loudness outside the demos), measured on the speech
    stem. Emotion voice models can render sad / deflated parts 13-20 dB under excited ones; in one reel the calm half
    sat 7-8 dB under the voice and was hard to hear on a phone. Fix it in the clip (per-segment gain riding), not in the mix.
  * voice over music per sentence (the goal is that the voice can always be heard well): the
    section and style checks only look at the average, and the house style is a steady un-ducked bed ~13 dB under
    the voice, so one quiet sentence can sink into it. Every sentence's speech (reference x master gain, while the
    speaker talks) must stay >= --min-voice-over-music dB (default 6) over the music under it. Fix a failure upstream
    (talking-head-edit speech leveling / per-segment gain), not by pulling the bed down everywhere.
  * BEFORE -> AFTER switch (v7, warning): every gap between a promptDemo's before and after clip (plan_resolved
    ba_gaps) has an SFX hit, so a repeated line reads as two takes and not as an editing glitch
  * music under speech per section: ducked music LUFS while the speaker talks (target -30..-26) and in gaps
  * intelligibility (with --whisper-py): faster-whisper on reference and final; WER of each vs words_raw.json
    must be within 2 points of each other
  * style (with --preset, or the preset music_mix.py recorded from the plan's "style"): the mix must sit inside the
    reference creator's measured house style (music_presets/<name>.json, made by ref_audio_profile.py): bed level
    under speech and below the voice, tempo, number of bed switches and mid-reel stops, SFX per minute and SFX level
    vs the voice. This is the check that catches an over-scored mix (e.g. 3 tracks / 2 switches, 16 SFX in 60 s).
    The per-section music range comes from the preset too (default -30..-26 LUFS when there is no preset).
  * writes <qc>/music_sections.png: waveform + music/speech level + spectrogram with sections and SFX marked
"""
import argparse, json, os, re, subprocess, sys
import numpy as np
from scipy import signal
from scipy.io import wavfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from music_mix import SR, decode, lufs, kweight, _blocks, true_peak_db  # noqa: E402


def ebur(path):
    r = subprocess.run(["ffmpeg", "-nostats", "-i", path, "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    tail = r[r.rfind("Summary:"):]
    g = lambda k: float(re.search(k + r":\s+(-?[\d.]+)", tail).group(1))
    return {"I": g("I"), "LRA": g("LRA"), "TP": g("Peak")}


def lag_samples(a, b, maxlag=2400):
    a = a.mean(1)[:SR * 20]; b = b.mean(1)[:SR * 20]
    n = min(len(a), len(b))
    c = signal.correlate(b[:n], a[:n], mode="full", method="fft")
    mid = n - 1
    w = c[mid - maxlag:mid + maxlag + 1]
    return int(np.argmax(w) - maxlag)


def seg_lufs(x):
    """Ungated K-weighted loudness of a short segment (sentence-level comparison)."""
    z = kweight(x)
    return float(-0.691 + 10 * np.log10(np.sum(np.mean(z ** 2, 0)) + 1e-15))


def sentences(words, gap=0.25):
    out = []
    for w in words:
        if out and w["s"] - out[-1][1] < gap and not out[-1][2].rstrip().endswith((".", "?", "!")):
            out[-1][1] = w["e"]; out[-1][2] += " " + w["w"]
        else:
            out.append([w["s"], w["e"], w["w"]])
    return out


def norm_words(text):
    t = text.lower().replace("-", " ")
    return [w for w in re.sub(r"[^a-z0-9' ]+", " ", t).split() if w]


def wer(ref, hyp):
    d = np.arange(len(hyp) + 1)
    for i, r in enumerate(ref, 1):
        prev, d = d, np.empty_like(d)
        d[0] = i
        for j, h in enumerate(hyp, 1):
            d[j] = min(prev[j] + 1, d[j - 1] + 1, prev[j - 1] + (r != h))
    return d[-1] / max(1, len(ref))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--stems", required=True); ap.add_argument("--words", required=True)
    ap.add_argument("--qc", required=True); ap.add_argument("--whisper-py")
    ap.add_argument("--label", default="music")
    ap.add_argument("--preset", help="style preset json (default: the one recorded in plan_resolved.json)")
    ap.add_argument("--min-voice-over-music", type=float, default=6.0,
                    help="per-sentence floor for speech minus music under it, dB (default 6)")
    x = ap.parse_args()
    os.makedirs(x.qc, exist_ok=True)
    res = json.load(open(os.path.join(x.stems, "plan_resolved.json")))
    words = json.load(open(x.words)); words = words if isinstance(words, list) else words["words"]
    G = 10 ** (res["master_gain_db"] / 20)
    ref = decode(x.ref); out = decode(x.out)
    _, mus = wavfile.read(os.path.join(x.stems, "music_stem.wav")); _, sfx = wavfile.read(os.path.join(x.stems, "sfx_stem.wav"))
    n = min(len(ref), len(out), len(mus))
    ref, out, mus, sfx = ref[:n], out[:n], mus[:n].astype(np.float64), sfx[:n].astype(np.float64)
    rep, fails = {}, []

    e = ebur(x.out); rep["loudness"] = e
    if abs(e["I"] + 14) > 0.5: fails.append(f"integrated {e['I']} LUFS")
    if e["TP"] > -1.0: fails.append(f"true peak {e['TP']} dBTP")
    clip = int(np.sum(np.abs(out) >= 0.999)); rep["clipped_samples"] = clip
    rep["sample_peak_dbfs"] = round(float(20 * np.log10(np.abs(out).max())), 2)
    if clip: fails.append(f"{clip} clipped samples")

    lag = lag_samples(ref, out); rep["speech_lag_samples"] = lag
    if abs(lag) > 1: fails.append(f"speech shifted by {lag} samples")
    if lag:
        out = np.roll(out, -lag)
    speech_est = out - mus - sfx
    resid = out - (ref * G + mus + sfx)
    rep["null_residual_db_vs_speech"] = round(lufs(resid) - lufs(ref * G), 1)

    rows = []
    for a, b, txt in sentences(words):
        i, j = int(a * SR), int(b * SR)
        if j - i < SR // 4:
            continue
        r0 = seg_lufs(ref[i:j]); r1 = seg_lufs(speech_est[i:j]); r2 = seg_lufs(out[i:j])
        sos = signal.butter(4, [300 / (SR / 2), 3400 / (SR / 2)], "band", output="sos")
        bb = lambda y: seg_lufs(signal.sosfilt(sos, y, axis=0))
        rows.append({"t": [round(a, 2), round(b, 2)], "text": txt[:60],
                     "speech_delta_db": round(r1 - r0, 2), "raw_delta_db": round(r2 - r0, 2),
                     "speechband_raw_delta_db": round(bb(out[i:j]) - bb(ref[i:j]), 2)})
    rep["per_sentence"] = rows
    worst_speech = max(abs(r["speech_delta_db"]) for r in rows)
    worst_raw = max(abs(r["raw_delta_db"]) for r in rows)
    worst_band = max(abs(r["speechband_raw_delta_db"]) for r in rows)
    rep["speech_delta_max_db"] = worst_speech; rep["raw_delta_max_db"] = worst_raw
    rep["speechband_raw_delta_max_db"] = worst_band
    if worst_speech > 0.5: fails.append(f"speech level changed by up to {worst_speech} dB")
    if worst_band > 0.5: fails.append(f"speech-band level (with music) changed by up to {worst_band} dB")

    iv = res["speech_intervals"]
    act = np.zeros(n, bool)
    for a, b in iv:
        act[int(a * SR):int(b * SR)] = True
    # audible product demos get an extra music duck (music_mix duck_windows): judge the bed on normal talking only,
    # and check separately that the bed really drops under each demo
    dws = res.get("duck_windows", [])
    dmask = np.zeros(n, bool)
    for w in dws:
        dmask[int(w["start"] * SR):int(min(n / SR, w["end"] + 0.25) * SR)] = True
    act_all = act
    act = act & ~dmask
    # voice over music per sentence: can every sentence be heard over the bed playing under it
    vom = []
    for a, b, txt in sentences(words):
        i, j = int(a * SR), int(b * SR)
        sel = act_all[i:j]
        if j - i < SR // 4 or sel.sum() < SR // 5:
            continue
        d = seg_lufs(ref[i:j][sel] * G) - seg_lufs(mus[i:j][sel])
        vom.append({"t": [round(a, 2), round(b, 2)], "text": txt[:60], "voice_over_music_db": round(d, 1)})
    rep["voice_over_music"] = {"min_db": min((r["voice_over_music_db"] for r in vom), default=None),
                               "median_db": round(float(np.median([r["voice_over_music_db"] for r in vom])), 1) if vom else None,
                               "floor_db": x.min_voice_over_music,
                               "low": [r for r in vom if r["voice_over_music_db"] < x.min_voice_over_music]}
    for r in rep["voice_over_music"]["low"]:
        fails.append(f"sentence {r['t'][0]:.2f}-{r['t'][1]:.2f} only {r['voice_over_music_db']} dB over the music "
                     f"(min {x.min_voice_over_music}): level the speech upstream, '{r['text'][:30]}'")
    preset = load_preset(x.preset or res.get("style_preset"), x.stems)
    lo_hi = ((preset or {}).get("mix", {}).get("section_level_range") or (preset or {}).get("bed", {}).get("section_level_range")
             or [-30.0, -26.0])
    secs = []
    for s in res["sections"]:
        i, j = int(s["start"] * SR), int(s["end"] * SR)
        gap = ~act_all[i:j] & ~dmask[i:j]
        m_on = lufs(mus[i:j][act[i:j]]); m_off = lufs(mus[i:j][gap]) if gap.sum() > SR * 0.5 else None
        sp = lufs(ref[i:j][act[i:j]] * G)
        secs.append({"track": s["track"], "span": [s["start"], s["end"]], "tone": s.get("tone"),
                     "music_under_speech_lufs": round(m_on, 1),
                     "music_in_gaps_lufs": round(m_off, 1) if m_off is not None else None,
                     "speech_lufs": round(sp, 1), "speech_to_music_db": round(sp - m_on, 1)})
        if not lo_hi[0] - 0.5 <= m_on <= lo_hi[1] + 0.5:
            fails.append(f"section {s['track']} music {m_on:.1f} LUFS under speech (range {lo_hi[0]}..{lo_hi[1]})")
    rep["music_sections"] = secs
    demo = []
    for w in dws:
        i, j = int(w["start"] * SR), int(w["end"] * SR)
        sel = act_all[i:j]
        if sel.sum() < SR // 4:
            continue
        m_w = lufs(mus[i:j][sel]); sp_w = lufs(ref[i:j][sel] * G)
        base = next((r["music_under_speech_lufs"] for r in secs if r["span"][0] <= w["start"] < r["span"][1]), None)
        drop = None if base is None else round(base - m_w, 1)
        demo.append({"label": w.get("label"), "span": [w["start"], w["end"]], "extra_db": w["extra_db"],
                     "music_lufs": round(m_w, 1), "drop_vs_section_db": drop, "demo_to_music_db": round(sp_w - m_w, 1)})
        if drop is not None and drop < w["extra_db"] - 2.0:
            fails.append(f"demo duck {w.get('label')}: music only {drop} dB down (want ~{w['extra_db']})")
    rep["demo_ducks"] = demo
    # demo segment audibility: each labelled segment vs the speaker's normal speech (sentences outside every demo window)
    norm = [seg_lufs(ref[int(a * SR):int(b * SR)] * G) for a, b, _ in sentences(words)
            if b - a >= 0.5 and not any(w["start"] - 0.1 < a < w["end"] + 0.1 for w in dws)]
    speech_med = float(np.median(norm)) if norm else None
    segrep = []
    for w in dws:
        for g in w.get("segments") or []:
            i, j = int(g["start"] * SR), int(g["end"] * SR)
            sel = act_all[i:j]
            if speech_med is None or j - i < SR // 4 or sel.sum() < SR // 5:
                continue
            lv = seg_lufs(ref[i:j][sel] * G)
            segrep.append({"demo": w.get("label"), "label": g["label"], "span": [g["start"], g["end"]],
                           "lufs": round(lv, 1), "vs_speech_db": round(lv - speech_med, 1)})
            if lv - speech_med < -6.0:
                fails.append(f"demo {w.get('label')} segment '{g['label']}' {g['start']:.2f}-{g['end']:.2f} is "
                             f"{speech_med - lv:.1f} dB under the speech (max 6): ride that segment up in the clip")
    rep["demo_segments"] = {"speech_median_lufs": None if speech_med is None else round(speech_med, 1), "segments": segrep}
    # before -> after switch SFX (v7): every gap between a promptDemo's before and after clip carries a transition hit
    # (music_mix whoosh), so a repeated line reads as two takes, not a glitch (a sponsor's reviewer heard one as a glitch)
    warns = []
    for g in res.get("ba_gaps", []):
        hit = [h for h in res.get("sfx", []) if g["start"] - 0.15 <= h["t"] <= g["end"] + 0.15]
        g["sfx"] = [f"{h['kind']}@{h['t']}" for h in hit]
        if not hit:
            warns.append(f"no SFX in the BEFORE -> AFTER gap of {g['demo']} ({g['start']:.2f}-{g['end']:.2f} s): add the "
                         "switch whoosh (music_mix does it unless switchSfx is false or the demo is skipped)")
    rep["ba_gaps"] = res.get("ba_gaps", [])
    rep["warnings"] = warns
    for w in warns:
        print("WARN", w)
    if preset:
        rep["style"] = style_check(preset, res, mus, sfx, ref * G, act, n)
        fails += [f"style: {f}" for f in rep["style"]["fails"]]

    if x.whisper_py:
        tr = os.path.join(HERE, "transcribe.py")
        refw = " ".join(w["w"] for w in words)
        w = {}
        for tag, path in (("reference", x.ref), ("final", x.out)):
            j = os.path.join(x.qc, f"whisper_{tag}.json")
            if not os.path.exists(j):
                subprocess.run([x.whisper_py, "-I", tr, path, j], check=True, capture_output=True)
            d = json.load(open(j)); d = d if isinstance(d, list) else d["words"]
            w[tag] = round(float(wer(norm_words(refw), norm_words(" ".join(t["w"] for t in d)))), 4)
        rep["wer_vs_words_raw"] = w
        if w["final"] - w["reference"] > 0.02: fails.append(f"WER rose {w['reference']} -> {w['final']}")

    plot(x, res, ref * G, mus, sfx, out, act_all)
    rep["image"] = os.path.join(x.qc, f"{x.label}_sections.png")
    rep["fails"] = fails; rep["ok"] = not fails
    json.dump(rep, open(os.path.join(x.qc, f"{x.label}_verify.json"), "w"), indent=1)
    print(json.dumps({k: v for k, v in rep.items() if k != "per_sentence"}, indent=1))
    print("PASS" if not fails else "FAIL: " + "; ".join(fails))
    sys.exit(0 if not fails else 1)


def load_preset(p, stems_dir):
    if not p:
        return None
    if not os.path.exists(p):
        alt = os.path.join(os.path.dirname(HERE), "music_presets", p if p.endswith(".json") else p + ".json")
        p = alt if os.path.exists(alt) else os.path.join(stems_dir, p)
    d = json.load(open(p))
    return d.get("preset", d)


KIND_FAMILY = {"impact": "impact/boom", "thump": "impact/boom", "boom": "impact/boom", "boom_big": "impact/boom",
               "whoosh_boom": "impact/boom", "pop": "pop/click", "tick": "pop/click", "click": "pop/click",
               "ding": "ding/chime", "whoosh": "whoosh", "riser": "riser"}


def _in(v, r):
    return r is None or v is None or r[0] <= v <= r[1]


def style_check(preset, res, mus, sfx, sp, act, n):
    """Compare the mix with a reference creator's measured house style (ranges from ref_audio_profile.py)."""
    b, s = preset.get("bed", {}), preset.get("sfx", {})
    dur = n / SR
    out = {"preset": preset.get("name"), "fails": [], "warnings": []}
    on = lufs(mus[act]) if act.sum() > SR else lufs(mus)
    below = lufs(sp[act]) - on
    out["bed_under_speech_lufs"] = round(on, 1); out["bed_below_voice_db"] = round(below, 1)
    if not _in(on, b.get("level_range")):
        out["fails"].append(f"bed {on:.1f} LUFS under speech, preset {b['level_range']}")
    if not _in(below, b.get("below_voice_db_range")):
        out["fails"].append(f"bed {below:.1f} dB below the voice, preset {b['below_voice_db_range']}")
    # tempo of what actually plays: each section's track tempo after the grid stretch (half/double time allowed)
    rng = b.get("bpm_range")
    bpms = []
    for sec in res["sections"]:
        g = res["grids"].get(sec["grid"], {})
        bpm = g.get("bpm")
        if bpm:
            bpms.append(round(bpm, 1))
            msg = f"track {sec['track']} at {bpm:.1f} BPM, preset {rng}"
            if rng and not any(rng[0] - 1 <= v <= rng[1] + 1 for v in (bpm, bpm / 2, bpm * 2)) and msg not in out["fails"]:
                out["fails"].append(msg)
    out["section_bpm"] = bpms
    # switches = changes of track between consecutive sections; stops = music absent mid-reel >= 0.3 s
    order = [sec["track"] for sec in sorted(res["sections"], key=lambda r: r["start"])]
    sw = sum(1 for a, c in zip(order, order[1:]) if a != c)
    out["track_switches"] = sw; out["tracks_used"] = sorted(set(order))
    if sw > b.get("max_switches", 99):
        out["fails"].append(f"{sw} track switches, preset max {b['max_switches']}")
    hop = int(0.1 * SR)
    lev = -0.691 + 10 * np.log10(_blocks(kweight(mus), 0.4, 0.1) + 1e-15)
    body = np.median(lev[lev > -60]) if (lev > -60).any() else -70
    gone = lev < body - 20
    first = np.argmax(~gone); last = len(gone) - 1 - np.argmax((~gone)[::-1])
    stops, i = [], first
    while i < last:
        if gone[i]:
            j = i
            while j < last and gone[j]:
                j += 1
            if (j - i) * 0.1 >= 0.3:
                stops.append([round(float(i * 0.1 + 0.2), 1), round(float(j * 0.1 + 0.2), 1)])
            i = j
        i += 1
    out["mid_reel_stops"] = stops
    if len(stops) > b.get("max_stops", 99):
        out["fails"].append(f"{len(stops)} mid-reel music stops {stops}, preset max {b['max_stops']}")
    # bed presence: the reference bed is there from the first frame to the last (no intro fade-up, no outro gap)
    on_ = np.flatnonzero(~gone)
    entry = round(float(on_[0]) * 0.1, 2) if len(on_) else None
    tail = round(dur - (float(on_[-1]) * 0.1 + 0.4), 2) if len(on_) else None
    out["bed_entry_s"] = entry; out["bed_end_gap_s"] = tail
    if "first frame" in (b.get("end_style") or "") or "last frame" in (b.get("end_style") or ""):
        if entry is None or entry > 0.5:
            out["fails"].append(f"bed enters at {entry} s; the reference bed starts on the first frame")
        if tail is None or tail > 0.5:
            out["fails"].append(f"bed stops {tail} s before the end; the reference bed runs to the last frame")
    # SFX are counted as events: hits within 0.6 s of each other (a whoosh into a boom) are one sound, as the
    # reference detector hears them. rel_db is the loudest 100 ms vs the voice's per-word loudest 100 ms.
    hits = sorted(res.get("sfx", []), key=lambda h: h["t"])
    fam_map = {**KIND_FAMILY, **(s.get("kind_family") or {})}
    events = []
    for h in hits:
        if events and h["t"] - events[-1][-1]["t"] <= 0.6:
            events[-1].append(h)
        else:
            events.append([h])
    pm = len(events) / (dur / 60)
    out["sfx_count"] = len(hits); out["sfx_events"] = len(events); out["sfx_per_min"] = round(pm, 1)
    if not _in(pm, s.get("per_min_range")):
        out["fails"].append(f"{len(events)} SFX events ({len(hits)} hits) = {pm:.1f}/min, preset {s['per_min_range']}/min")
    lr = s.get("level_range")
    heads = [max(ev, key=lambda e: e.get("rel_db", -99)) for ev in events]   # each event's loudest hit
    loud = [h for h in heads if lr and not lr[0] - 0.5 <= h.get("rel_db", 0) <= lr[1] + 0.5]
    if loud:
        out["fails"].append("SFX outside the preset level range " + str(lr) + ": " +
                            ", ".join(f"{h['kind']}@{h['t']} {h['rel_db']} dB" for h in loud[:6]))
    # palette: each event's family is that of its loudest hit; most events must come from the reference's
    # common families (share >= 0.1 of its SFX), e.g. sub booms, not UI dings and risers
    pal = s.get("palette") or {}
    if pal and events:
        common = {f for f, v in pal.items() if v >= 0.1}
        fams = [h.get("family") or fam_map.get(h["kind"], h["kind"]) for h in heads]
        share = sum(f in common for f in fams) / len(fams)
        out["sfx_families"] = fams; out["sfx_palette_match"] = round(share, 2)
        if share < 0.6:
            out["fails"].append(f"only {share:.0%} of SFX events are in the reference palette {sorted(common)} "
                                f"(got {sorted(set(fams))})")
    # hook: most reference reels open with a hit in the first 1.5 s
    win = s.get("hook_window_s", 1.5)
    if (s.get("hook_share") or 0) >= 0.5 and not any(h["t"] < win for h in hits):
        out["warnings"].append(f"no SFX in the first {win} s; {s['hook_share']:.0%} of reference reels open with one")
    # what the SFX really reach after the peak guard (stem loudest 100 ms around each hit vs the voice)
    vref = res.get("speech_ref_100ms")
    if vref is not None:
        vref += res.get("master_gain_db", 0.0)        # stems are after the master gain
    if vref is not None and sfx is not None:
        eff = []
        for ev in events:
            a = int(max(0, ev[0]["t"] - 0.8) * SR); z = int(min(dur, ev[-1]["t"] + 0.8) * SR)
            pk = -0.691 + 10 * np.log10(_blocks(kweight(sfx[a:z]), 0.1, 0.01).max() + 1e-15)
            plan_db = max(e.get("rel_db", -99) for e in ev)
            eff.append({"t": ev[0]["t"], "plan_db": plan_db, "effective_db": round(float(pk - vref), 1)})
        out["sfx_effective"] = eff
        low = [e for e in eff if e["plan_db"] - e["effective_db"] > 2.0]
        if low:
            out["warnings"].append("peak guard pulled SFX more than 2 dB under plan: " +
                                   ", ".join(f"{e['t']} s {e['plan_db']} -> {e['effective_db']} dB" for e in low))
    if b.get("duck_db_range") and res.get("duck", {}).get("depth_db") is not None:
        d = res["duck"]["depth_db"]; out["duck_db"] = d
        if d > b["duck_db_range"][1] + 1.0:
            out["fails"].append(f"duck depth {d} dB, the reference bed is near-constant (preset {b['duck_db_range']})")
        elif not _in(d, b["duck_db_range"]):
            out["warnings"].append(f"duck depth {d} dB, preset {b['duck_db_range']}")
    # brightness (warning only): centroid of music + SFX while talking with the profile's own estimator (the
    # reference "bed" is the Demucs accompaniment, SFX included), scaled by the Demucs calibration (0.82x)
    cr = b.get("centroid_hz_range")
    if cr and act.sum() > SR:
        from ref_audio_profile import spectral_features
        cen = float(spectral_features(mus + (sfx if sfx is not None else 0), act)["centroid_hz"])
        out["bed_centroid_hz"] = round(cen); out["bed_centroid_demucs_scale_hz"] = round(cen * 0.82)
        if not _in(cen * 0.82, cr):
            out["warnings"].append(f"bed centroid {cen:.0f} Hz (x0.82 = {cen * 0.82:.0f}), preset {cr} (Demucs scale)")
    out["ok"] = not out["fails"]
    return out


def short_term(x, win=0.4, step=0.05):
    p = _blocks(kweight(x), win, step)
    return np.arange(len(p)) * step + win / 2, -0.691 + 10 * np.log10(p + 1e-15)


def plot(x, res, sp, mus, sfx, out, act):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    t = np.arange(len(sp)) / SR
    fig, ax = plt.subplots(3, 1, figsize=(18, 10), sharex=True, gridspec_kw={"height_ratios": [1.2, 1, 1.4]})
    cols = {"A": "#d98c3a", "B": "#3a8fd9", "C": "#c0392b"}
    d = 40
    ax[0].plot(t[::d], sp[::d, 0], color="#888", lw=0.4, label="speech")
    ax[0].plot(t[::d], mus[::d, 0], color="#2a6", lw=0.4, label="music")
    ax[0].plot(t[::d], sfx[::d, 0], color="#a2c", lw=0.5, label="sfx")
    ax[0].set_ylim(-1, 1); ax[0].legend(loc="upper right", fontsize=8); ax[0].set_ylabel("waveform")
    for lab, sig, c in (("speech", sp, "#666"), ("music", mus, "#2a6"), ("sfx", sfx, "#a2c")):
        tt, l = short_term(sig); ax[1].plot(tt, l, color=c, lw=1, label=f"{lab} (400 ms LUFS)")
    ax[1].axhspan(-30, -26, color="#2a6", alpha=0.08)
    ax[1].set_ylim(-50, -5); ax[1].legend(loc="lower right", fontsize=8); ax[1].set_ylabel("LUFS")
    f, tt, S = signal.spectrogram(out.mean(1), SR, nperseg=2048, noverlap=1536)
    ax[2].pcolormesh(tt, f, 10 * np.log10(S + 1e-12), shading="auto", cmap="magma", vmin=-110, vmax=-30)
    ax[2].set_ylim(0, 12000); ax[2].set_ylabel("Hz (final mix)"); ax[2].set_xlabel("seconds")
    for s in res["sections"]:
        for a in ax:
            a.axvspan(s["start"], s["end"], color=cols.get(s["track"], "#999"), alpha=0.07)
        ax[0].text(s["start"] + 0.2, 0.85, f"{s['track']}: {s.get('tone') or ''}", fontsize=9)
        for dd in s.get("dropouts", []):
            ax[1].axvspan(dd["out_at"], dd["back_at"], color="#000", alpha=0.1)
    for tm in res.get("tone_map", []):
        ax[2].axvline(tm["start"], color="w", lw=0.6, ls="--")
        ax[2].text(tm["start"] + 0.1, 11200, tm["tone"][:28], color="w", fontsize=7)
    for h in res["sfx"]:
        ax[0].axvline(h["t"], color="#a2c", lw=0.6, alpha=0.6)
        ax[0].text(h["t"], -0.95, h["kind"], rotation=90, fontsize=7, color="#a2c")
    fig.suptitle("Music + SFX mix: sections, levels, spectrogram"); fig.tight_layout()
    fig.savefig(os.path.join(x.qc, f"{x.label}_sections.png"), dpi=110)


if __name__ == "__main__":
    main()
