"""Build the shared edit (captions + overlays) consumed by BOTH the Remotion and HyperFrames projects.

Usage: python3 build_edit.py <words_raw.json> <overlays.json> <out edit.json> --frames N [--fps 30] [--video src.mp4]

- words_raw.json: faster-whisper word list ({"words":[{"w","s","e"}]})
- overlays.json:  {"corrections": [...], "sansWindows": [[s,e],...], "overlays": [...], "sponsored": bool}
- --video: the reel's own video; promptDemo rows get real waveform bars from its audio (RMS per bar).
Everything is in seconds; frames are derived here once so both renderers agree to the frame.
"""
import json, re, sys, argparse, os

ap = argparse.ArgumentParser()
ap.add_argument("words"); ap.add_argument("overlays"); ap.add_argument("out")
ap.add_argument("--fps", type=int, default=30)
ap.add_argument("--frames", type=int, required=True, help="video frame count of the source")
ap.add_argument("--faces", help="faces.json from faces.py: lifts captions below the chin per shot")
ap.add_argument("--cuts", default="", help="comma-separated shot-cut times (s) for per-shot caption height")
ap.add_argument("--video", help="source video: promptDemo waveform bars come from its audio")
a = ap.parse_args()

words = json.load(open(a.words))["words"]
cfg = json.load(open(a.overlays))
fps = a.fps
# whisper hallucination guard (v6: "making videos. When it comes to" crammed into 59.18-59.26 s, never said): 4+ words
# inside 0.25 s (over 16 words/s) cannot be real speech
hit = set()
for i in range(len(words) - 3):
    if words[i + 3]["e"] - words[i]["s"] < 0.25:
        hit.update(range(i, i + 4))
k = 0; hit = sorted(hit)
while k < len(hit):
    m = k
    while m + 1 < len(hit) and hit[m + 1] == hit[m] + 1:
        m += 1
    ws = words[hit[k]:hit[m] + 1]
    print(f"WARN {len(ws)} words in {ws[-1]['e'] - ws[0]['s']:.2f}s at {ws[0]['s']:.2f}s: '{' '.join(w['w'] for w in ws)}' "
          "(likely a whisper hallucination: listen, then delete the phantom words from words_raw.json, keeping the original "
          "as words_raw.whisper.json)")
    k = m + 1

# 1) hand corrections: {"match": ["re","-motion,"], "replace": "Remotion,"} matches consecutive tokens
toks = [dict(w) for w in words]
for c in cfg.get("corrections", []):
    m, rep = c["match"], c["replace"]
    i = 0
    while i <= len(toks) - len(m):
        if [t["w"] for t in toks[i:i + len(m)]] == m:
            merged = {"w": rep, "s": toks[i]["s"], "e": toks[i + len(m) - 1]["e"]}
            toks[i:i + len(m)] = [merged]
        i += 1

# 2) pages: one word, occasionally two (a tiny word glued to its neighbour)
SMALL = re.compile(r"^(a|I|I'd|I'll|to|of|in|on|is|the|an|my|so|it|or|be|at|do)$", re.I)
pages, i = [], 0
while i < len(toks):
    t = toks[i]; grp = [t]
    if (i + 1 < len(toks) and SMALL.match(t["w"]) and not t["w"].endswith((",", ".", "?"))
            and toks[i + 1]["s"] - t["e"] < 0.25 and len(t["w"]) + len(toks[i + 1]["w"]) <= 11):
        grp.append(toks[i + 1]); i += 1
    pages.append({"text": " ".join(g["w"] for g in grp), "s": grp[0]["s"], "e": grp[-1]["e"],
                  "words": [{"w": g["w"], "s": g["s"], "e": g["e"]} for g in grp]})
    i += 1

