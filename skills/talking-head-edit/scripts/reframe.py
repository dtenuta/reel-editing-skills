#!/usr/bin/env python3
"""Face-centered reframing: compute a crop window for every piece in cuts.json.

Usage:
  reframe.py cuts.json [--write]                 detect faces, print crops (--write stores them)
  reframe.py cuts.json --sheet OUT.mp4 SHEET.jpg  one still per piece of a finished video with
                                                  face box, center line and eye-line guides

Default on (settings.reframe = true); build_video.py calls annotate() itself when a piece has
no "crop", so this script is only needed to inspect or tune the numbers.

Method (frame_mode "target", the default since v10):
  1. Sample settings.face_samples frames spread over each piece's cuts (ffmpeg autorotate,
     decoded at 540x960) and run OpenCV YuNet (assets/models/yunet.onnx in the repo, or $YUNET_MODEL;
     Haar cascade fallback). Largest face per frame.
  2. Per piece take the MEDIAN face center x, eye line y and face height over the samples
     (robust to blinks, head turns and a missed detection; no per-frame tracking, so no jitter).
  3. Roles alternate per talking piece (wide / punch; an insert does not shift it). Each role has
     a FRAMING target (settings.frame_targets), measured with YuNet on a hand-made reference re-cut:
     wide = face 19.7% of the frame height with the eye line at 44.4%, punch = face 22.6% with
     the eye line at 39.9%. The zoom is derived, not fixed: zoom(source, role) =
     target face_h / median face_h of that source's locked-off pieces, so every piece of a role
     from one camera setup gets the same zoom (no size jitter) and lands on the target framing whatever
     the camera distance, then refined closed-loop by detecting the face IN the framed crop of up to
     6 pieces (YuNet boxes are not scale invariant; open loop came out ~9% too big). Capped at
     settings.zoom_max (2.25); a capped role prints a WARN with
     the face size actually reached. Every role reports the upscale factor and the real source
     pixels kept (WARN below settings.upscale_warn_px of height).
  4. Crop window per piece: face center at x = 50%, eye line at the role's eye_y, clamped inside
     the frame. The achieved face position is reported; anything outside +/-2% x is flagged.
  5. Handheld pieces (median-face x spread across samples > settings.track_spread, e.g. a selfie
     hook) keep their natural framing (the reference left its selfie hook untouched): zoom
     settings.handheld_zoom (just enough room to pan), eye line settings.eye_y, smoothed track:
     face sampled every track_step s, median + moving-average filtered, rendered as a
     piecewise-linear crop pan (p["track"]). Locked-off tripod pieces never pan.
  frame_mode "zoom" = v2-v9: fixed role zooms settings.reframe_zooms (1.08 / 1.25) raised only to
  center the face, punch >= wide * zoom_contrast, eye line in eye_band, cap zoom_max.
Crops are stored per piece as "crop": [x, y, w, h] in output pixels (source scaled to WxH).
"""
import argparse, json, os, subprocess, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import FFMPEG, probe, YUNET
DW, DH = 540, 960  # detection resolution (portrait)


def grab(src, t, w=DW, h=DH):
    import cv2
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", src, "-frames:v", "1",
                          "-vf", f"scale={w}:{h}", "-f", "image2pipe", "-vcodec", "png", "-"],
                         capture_output=True, check=True).stdout
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)


def grab_crop(src, t, crop, S, w=DW, h=DH):
    """Frame at t cropped to an output-pixel window [x, y, cw, ch] (applied in source pixels, like
    build_video.crop_filter) and scaled to w x h: what the viewer will see, for closed-loop sizing."""
    import cv2
    x, y, cw, ch = crop; W, H = S["width"], S["height"]
    vf = f"crop=iw*{cw}/{W}:ih*{ch}/{H}:iw*{x}/{W}:ih*{y}/{H},scale={w}:{h}"
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", src, "-frames:v", "1",
                          "-vf", vf, "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, check=True).stdout
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)


