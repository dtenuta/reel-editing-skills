"""Verify a rendered reel against the source and the shared edit.
Usage: python3 verify.py <source.mp4> <render.mp4> <edit.json> <faces_src.json> <outdir> <label>
Writes <outdir>/<label>_contact.jpg, <label>_sync.jpg, <label>_mobile.jpg (caption legibility at 360px wide)
and prints a JSON report. Exit code 1 if any hard check fails (frames, corrupt frames, legibility, card edges, CTA offer
shown >= 2.5 s at >= 40px). Warnings (sponsored demo beats, BEFORE/AFTER separator, missing offer) never fail the run.
"""
import json, re, subprocess, sys, os
if len(sys.argv) < 7 or sys.argv[1] in ("-h", "--help"):
    print(__doc__.strip()); sys.exit(0 if sys.argv[1:2] in (["-h"], ["--help"]) else 2)
import cv2, numpy as np

src, out, editp, facesp, outdir, label = sys.argv[1:7]
os.makedirs(outdir, exist_ok=True)
edit = json.load(open(editp)); faces = json.load(open(facesp)); FPS = edit["fps"]

def probe(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_entries",
                        "stream=codec_name,codec_type,width,height,r_frame_rate,nb_read_frames,duration,bit_rate,sample_rate,channels:format=duration",
                        "-of", "json", p], capture_output=True, text=True)
    return json.loads(r.stdout)

def frame(cap, f):
    cap.set(cv2.CAP_PROP_POS_FRAMES, f); ok, img = cap.read(); return img if ok else None

report = {"label": label, "source": probe(src), "render": probe(out)}
sv = [s for s in report["source"]["streams"] if s["codec_type"] == "video"][0]
rv = [s for s in report["render"]["streams"] if s["codec_type"] == "video"][0]
report["frames_match"] = sv["nb_read_frames"] == rv["nb_read_frames"]
report["duration_delta_s"] = round(float(report["render"]["format"]["duration"]) - float(report["source"]["format"]["duration"]), 4)

cs, co = cv2.VideoCapture(src), cv2.VideoCapture(out)
W, H = int(co.get(3)), int(co.get(4))

# --- caption sync: 5 spot checks. The caption must be ABSENT one frame before its page starts
#     (unless the previous page butts against it) and PRESENT 2 frames after. Presence = pixel change
#     vs the source inside the caption band.
def cap_band(c):
    y = c["y"] * H / 1920; return int(y - 0.035 * H), int(y + 0.035 * H)
def changed(f, y0, y1):
    a, b = frame(cs, f), frame(co, f)
    if a is None or b is None: return 0
    d = cv2.absdiff(cv2.resize(a, (W, H)), b)[y0:y1, int(.05 * W):int(.95 * W)].max(axis=2)
    return float((d > 60).mean())
caps_all = edit["captions"]   # 5 evenly spaced pages
picks = [caps_all[int(i * (len(caps_all) - 1) / 4)] for i in range(5)] if len(caps_all) >= 5 else list(caps_all)
sync, crops = [], []
for c in picks:
    y0, y1 = cap_band(c)
    prev_gap = c["from"] - max([p["to"] for p in edit["captions"] if p["to"] <= c["from"]] or [-99])
    before = changed(c["from"] - 1, y0, y1) if prev_gap > 0 else None
    after = changed(c["from"] + 2, y0, y1)
    sync.append({"text": c["text"], "word_start_s": round(c["words"][0]["s"], 3), "page_from_frame": c["from"],
                 "page_from_s": round(c["from"] / FPS, 3), "offset_ms": round((c["from"] / FPS - c["words"][0]["s"]) * 1000),
                 "changed_px_before": None if before is None else round(before, 4), "changed_px_after": round(after, 4)})
    img = frame(co, c["from"] + 2)[max(0, y0 - 60):y1 + 60, :]
    img = cv2.resize(img, (720, int(img.shape[0] * 720 / W)))
    cv2.putText(img, f'{c["text"]} @ {c["words"][0]["s"]:.2f}s  (frame {c["from"]+2})', (12, 34), cv2.FONT_HERSHEY_SIMPLEX, .9, (0, 255, 255), 2)
    crops.append(img)
cv2.imwrite(f"{outdir}/{label}_sync.jpg", np.vstack(crops), [cv2.IMWRITE_JPEG_QUALITY, 85])
report["caption_sync"] = sync