dur_s = a.frames / fps
sans = cfg.get("sansWindows", [])
# Caption lane (v2): one lane centered at 75% of the height (y=1440), below the speaker's hands and the mic, above the
# bottom-20% platform UI. Pushed lower only if the chin + 6% sits below it (close-up shots), capped at 76%.
CAP_LANE, CAP_MAX = 0.75, 0.76
cuts = [0.0] + [float(x) for x in a.cuts.split(",") if x] + [dur_s + 1]
faces = json.load(open(a.faces)) if a.faces else []
def cap_y(t):
    for s0, s1 in zip(cuts, cuts[1:]):
        if s0 <= t < s1:
            ys = [f["box"][3] for f in faces if f["box"] and s0 <= f["t"] < s1]
            if not ys: break
            return round(1920 * min(CAP_MAX, max(CAP_LANE, max(ys) + 0.06)))
    return round(1920 * CAP_LANE)
# Per-page font size: base 136px serif / 120px sans, shrunk so the page fits 820px (title-safe, clear of the right UI).
MET = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "templates", "fonts", "caption_metrics.json")))
BASE, FIT_W = {"serif": 136, "sans": 120}, 820
def font_px(text, style):
    m = MET[style]; em = sum(m["adv"].get(ch, m["default"]) for ch in text)
    if style == "sans": em -= 0.03 * max(0, len(text) - 1)   # letter-spacing -0.03em
    return min(BASE[style], int(FIT_W / max(em, 0.1)))
caps = []
for k, p in enumerate(pages):
    nxt = pages[k + 1]["s"] if k + 1 < len(pages) else dur_s
    end = nxt if nxt - p["e"] < 0.45 else p["e"] + 0.2
    f0 = round(p["s"] * fps); f1 = max(f0 + 3, round(end * fps))
    if k + 1 < len(pages):
        f1 = min(f1, round(pages[k + 1]["s"] * fps))
    f1 = min(f1, a.frames)
    mid = (p["s"] + p["e"]) / 2
    style = "sans" if any(s <= mid < e for s, e in sans) else "serif"
    caps.append({"id": f"c{k:03d}", "text": p["text"], "from": f0, "to": f1, "style": style,
                 "y": round(1920 * CAP_LANE) if style == "sans" else cap_y(p["s"]), "fs": font_px(p["text"], style),
                 "words": p["words"]})

DEMO_BARS = 44
def audio_bars(t0, t1, n=DEMO_BARS):
    """RMS (dB) of the reel's own audio in n equal slices of [t0, t1] -> bar heights 0..1 (36 dB range under
    the loudest bar of the whole demo, applied by the caller). None without --video."""
    if not a.video or t1 <= t0:
        return None
    import subprocess, numpy as np
    sr = 16000
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}", "-i", a.video, "-vn",
                          "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.float32)
    if len(x) < n:
        return None
    return [float(10 * np.log10(np.mean(c.astype(np.float64) ** 2) + 1e-12)) for c in np.array_split(x, n)]

def promptdemo_frames(o):
    """promptDemo (audible product demo beat): rows [{tag, kind, start, end, mark:{start,end,label,strike},
    segments:[{start,end,label}]}] -> frames, plus real waveform bars per row from the reel audio."""
    fr = lambda t: round(t * fps)
    o["typeF"] = fr(o.get("typeAt", o["start"] + 4 / fps))
    o["typeEndF"] = fr(o["typeEnd"]) if "typeEnd" in o else None
    dbs = []
    for r in o.get("rows", []):
        r["rf"], r["rt"] = fr(r["start"]), fr(r["end"])
        if r.get("mark"):
            r["mark"] = dict(r["mark"], mf=fr(r["mark"]["start"]), mt=fr(r["mark"]["end"]))
        r["segments"] = [dict(g, sf=fr(g["start"]), st=fr(g["end"])) for g in r.get("segments", [])]
        r["_db"] = audio_bars(r["start"], r["end"])
        dbs += r["_db"] or []
    top = max(dbs) if dbs else 0.0
    for r in o.get("rows", []):
        d = r.pop("_db")
        r["bars"] = [round(min(1.0, max(0.06, (v - (top - 36.0)) / 36.0)), 3) for v in d] if d else None
        if r["bars"] is None:
            print(f"WARN promptDemo {o['id']}: no --video, placeholder waveform for row {r.get('tag')}")
    pairs = ba_pairs(o)
    if pairs:
        o["ba"] = pairs