class Detector:
    def __init__(self, model=YUNET):
        import cv2
        self.cv2 = cv2
        self.yn = None
        if os.path.isfile(model):
            try:
                self.yn = cv2.FaceDetectorYN_create(model, "", (DW, DH), score_threshold=0.6)
            except Exception as e:  # pragma: no cover
                print("YuNet unavailable, Haar fallback:", e)
        else:
            print(f"WARN YuNet model not found at {model}; using the Haar fallback (the framing targets were measured "
                  "with YuNet). Set YUNET_MODEL or REELKIT_HOME (see README).")
        self.haar = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))

    def __call__(self, img):
        """Return (cx, eye_y, face_h) as fractions of the frame, or None."""
        h, w = img.shape[:2]
        if self.yn is not None:
            self.yn.setInputSize((w, h))
            _, faces = self.yn.detect(img)
            if faces is not None and len(faces):
                f = max(faces, key=lambda r: r[2] * r[3])
                x, y, bw, bh = f[:4]
                rex, rey, lex, ley = f[4:8]  # eye landmarks
                return ((x + bw / 2) / w, ((rey + ley) / 2) / h, bh / h, (x / w, y / h, bw / w, bh / h))
        g = self.cv2.cvtColor(img, self.cv2.COLOR_BGR2GRAY)
        r = self.haar.detectMultiScale(g, 1.1, 5, minSize=(w // 8, w // 8))
        if len(r):
            x, y, bw, bh = max(r, key=lambda b: b[2] * b[3])
            return ((x + bw / 2) / w, (y + 0.40 * bh) / h, bh / h, (x / w, y / h, bw / w, bh / h))
        return None


def sample_times(cuts, n):
    tot = sum(e - s for s, e in cuts)
    out = []
    for k in range(n):
        d = tot * (k + 0.5) / n
        for s, e in cuts:
            if d <= e - s:
                out.append(s + d); break
            d -= e - s
    return out


def dense_track(det, src, cuts, step):
    """Handheld piece: face position every `step` s (source time), median-filtered (5) then
    moving-averaged (5) so the reframe follows slow drift but never jitters."""
    pts = []
    for s0, e0 in cuts:
        t = s0 + step / 2
        while t < e0:
            g = det(grab(src, t))
            if g:
                pts.append([t, g[0], g[1]])
            t += step
    if len(pts) < 3:
        return None
    a = np.array(pts)
    for c in (1, 2):
        v = a[:, c].copy()
        med = np.array([np.median(v[max(0, i - 2):i + 3]) for i in range(len(v))])
        a[:, c] = np.array([med[max(0, i - 2):i + 3].mean() for i in range(len(v))])
    return a.tolist()


def need_zoom(fx, ey, S):
    """Minimum zoom that centers x exactly and puts the eye line inside the eye band."""
    zx = 1.0 / (2 * min(fx, 1 - fx))
    best = 9.0
    for ty in np.linspace(S["eye_band"][0], S["eye_band"][1], 11):
        zy = max(ty / max(ey, 1e-3), (1 - ty) / max(1 - ey, 1e-3), 1.0)
        best = min(best, zy)
    return max(zx, best, 1.0)


def place(fx, ey, z, S, ty=None):
    W, H = S["width"], S["height"]
    w, h = round(W / z / 2) * 2, round(H / z / 2) * 2
    if ty is None:
        lo_b, hi_b = S["eye_band"]
        ty = min(max(S["eye_y"], lo_b), hi_b)
    x = fx * W - w / 2
    y = ey * H - ty * h
    x = int(round(max(0, min(W - w, x)))); y = int(round(max(0, min(H - h, y))))
    return [x, y, w, h], ((fx * W - x) / w, (ey * H - y) / h)


def annotate(C, verbose=True):
    S = C["settings"]
    det = Detector(S.get("face_model", YUNET))
    meas = []
    for p in C["pieces"]:
        if p.get("insert"):  # B-roll insert: no face, not part of the wide/punch alternation
            meas.append("insert"); continue
        src = C["sources"][p["src"]]
        got = [det(grab(src, t)) for t in sample_times(p["cuts"], S["face_samples"])]
        got = [g for g in got if g]
        if not got:
            meas.append(None)
            print(f"   WARN {p['label']}: no face found, piece keeps a centered crop"); continue
        a = np.array([g[:3] for g in got])
        fx, ey, fh = np.median(a, axis=0)
        spread = float(np.ptp(a[:, 0])) if len(a) > 1 else 0.0
        meas.append((float(fx), float(ey), float(fh), len(got), spread))
        if spread > S["track_spread"]:
            p["_track_raw"] = dense_track(det, src, p["cuts"], S["track_step"])
    # 0 = wide, 1 = punch, alternating over the talking pieces only (an insert does not shift it)
    roles, r_ = [], 0
    for m_ in meas:
        roles.append(r_ if m_ != "insert" else None); r_ += m_ != "insert"
    roles = [r % 2 if r is not None else None for r in roles]
    ins = [m_ == "insert" for m_ in meas]
    meas = [None if m_ == "insert" else m_ for m_ in meas]
    zmax = S["zoom_max"]
    target = S.get("frame_mode", "target") == "target"
    hand = [bool(m and m[4] > S["track_spread"]) for m in meas]
    zr = []
    for r in (0, 1):
        need = [need_zoom(m[0], m[1], S) for m, rr in zip(meas, roles) if m and rr == r]
        zr.append(min(zmax, max([S["reframe_zooms"][r]] + need)))
    if zr[1] < zr[0] * S["zoom_contrast"]:
        zr[1] = min(zmax, zr[0] * S["zoom_contrast"])
    if zr[1] < zr[0] * S["zoom_contrast"] - 1e-6:  # punch hit the cap: pull wide down to keep contrast
        zr[0] = zr[1] / S["zoom_contrast"]
    S["reframe_role_zooms"] = [round(z, 4) for z in zr]
    ZT, KB, report = {}, {}, []
    if target:
        T = S["frame_targets"]
        fh_src = {}
        for p, m, h_ in zip(C["pieces"], meas, hand):
            if m and not h_:
                fh_src.setdefault(p["src"], []).append(m[2])
        for k, fhs in fh_src.items():
            fh = float(np.median(fhs)); dim = probe(C["sources"][k])
            for r in (0, 1):
                # open loop: target / source face height. YuNet's box is not scale invariant (a face
                # 100 px tall in the 540x960 detection frame gets a relatively smaller box than the
                # same face at 210 px: open loop came out ~9% too big), so close the loop: detect the
                # face in the actual framed crop of up to 6 pieces of this source + role and rescale
                # the zoom until the median framed face hits the target (2 passes).
                want = T[r]["face_h"] / fh
                pcs = [(p, m) for p, m, rr, h_ in zip(C["pieces"], meas, roles, hand)
                       if m and not h_ and rr == r and p["src"] == k]
                pcs = pcs[::max(1, len(pcs) // 6)][:6]
                for _ in range(2):
                    z = min(zmax, max(1.0, want))
                    got = []
                    for p, m in pcs:
                        crop, _ = place(m[0], m[1], z, S, T[r]["eye_y"])
                        g = det(grab_crop(C["sources"][k], sample_times(p["cuts"], 1)[0], crop, S))
                        if g:
                            got.append(g[2])
                    if not got:
                        break
                    want = want * T[r]["face_h"] / float(np.median(got))
                z = min(zmax, max(1.0, want))
                ZT[(k, r)] = z; KB[(k, r)] = (T[r]["face_h"] / fh) / want  # detector scale bias
                kept_w, kept_h = dim["width"] / z, dim["height"] / z
                up = S["height"] / kept_h
                row = {"src": k, "role": ("wide", "punch")[r], "src_face_h": round(fh, 4), "zoom": round(z, 4),
                       "target_face_h": T[r]["face_h"], "face_h": round(T[r]["face_h"] * min(1.0, z / want), 4),
                       "target_eye_y": T[r]["eye_y"],
                       "src_px": [dim["width"], dim["height"]], "kept_px": [round(kept_w), round(kept_h)],
                       "upscale": round(up, 3), "capped": want > zmax + 1e-9, "too_close": want < 1.0}
                report.append(row)
        S["frame_report"] = report
        if verbose:
            print(f"reframe (target framing, cap {zmax}x):")
            for row in report:
                print(f"  source {row['src']} {row['role']:>5}: src face {row['src_face_h']:.3f} -> zoom {row['zoom']:.3f}"
                      f" (closed loop on the framed crop) -> face {row['face_h']:.3f} (target {row['target_face_h']}),"
                      f" eye line {row['target_eye_y']};"
                      f" keeps {row['kept_px'][0]}x{row['kept_px'][1]} of {row['src_px'][0]}x{row['src_px'][1]} source px,"
                      f" upscale {row['upscale']:.2f}x to {S['width']}x{S['height']}")
                if row["capped"]:
                    print(f"   WARN {row['role']} framing capped by zoom_max {zmax}: face {row['face_h']:.3f} instead of "
                          f"{row['target_face_h']} ({(row['face_h'] / row['target_face_h'] - 1) * 100:+.0f}%). "
                          f"Camera too far for the target; raise zoom_max only if the upscale below is acceptable")
                if row["too_close"]:
                    print(f"   WARN {row['role']}: face already larger than the target at 1.0x ({row['src_face_h']:.3f});"
                          f" cannot zoom out, framing is tighter than the target")
                if row["kept_px"][1] < S["upscale_warn_px"]:
                    print(f"   WARN {row['role']}: only {row['kept_px'][1]} px of real source height kept (< "
                          f"{S['upscale_warn_px']}), {row['upscale']:.2f}x upscale; expect visible softness")
    elif verbose:
        print(f"reframe (legacy zoom mode): wide zoom {zr[0]:.3f}, punch zoom {zr[1]:.3f} (cap {zmax}), eye line target {S['eye_y']}")
    for p, m, r, isn in zip(C["pieces"], meas, roles, ins):
        if isn:
            continue
        ty = None
        if target and m and m[4] > S["track_spread"]:  # handheld: natural tracked framing
            z = min(S["handheld_zoom_max"], max(S["handheld_zoom"], need_zoom(m[0], m[1], S)))
        elif target:
            z = ZT.get((p["src"], r)) or next((v for (k_, r_), v in ZT.items() if r_ == r), zr[r])
            ty = S["frame_targets"][r]["eye_y"]
        else:
            z = zr[r]
        p["zoom"] = round(z, 4)
        fx, ey = (m[0], m[1]) if m else (0.5, S["eye_y"])
        p["crop"], (ax, ay) = place(fx, ey, z, S, ty)
        p["face"] = {"src_cx": round(fx, 4), "src_eye_y": round(ey, 4),
                     "out_cx": round(ax, 4), "out_eye_y": round(ay, 4),
                     "samples": m[3] if m else 0, "cx_spread": round(m[4], 4) if m else None,
                     "role": ("wide", "punch")[r], "handheld": bool(m and m[4] > S["track_spread"]),
                     "src_face_h": round(m[2], 4) if m else None,
                     "out_face_h": round(m[2] * z * KB.get((p["src"], r), 1.0), 4) if m else None}
        tr = p.pop("_track_raw", None)
        if tr:
            path = []
            for t_, x_, y_ in tr:
                c, (tx, ty_) = place(x_, y_, z, S, ty)
                path.append([round(t_, 3), c[0], c[1], round(tx, 3)])
            p["track"] = path
            ach = [q[3] for q in path]
            if verbose:
                print(f"  {p['label']:>6} handheld (x spread {m[4]:.2f}): smoothed track, {len(path)} keys, "
                      f"face x after reframe {min(ach):.2f}-{max(ach):.2f}")
        else:
            p.pop("track", None)
        flag = "" if abs(ax - 0.5) <= 0.02 else "  <-- not fully centered (crop hit the frame edge)"
        if verbose:
            print(f"  {p['label']:>6} {p['face']['role'] + (' handheld' if p['face']['handheld'] else ''):>14} zoom {z:.3f}"
                  f" src face x {fx:.3f} eye {ey:.3f} -> out x {ax:.3f} eye {ay:.3f}"
                  + (f" face {p['face']['out_face_h']:.3f}" if m else "") +
                  f"  crop {p['crop']}  ({p['face']['samples']} samples, x spread {p['face']['cx_spread']}){flag}")
    return C


def sheet(video, C, out):
    """One still per piece of the finished video, with face box, center line and eye guides
    (target mode: the role's target eye line; legacy: the eye band), measured face height vs target."""
    import cv2
    S = C["settings"]; det = Detector(S.get("face_model", YUNET))
    target = S.get("frame_mode", "target") == "target"
    t = 0.0; tiles = []
    for p in C["pieces"]:
        d = sum(e - s for s, e in p["cuts"])
        mid = t + d / 2; t += d
        img = grab(video, mid, 360, 640)
        h, w = img.shape[:2]
        f = det(img)
        cv2.line(img, (w // 2, 0), (w // 2, h), (0, 255, 255), 1)
        role = (p.get("face") or {}).get("role")
        hand = (p.get("face") or {}).get("handheld")
        if target and role and not hand and not p.get("insert"):
            tg = S["frame_targets"][("wide", "punch").index(role)]
            cv2.line(img, (0, int(tg["eye_y"] * h)), (w, int(tg["eye_y"] * h)), (255, 0, 255), 1)
        else:
            for fr in S["eye_band"]:
                cv2.line(img, (0, int(fr * h)), (w, int(fr * h)), (255, 200, 0), 1)
        txt = f"{p['label']} z{p.get('zoom', 1):.2f}" + (f" {role[0]}" if role else "")
        if f:
            bx, by, bw, bh = f[3]
            cv2.rectangle(img, (int(bx * w), int(by * h)), (int((bx + bw) * w), int((by + bh) * h)), (0, 255, 0), 2)
            txt += f" f{f[2]:.3f} e{f[1]:.2f}"
        cv2.rectangle(img, (0, 0), (w, 26), (0, 0, 0), -1)
        cv2.putText(img, txt, (4, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(img, f"{mid:.1f}s", (4, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        tiles.append(img)
    cols = 7
    rows = [tiles[i:i + cols] for i in range(0, len(tiles), cols)]
    blank = np.zeros_like(tiles[0])
    rows = [np.hstack(r + [blank] * (cols - len(r))) for r in rows]
    cv2.imwrite(out, np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 88])
    print("piece stills (face box green, frame center yellow, target eye line magenta / legacy eye band blue,"
          " f = face height, e = eye line):", out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cuts")
    ap.add_argument("--write", action="store_true", help="store crops/zooms into cuts.json")
    ap.add_argument("--sheet", nargs=2, metavar=("VIDEO", "SHEET_JPG"))
    a = ap.parse_args()
    C = json.load(open(a.cuts))
    from common import DEFAULTS
    C["settings"] = {**DEFAULTS, **C["settings"]}
    if a.sheet:
        sheet(a.sheet[0], C, a.sheet[1]); return
    annotate(C)
    if a.write:
        json.dump(C, open(a.cuts, "w"), indent=1); print("wrote", a.cuts)


if __name__ == "__main__":
    main()