# --- face coverage: for every 0.5 s sample inside an overlay window (full-frame types excluded: takeover and
#     promptDemo are deliberate full-frame inserts), measure how much of the detected face box the overlay changed.
FULL = ("takeover", "promptDemo")
fc = []
for o in edit["overlays"]:
    if o["type"] in FULL: continue
    for s in faces:
        f = round(s["t"] * FPS)
        if not s["box"] or not (o["from"] + 6 <= f < o["to"] - 5): continue
        x0, y0, x1, y1 = s["box"]
        # tighten the generous Haar box to the face proper (eyes..chin)
        fy0, fy1 = int((y0 + .08 * (y1 - y0)) * H), int(y1 * H); fx0, fx1 = int(x0 * W), int(x1 * W)
        a, b = frame(cs, f), frame(co, f)
        d = cv2.absdiff(cv2.resize(a, (W, H)), b)[fy0:fy1, fx0:fx1].max(axis=2)
        # ignore the caption band (captions sit under the chin by design)
        fc.append({"overlay": o["id"], "t": s["t"], "face_px_changed": round(float((d > 60).mean()), 4)})
report["face_coverage_max"] = max((x["face_px_changed"] for x in fc), default=0)
report["face_coverage_samples"] = len(fc)
report["face_coverage_by_overlay"] = {k: max(x["face_px_changed"] for x in fc if x["overlay"] == k) for k in {x["overlay"] for x in fc}}