BA_GAP_S = 0.4
def row_words(r):
    return [re.sub(r"[^a-z0-9']", "", t["w"].lower()) for t in toks if r["start"] - 0.05 <= t["s"] < r["end"] + 0.05]
def ba_pairs(o):
    """BEFORE -> AFTER separator (a sponsor's reviewer: "the audio seems to repeat around 'your emotion'").
    A before/after pair often says the same line twice; with a 0.40 s gap, tiny tags and no sound a brand reviewer
    heard it as an editing glitch. Every consecutive before -> after row pair gets: a gap of >= 0.4 s in the cut, a
    whoosh in the gap (music_mix.py, opt-out "switchSfx": false), and a big full-width BEFORE -> AFTER band that
    switches exactly when the AFTER audio starts (gen.mjs, opt-out "switchLabel": false). verify.py warns when a pair
    that repeats words is missing any of it."""
    out, rows = [], o.get("rows", [])
    for i in range(len(rows) - 1):
        b, a = rows[i], rows[i + 1]
        if b.get("kind") != "before" or a.get("kind") != "after":
            continue
        wb, wa = [w for w in row_words(b) if w], [w for w in row_words(a) if w]
        shared = len(set(wb) & set(wa))
        p = {"b": i, "a": i + 1, "gapF": a["rf"] - b["rt"], "switchF": a["rf"],
             "label": bool(o.get("switchLabel", True)) and not o.get("video"), "sfx": bool(o.get("switchSfx", True)),
             "repeat": shared >= 3 or (min(len(wb), len(wa)) > 0 and shared / min(len(wb), len(wa)) >= 0.5),
             "shared": shared}
        miss = ([f"gap {p['gapF'] / fps:.2f}s < {BA_GAP_S}s"] if p["gapF"] < round(BA_GAP_S * fps) else []) + \
               ([] if p["label"] else ["no BEFORE/AFTER switch band"]) + ([] if p["sfx"] else ["no switch SFX"])
        if miss:
            print(f"WARN promptDemo {o['id']} before -> after{' repeats ' + str(shared) + ' words' if p['repeat'] else ''}: "
                  + ", ".join(miss) + " (a repeated line reads as an editing glitch without a clear separator)")
        out.append(p)
    return out

OFFER_MIN_S, OFFER_FIT, OFFER_FS, OFFER_FLOOR = 2.5, 720, 46, 40
def cta_offer(o):
    """cta "offer" (sponsored reels): the brand's offer line ("Use my link for a FREE
    14-day trial.") on a paper card under the chin. offerAt (s, default start + 10 frames) -> offerF. offerY = chin +
    3% from --faces over the CTA window, kept in 55-60% of the height (above the caption ink). offerFs = Inter size that
    fits the longest line ("\\n" splits lines) in 720px, max 46px; under 40px it will not read on a phone, so shorten
    the line or split it. It must be fully on screen >= 2.5 s: if the reel ends too soon, hold the last frame
    (hold_end.sh) and set the CTA end to the new end."""
    fr = lambda t: round(t * fps)
    o["offerF"] = fr(o["offerAt"]) if "offerAt" in o else o["from"] + 10
    ys = [f["box"][3] for f in faces if f["box"] and o["start"] <= f["t"] <= o["end"]]
    o["offerY"] = round(1920 * min(0.60, max(0.55, (max(ys) if ys else 0.54) + 0.03)))
    m = MET["sans"]
    def em(ln):
        w = sum(m["adv"].get(ch, m["default"]) for ch in ln) - 0.01 * max(0, len(ln) - 1)   # letter-spacing -0.01em
        return w + 0.52 * len(re.findall(r"(?<![A-Za-z])[A-Z]{2,}(?![A-Za-z])", ln))         # .em box padding
    o["offerFs"] = min(OFFER_FS, int(OFFER_FIT / max(0.1, max(em(ln) for ln in o["offer"].split("\n")))))
    full = (o["to"] - 5 - (o["offerF"] + 6)) / fps
    if o["offerFs"] < OFFER_FLOOR:
        print(f"WARN cta {o['id']} offer fits only {o['offerFs']}px (< {OFFER_FLOOR}): split it with \\n or shorten it")
    if full < OFFER_MIN_S:
        print(f"WARN cta {o['id']} offer fully visible {full:.2f}s (< {OFFER_MIN_S}): hold the last frame (hold_end.sh) "
              "and move the CTA end")
    print(f"cta {o['id']} offer: from frame {o['offerF']} ({full:.2f}s fully visible), y {o['offerY']}, {o['offerFs']}px")

ovs = []
for o in cfg["overlays"]:
    o = dict(o); o["from"] = round(o["start"] * fps); o["to"] = min(a.frames, round(o["end"] * fps))
    if "items" in o:   # steps: each item appears at its own time ("at", seconds) -> "f" (frame)
        o["items"] = [dict(it, f=round(it["at"] * fps)) for it in o["items"]]
    if "newAt" in o:   # split: the "new way" pane pops in later
        o["newF"] = round(o["newAt"] * fps)
    if o["type"] == "promptDemo":
        promptdemo_frames(o)
    if o["type"] == "cta" and o.get("offer"):
        cta_offer(o)
    ovs.append(o)

# 3) card-edge snap (v6): whisper starts the first word after an insert early (v5: "Their" 6 frames, "All" 4 frames
#    before the demo card ended), so a serif caption popped over the fading full-frame card. A page that starts in
#    the last EDGE frames of a full-frame card (takeover / promptDemo) and is spoken at or after the card's end (its
#    last word ends in the card's last 2 frames or later, or the page stays up past the card) waits for the card.
#    It keeps at least MIN_PAGE frames; later pages are pushed back as needed (v6: "All" 1697 -> 1703, "of this" -> 1707).
FULL, EDGE, MIN_PAGE = ("takeover", "promptDemo"), 10, 4
def pops_early(c, o):
    return o["to"] - EDGE <= c["from"] < o["to"] and (c["words"][-1]["e"] * fps >= o["to"] - 2 or c["to"] > o["to"])
for o in ovs:
    if o["type"] not in FULL:
        continue
    for k, c in enumerate(caps):
        if not pops_early(c, o):
            continue
        print(f"snap caption '{c['text']}' {c['from']} -> {o['to']} (after {o['id']} ends)")
        c["from"] = o["to"]; c["to"] = max(c["to"], c["from"] + MIN_PAGE)
        for p, q in zip(caps[k:], caps[k + 1:]):
            if q["from"] >= p["to"]:
                break
            print(f"  push caption '{q['text']}' {q['from']} -> {p['to']}")
            q["from"] = p["to"]; q["to"] = max(q["to"], q["from"] + 3)
        c["to"] = min(c["to"], a.frames)

edit = {"fps": fps, "width": 1080, "height": 1920, "durationInFrames": a.frames,
        "source": cfg.get("source", "reel.mp4"), "captions": caps, "overlays": ovs,
        "safe": cfg.get("safe", {}), "sponsored": bool(cfg.get("sponsored", False))}
json.dump(edit, open(a.out, "w"), indent=1, ensure_ascii=False)
print(f"{len(caps)} caption pages, {len(ovs)} overlays -> {a.out}")
print(" | ".join(f"{c['text']}" for c in caps))