# --- corrupt-frame scan: every frame, low-res grey, middle band (30-55% height) which holds neither
#     cards nor captions. Outside takeovers it must match the source; a wrong video frame or black
#     tile shows up as a large mean diff. (Remotion at high concurrency produced ~2% of these.)
def grey_frames(p):
    pr = subprocess.Popen(["ffmpeg", "-v", "error", "-i", p, "-vf", "scale=90:160", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                          stdout=subprocess.PIPE)
    while True:
        b = pr.stdout.read(90 * 160)
        if len(b) < 90 * 160: break
        yield np.frombuffer(b, np.uint8).astype(np.int16).reshape(160, 90)
tk = [(o["from"], o["to"]) for o in edit["overlays"] if o["type"] in FULL]
bad = [i for i, (a, b) in enumerate(zip(grey_frames(src), grey_frames(out)))
       if not any(f <= i < t for f, t in tk) and np.abs(a[48:88] - b[48:88]).mean() > 12]
report["corrupt_frames"] = len(bad)
report["corrupt_frame_list"] = bad[:50]

# --- legibility at phone size: downscale to 360px wide (a 720p phone at 2x density shows about this many
#     CSS px) and measure the caption itself. Glyph = pixels the caption turned bright vs the source.
#     stem_px:  2 x the 95th-percentile distance-transform of the glyph mask = thickest stems, in 360-px pixels.
#     height_px: glyph-row span (cap height + descenders).
#     halo:     mean luma of the glyph minus mean luma of the 1-px ring around it (the outline), so the word
#               separates from a white bookshelf or a grey laptop as well as from dark clothes.
#   Calibrated on one reel. v1 (Instrument Serif 100px, shadow only, judged "pretty thin white lines"):
#   stem 1.9-3.8, height 17-31, halo 81-122 -> fails. v2 (DM Serif Display 136px, 3px outline + shadow):
#   stem 5.7, height 32-38, halo 143-174 -> passes. Floors sit between the two.
LEG = {"stem_px": 4.0, "height_px": 28, "halo": 130}
def legible(c):
    f = (c["from"] + c["to"]) // 2
    a, b = frame(cs, f), frame(co, f)
    if a is None or b is None: return None
    a, b = cv2.resize(a, (360, 640), interpolation=cv2.INTER_AREA), cv2.resize(b, (360, 640), interpolation=cv2.INTER_AREA)
    y = int(c["y"] / 1920 * 640); y0, y1 = max(0, y - 34), min(640, y + 34)
    A, B = a[y0:y1], b[y0:y1]
    lb = cv2.cvtColor(B, cv2.COLOR_BGR2GRAY).astype(np.float32)
    glyph = ((cv2.absdiff(A, B).max(axis=2) > 50) & (lb > 190)).astype(np.uint8)
    if glyph.sum() < 20: return {"text": c["text"], "t": round(f / FPS, 2), "error": "caption not found"}
    ring = cv2.dilate(glyph, np.ones((3, 3), np.uint8)) - glyph
    dt = cv2.distanceTransform(glyph, cv2.DIST_L2, 3)
    rows = np.where(glyph.any(axis=1))[0]
    m = {"text": c["text"], "t": round(f / FPS, 2), "style": c["style"],
         "stem_px": round(2 * float(np.percentile(dt[glyph > 0], 95)), 2),
         "height_px": int(rows[-1] - rows[0] + 1),
         "halo": round(float(lb[glyph > 0].mean() - lb[ring > 0].mean()), 1)}
    m["ok"] = all(m[k] >= v for k, v in LEG.items())
    return m, cv2.resize(b[max(0, y0 - 20):min(640, y1 + 20)], None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
tk_frames = [(o["from"], o["to"]) for o in edit["overlays"] if o["type"] in FULL]
cands = [c for c in edit["captions"] if c["to"] - c["from"] >= 6 and not any(a <= c["from"] < b for a, b in tk_frames)]
# height_px is a glyph-row span, so only compare words that reach full height (a capital, digit or b d f h k l):
# an x-height-only word ("use", "now") measures ~25% shorter at the same font size and failed falsely.
full = [c for c in cands if re.search(r"[A-Z0-9bdfhkl]", c["text"])]
cands = full if len(full) >= 8 else cands
legs, lcrops = [], []
for c in [cands[int(i * (len(cands) - 1) / 7)] for i in range(8)] if len(cands) >= 8 else cands:
    r = legible(c)
    if r is None: continue
    if isinstance(r, dict): legs.append(r); continue
    legs.append(r[0]); im = r[1]
    cv2.putText(im, f'{r[0]["text"]} {"OK" if r[0]["ok"] else "LOW"} stem {r[0]["stem_px"]} halo {r[0]["halo"]:.0f}', (8, 22),
                cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 255, 255) if r[0]["ok"] else (0, 0, 255), 2)
    lcrops.append(im)
if lcrops:
    w = max(i.shape[1] for i in lcrops)
    cv2.imwrite(f"{outdir}/{label}_mobile.jpg", np.vstack([cv2.copyMakeBorder(i, 0, 4, 0, w - i.shape[1], cv2.BORDER_CONSTANT) for i in lcrops]),
                [cv2.IMWRITE_JPEG_QUALITY, 90])
report["legibility_floor"] = LEG
report["legibility"] = legs
report["legibility_ok"] = bool(legs) and all(x.get("ok") for x in legs)

# --- contact sheet: one still in the middle of every overlay (so every graphic beat is visible) + fillers, 2 rows
mids = sorted({round((o["from"] + o["to"]) / 2 / FPS, 1) for o in edit["overlays"]})
dur = edit["durationInFrames"] / FPS
times = mids[:12]
while len(times) < 8: times = sorted(set(times + [round(dur * (len(times) + .5) / 9, 1)]))
if len(times) % 2: times.append(round(dur - 1.0, 1))
tiles = []
for t in times:
    img = cv2.resize(frame(co, min(round(t * FPS), edit["durationInFrames"] - 1)), (405, 720))
    cv2.rectangle(img, (0, 0), (150, 40), (0, 0, 0), -1)
    cv2.putText(img, f"{t:.1f}s", (10, 29), cv2.FONT_HERSHEY_SIMPLEX, .9, (255, 255, 255), 2)
    tiles.append(img)
n = len(tiles) // 2
sheet = np.vstack([np.hstack(tiles[:n]), np.hstack(tiles[n:])])
cv2.imwrite(f"{outdir}/{label}_contact.jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
json.dump(report, open(f"{outdir}/{label}_report.json", "w"), indent=1)
print(json.dumps({k: v for k, v in report.items() if k not in ("source", "render")}, indent=1))
# --- sponsored reels must SHOW the product: >= 2 audible demo beats (promptDemo, or any overlay with "demo": true).
#     A warning, not a hard fail (a brand brief can waive it), printed loudly so it is never missed.
demos = [o["id"] for o in edit["overlays"] if o["type"] == "promptDemo" or o.get("demo")]
report["demo_beats"] = demos
if edit.get("sponsored") and len(demos) < 2:
    report["warnings"] = report.get("warnings", []) + [f"sponsored reel has {len(demos)} demo beat(s) (< 2): show the "
                                                         "product with audible demos (promptDemo), before/after where it applies"]
    print("WARN", report["warnings"][-1])
# --- card edges: a caption page must not pop in the last frames of a full-frame card when its words are spoken after
#     the card (v5: "Their" / "All" popped 4-6 frames early over the fading demo card). build_edit snaps these.
#     Same rule as build_edit.pops_early: starts in the card's last 10 frames and is spoken at / after its end.
edge = [f"{c['text']}@{c['from']} < {o['id']} end {o['to']}" for o in edit["overlays"] if o["type"] in FULL
        for c in edit["captions"] if o["to"] - 10 <= c["from"] < o["to"]
        and (c["words"][-1]["e"] * FPS >= o["to"] - 2 or c["to"] > o["to"])]
report["caption_card_edge"] = edge
if edge:
    report["warnings"] = report.get("warnings", []) + [f"caption pops before a full-frame card ends: {edge} (rebuild edit.json)"]
    print("WARN", report["warnings"][-1])
# --- BEFORE -> AFTER separator (v7). In one reel the before and after clips both said "you can also change your ...",
#     0.40 s apart, with small tags and no sound; the sponsor's reviewer heard "the audio repeat around 'your emotion'" as
#     an editing glitch. A before -> after pair that repeats words needs: a gap >= 0.4 s (in the edit AND measured as
#     silence in the render), the big BEFORE/AFTER switch band (build_edit "ba" + gen.mjs) and a transition SFX in the
#     gap (music_mix whoosh; music_verify checks the hit). Warning, printed loudly.
def words_in(r):
    return {re.sub(r"[^a-z0-9']", "", w["w"].lower()) for c in edit["captions"] for w in c.get("words", [])
            if r["rf"] / FPS - 0.05 <= w["s"] < r["rt"] / FPS + 0.05} - {""}
def silent_gap_ms(t0, t1):
    """Longest pause touching [t0, t1]: a run of 10 ms blocks (median of 3) 18 dB under the window's loud level (p95,
    floor -50 dBFS). Relative, because a gap is usually the speaker's room tone, not digital silence (one case: room
    tone about 21 dB under the speech, 0.41 s); syllable dips inside words stay under 0.1 s."""
    a0 = max(0.0, t0 - 0.3)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{a0:.3f}", "-t", f"{t1 - t0 + 0.6:.3f}", "-i", out, "-vn",
                          "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, np.float32).astype(np.float64)
    if len(x) < 1600: return None
    db = 10 * np.log10(np.array([np.mean(c ** 2) for c in np.array_split(x, len(x) // 160)]) + 1e-12)
    db = np.median(np.vstack([np.r_[db[:1], db[:-1]], db, np.r_[db[1:], db[-1:]]]), axis=0)
    quiet = db < max(-50.0, float(np.percentile(db, 95)) - 18)
    best, run, start = 0, 0, 0
    for i, q in enumerate(quiet):
        if q:
            if run == 0: start = i
            run += 1
            if a0 + (start / 100) <= t1 + 0.1 and a0 + (i / 100) >= t0 - 0.1: best = max(best, run)
        else:
            run = 0
    return best * 10
ba_rep = []
for o in edit["overlays"]:
    if o["type"] != "promptDemo": continue
    rows = o.get("rows") or []
    for i in range(len(rows) - 1):
        b, a = rows[i], rows[i + 1]
        if b.get("kind") != "before" or a.get("kind") != "after": continue
        wb, wa = words_in(b), words_in(a); shared = len(wb & wa)
        repeat = shared >= 3 or (min(len(wb), len(wa)) > 0 and shared / min(len(wb), len(wa)) >= 0.5)
        p = next((p for p in o.get("ba", []) if p["b"] == i and p["a"] == i + 1), None)
        gap_f = a["rf"] - b["rt"]; gap_ms = silent_gap_ms(b["rt"] / FPS, a["rf"] / FPS)
        miss = []
        if gap_f < round(0.4 * FPS) or (gap_ms is not None and gap_ms < 400):
            miss.append(f"gap {gap_f / FPS:.2f}s in the edit, {gap_ms} ms of silence measured (want >= 400 ms)")
        if not (p and p.get("label")):
            miss.append("no big BEFORE/AFTER switch band (rebuild edit.json with build_edit.py, regenerate with gen.mjs)")
        if not (p and p.get("sfx")):
            miss.append("no transition SFX in the gap (music_mix.py adds a whoosh for each build_edit 'ba' pair)")
        ba_rep.append({"demo": o["id"], "rows": [i, i + 1], "repeat": repeat, "shared_words": sorted(wb & wa),
                       "gap_frames": gap_f, "gap_silence_ms": gap_ms, "switch_band": bool(p and p.get("label")),
                       "switch_sfx": bool(p and p.get("sfx")), "missing": miss})
        if repeat and miss:
            report["warnings"] = report.get("warnings", []) + [
                f"{o['id']} BEFORE/AFTER repeats {shared} words ({' '.join(sorted(wb & wa))}) without a clear separator: "
                + "; ".join(miss) + ". A repeated line with no separator reads as an editing glitch."]
            print("WARN", report["warnings"][-1])
report["before_after"] = ba_rep
# --- CTA offer line (v7, sponsored reels): the brand's offer ("Use my link for a FREE 14-day
#     trial.") must be on screen, readable on a phone (>= 40px design = 13+ px on a 360px-wide phone) and fully
#     visible >= 2.5 s (hold the last frame with hold_end.sh when the reel ends too soon). Hard checks; a sponsored
#     reel whose CTA carries no offer gets a warning (the brief may not give one).
offer_rep, offer_ok, ocrops = [], True, []
for o in edit["overlays"]:
    if o["type"] != "cta": continue
    if not o.get("offer"):
        if edit.get("sponsored"):
            report["warnings"] = report.get("warnings", []) + [f"sponsored reel CTA {o['id']} has no offer line: add the "
                                                                 "brand's offer (cta 'offer') when the brief or brand gives one"]
            print("WARN", report["warnings"][-1])
        continue
    of = o.get("offerF", o["from"] + 10)
    full_s = round((o["to"] - 5 - (of + 6)) / FPS, 2); on_s = round((o["to"] - of) / FPS, 2)
    mid = min((of + 6 + o["to"] - 5) // 2, edit["durationInFrames"] - 1)
    oy = o.get("offerY", 1092) * H / 1920
    shown = changed(mid, int(oy), int(oy + 0.09 * H))
    fs = o.get("offerFs", 44)
    r = {"id": o["id"], "offer": o["offer"], "from_s": round(of / FPS, 2), "to_s": round(o["to"] / FPS, 2),
         "fully_visible_s": full_s, "on_screen_s": on_s, "font_px": fs, "phone_px_at_360": round(fs / 3, 1),
         "changed_px": round(shown, 4)}
    r["ok"] = full_s >= 2.5 and fs >= 40 and shown > 0.05
    offer_ok &= r["ok"]; offer_rep.append(r)
    img = frame(co, mid)
    if img is not None:
        crop = cv2.resize(img, (360, 640), interpolation=cv2.INTER_AREA)[max(0, int(oy / H * 640) - 20):int(oy / H * 640) + 90]
        ocrops.append(cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST))
    print(("OK " if r["ok"] else "FAIL ") + f"offer {o['id']}: fully visible {full_s}s (on screen {on_s}s), {fs}px "
          f"({r['phone_px_at_360']}px on a 360px phone), drawn {shown:.1%} of its band")
if ocrops:
    cv2.imwrite(f"{outdir}/{label}_offer_mobile.jpg", np.vstack(ocrops), [cv2.IMWRITE_JPEG_QUALITY, 90])
report["cta_offer"] = offer_rep
report["pass"] = bool(report["frames_match"] and report["corrupt_frames"] == 0 and report["legibility_ok"] and not edge
                      and offer_ok)
json.dump(report, open(f"{outdir}/{label}_report.json", "w"), indent=1)
print("PASS" if report["pass"] else "FAIL", "| legibility", "ok" if report["legibility_ok"] else "BELOW FLOOR",
      "| card edges", "ok" if not edge else "EARLY CAPTION", "| offer", "ok" if offer_ok else "FAIL",
      "| warnings", len(report.get("warnings", [])))
print("video", rv["codec_name"], rv["width"], rv["height"], rv["r_frame_rate"], rv["nb_read_frames"],
      "| audio", [(s["codec_name"], s.get("bit_rate"), s.get("sample_rate")) for s in report["render"]["streams"] if s["codec_type"] == "audio"])
sys.exit(0 if report["pass"] else 1)
