#!/usr/bin/env python3
"""Music + SFX stage: tone map -> beat-aware sections -> ducked, EQ'd bed + graphic SFX -> mastered mix.

    music_mix.py <music_plan.json> --reel <captioned.mp4> --words <words_raw.json> --edit <edit.json> --out <dir>

Writes <dir>/mix.wav (48 kHz float, the final audio), music_stem.wav, sfx_stem.wav (both post master gain,
so mix = speech*G + music + sfx) and plan_resolved.json (grids, realized transition times, SFX hits, gains).
The speech (the reel's own audio track) is never processed: it is only scaled by one constant master gain G,
and the run fails if |G| > 0.5 dB. See music_plan.example.json for every field.

Pipeline:
  1. Tracks are resolved by Mixkit ID (cache ~/.cache/reel-music/mixkit, fetched on demand) or a local file.
  2. Grids: each grid fits one tempo to a list of reel-time anchors (story beats such as "new way" at 26.1s),
     so the important beats land on bar lines. Tracks on a grid are time-stretched (ffmpeg atempo) to it.
  3. Sections place a track downbeat (`cue`, source seconds, snapped to the nearest onset) on a reel bar
     (`cue_at`) and play from `start` to `end` with musical in/out transitions:
       in:  fade{sec} | cut | join | xfade{beats} | sweep_in{beats}
       out: fade{sec} | cut | join | xfade{beats} | tape_stop{beats|sec} | sweep_cut{beats,to_hz} |
            filter_out{sec} | button{at,pre_gap_beats,ring}
     `join` splits one track run into two sections (e.g. intro and groove) with their own levels, gaplessly.
     `dropouts` mute a section between two grid-snapped times (the track keeps time underneath, so it
     comes back on the beat). Snap modes: none, beat, bar, floor_beat, ceil_beat, floor_bar, ceil_bar.
  4. Bus EQ (high-pass + a gentle 2-5 kHz dip), sidechain-style ducking from the word timings, then each
     section is calibrated so the *ducked* music measures `level` LUFS while the speaker talks. Audible product demos
     (promptDemo overlays in edit.json, plus plan `duck_windows`) get an extra music-only duck (default 8 dB,
     `demo_duck_db`) so the demo clip is heard clearly; those spans are left out of the section calibration.
  5. SFX: auto-placed on graphic entrances from edit.json (whoosh/pop/ding/riser), plus plan extras. A kind can be
     a licensed Mixkit SFX sample (`sfx.samples`: {"whoosh": {"mixkit_sfx": 1489, "trim": [0, 0.6], "hit": 0.2}})
     instead of the procedural sfx.py sound.
  6. Master: one constant gain to the target LUFS; a peak guard trims only music+SFX if true peak is over.

Style presets: `"style": "<name>"` (default DEFAULT_STYLE; "none" to opt out) loads music_presets/<name>.json, the
reference creator's measured house style from ref_audio_profile.py. Its `mix` block supplies the defaults for duck,
eq, section level, SFX levels, the SFX auto map (which graphics get a sound) and SFX samples; anything in the plan
overrides it. music_verify.py then checks the result against the preset's measured ranges.
"""
import argparse, json, os, subprocess, sys
import numpy as np
from scipy import signal
from scipy.io import wavfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sfx as SFX            # noqa: E402
import music_fetch as MF     # noqa: E402

PRESETS = os.path.join(os.path.dirname(HERE), "music_presets")
DEFAULT_STYLE = "steady-funk-bed"         # measured from a reference creator's reels; see music_presets/


def load_style(plan, plan_dir="."):
    """(path, preset) for the plan's style preset, or (None, {}) when the plan says "style": "none"."""
    name = plan.get("style", DEFAULT_STYLE)
    if not name or name == "none":
        return None, {}
    for p in (name, os.path.join(plan_dir, name), os.path.join(PRESETS, name if name.endswith(".json") else name + ".json")):
        if os.path.isfile(p):
            d = json.load(open(p))
            return os.path.abspath(p), d.get("preset", d)
    raise SystemExit(f"style preset {name!r} not found (looked in {PRESETS})")

SR = 48000


# ---------------------------------------------------------------- audio io
def decode(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "2", "-ar", str(SR),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).astype(np.float64)


def atempo(x, r):
    """Time-stretch by tempo factor r (output is r times faster) without changing pitch."""
    if abs(r - 1) < 1e-5:
        return x
    chain = []
    rr = r
    while rr > 2.0:
        chain.append("atempo=2.0"); rr /= 2
    while rr < 0.5:
        chain.append("atempo=0.5"); rr /= 0.5
    chain.append(f"atempo={rr:.8f}")
    p = subprocess.run(["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
                        "-af", ",".join(chain), "-f", "f32le", "-"],
                       input=x.astype(np.float32).tobytes(), capture_output=True, check=True)
    return np.frombuffer(p.stdout, np.float32).reshape(-1, 2).astype(np.float64)


def write_wav(path, x):
    wavfile.write(path, SR, x.astype(np.float32))


# ---------------------------------------------------------------- loudness (BS.1770-4, 48 kHz)
_K1 = ([1.53512485958697, -2.69169618940638, 1.19839281085285], [1, -1.69065929318241, 0.73248077421585])
_K2 = ([1.0, -2.0, 1.0], [1, -1.99004745483398, 0.99007225036621])


def kweight(x):
    y = signal.lfilter(*_K1, x, axis=0)
    return signal.lfilter(*_K2, y, axis=0)


def _blocks(z, win=0.4, step=0.1):
    n, h = int(win * SR), int(step * SR)
    if len(z) < n:
        return np.array([np.sum(np.mean(z ** 2, 0))]) if len(z) else np.array([1e-12])
    c = np.cumsum(np.concatenate([np.zeros((1, z.shape[1])), z ** 2]), 0)
    starts = np.arange(0, len(z) - n + 1, h)
    return np.sum((c[starts + n] - c[starts]) / n, 1)


def lufs(x):
    """Gated integrated loudness."""
    p = _blocks(kweight(x))
    l = -0.691 + 10 * np.log10(p + 1e-15)
    p = p[l > -70]
    if not len(p):
        return -120.0
    rel = -0.691 + 10 * np.log10(p.mean()) - 10
    p = p[(-0.691 + 10 * np.log10(p)) > rel]
    return float(-0.691 + 10 * np.log10(p.mean())) if len(p) else -120.0


def momentary_max(x):
    p = _blocks(kweight(x))
    return float(-0.691 + 10 * np.log10(p.max() + 1e-15))


def short_peak(x, win=0.1, step=0.01):
    """Loudest 100 ms K-weighted block, in LUFS-like dB: a fair level for short SFX and speech syllables."""
    p = _blocks(kweight(x), win, step)
    return float(-0.691 + 10 * np.log10(p.max() + 1e-15))


def true_peak_db(x):
    up = signal.resample_poly(x, 4, 1, axis=0)
    return float(20 * np.log10(np.max(np.abs(up)) + 1e-12))


# ---------------------------------------------------------------- filters
def biquad_peak(f0, gain_db, q):
    A = 10 ** (gain_db / 40); w = 2 * np.pi * f0 / SR; al = np.sin(w) / (2 * q)
    b = [1 + al * A, -2 * np.cos(w), 1 - al * A]; a = [1 + al / A, -2 * np.cos(w), 1 - al / A]
    return np.array(b) / a[0], np.array(a) / a[0]


def bus_eq(x, eq):
    y = x
    if eq.get("hp_hz"):
        sos = signal.butter(2, eq["hp_hz"] / (SR / 2), "high", output="sos")
        y = signal.sosfilt(sos, y, axis=0)
    if eq.get("dip_db"):
        b, a = biquad_peak(eq.get("dip_hz", 3200), eq["dip_db"], eq.get("q", 0.8))
        y = signal.lfilter(b, a, y, axis=0)
    if eq.get("lp_hz"):
        sos = signal.butter(2, eq["lp_hz"] / (SR / 2), "low", output="sos")
        y = signal.sosfilt(sos, y, axis=0)
    return y


def tv_filter(x, i0, i1, fc_of_u, kind, block=256):
    """Time-varying 2nd-order Butterworth over samples [i0, i1) of x (in place); cutoff fc_of_u(u), u 0..1."""
    i0 = max(0, i0); i1 = min(len(x), i1)
    if i1 <= i0:
        return
    zi = None
    for b0 in range(i0, i1, block):
        b1 = min(i1, b0 + block)
        u = (b0 - i0) / max(1, i1 - i0)
        fc = float(np.clip(fc_of_u(u), 20, SR / 2 * 0.95))
        sos = signal.butter(2, fc / (SR / 2), kind, output="sos")
        if zi is None:
            zi = np.stack([signal.sosfilt_zi(sos) * x[b0, c] for c in range(2)], -1)
        y, zi = signal.sosfilt(sos, x[b0:b1], axis=0, zi=zi)
        x[b0:b1] = y


def ramp(n, up=True, shape="eqpow"):
    u = np.linspace(0, 1, max(1, n))
    g = np.sin(u * np.pi / 2) if shape == "eqpow" else u
    return g if up else g[::-1]


# ---------------------------------------------------------------- grids
class Grid:
    def __init__(self, name, spec):
        self.name = name
        anchors = [a if isinstance(a, dict) else {"t": a, "snap": "bar"} for a in spec["anchors"]]
        self.bpb = spec.get("beats_per_bar", 4)
        hint = spec["bpm_hint"]
        span = spec.get("max_stretch", 0.08)
        origin = anchors[0]["t"]
        best = None
        for bpm in np.arange(hint * (1 - span), hint * (1 + span), 0.005):
            beat = 60 / bpm
            err = 0.0
            for a in anchors[1:]:
                unit = beat * (self.bpb if a.get("snap", "bar") == "bar" else 1)
                k = round((a["t"] - origin) / unit)
                err = max(err, abs(a["t"] - origin - k * unit))
            cost = err + 0.25 * abs(np.log(bpm / hint))
            if best is None or cost < best[0]:
                best = (cost, bpm, err)
        if "bpm" in spec:                      # explicit override
            best = (0, spec["bpm"], 0)
        self.bpm = float(best[1]); self.beat = 60 / self.bpm; self.bar = self.beat * self.bpb
        self.origin = origin; self.max_anchor_err = float(best[2]); self.anchors = anchors

    def snap(self, t, mode):
        if mode in (None, "none"):
            return t
        unit = self.bar if "bar" in mode else self.beat
        k = (t - self.origin) / unit
        k = np.floor(k + 1e-9) if mode.startswith("floor") else np.ceil(k - 1e-9) if mode.startswith("ceil") else round(k)
        return float(self.origin + k * unit)

    def info(self):
        return {"bpm": round(self.bpm, 3), "beat": round(self.beat, 4), "bar": round(self.bar, 4),
                "origin": self.origin, "max_anchor_err_ms": round(self.max_anchor_err * 1000, 1),
                "anchors": [{**a, "nearest_grid": round(self.snap(a["t"], a.get("snap", "bar")), 3)}
                            for a in self.anchors]}


# ---------------------------------------------------------------- tracks
class Track:
    def __init__(self, key, spec, plan_dir):
        self.key, self.spec = key, spec
        if spec.get("file"):
            path = spec["file"] if os.path.isabs(spec["file"]) else os.path.join(plan_dir, spec["file"])
        else:
            path = MF.fetch(spec["tag"], spec["mixkit_id"], os.path.expanduser(spec.get("cache", MF.CACHE)))
        self.path = path
        self.audio = decode(path)
        self.bpm = float(spec["bpm"])
        import librosa
        mono = librosa.resample(self.audio.mean(1).astype(np.float32), orig_sr=SR, target_sr=22050)
        # downbeats are usually kicks: snap cues to onsets of the low band (< 150 Hz)
        low = signal.sosfilt(signal.butter(4, 150 / 11025, output="sos"), mono)
        self.onsets = librosa.onset.onset_detect(y=low.astype(np.float32), sr=22050, units="time",
                                                 hop_length=128, backtrack=True)
        self.key_pc, self.mode, self.key_name = MF.key_of(mono, 22050)

    def snap_cue(self, t, tol=0.06):
        if not len(self.onsets):
            return t
        i = int(np.argmin(np.abs(self.onsets - t)))
        return float(self.onsets[i]) if abs(self.onsets[i] - t) <= tol else t

    def scale_freq(self, degree, base_lo=440.0):
        steps = [0, 2, 4, 5, 7, 9, 11] if self.mode == "major" else [0, 2, 3, 5, 7, 8, 10]
        d = degree - 1
        semis = steps[d % 7] + 12 * (d // 7)
        f = 440 * 2 ** ((self.key_pc - 9) / 12)
        while f < base_lo:
            f *= 2
        while f >= 2 * base_lo:
            f /= 2
        return f * 2 ** (semis / 12)


# ---------------------------------------------------------------- speech activity + ducking
def speech_intervals(words, lead, hold, merge_gap):
    iv = sorted((max(0, w["s"] - lead), w["e"] + hold) for w in words)
    out = []
    for a, b in iv:
        if out and a - out[-1][1] < merge_gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def activity_mask(intervals, n):
    m = np.zeros(n, bool)
    for a, b in intervals:
        m[int(a * SR):int(b * SR)] = True
    return m


def duck_gain(intervals, n, depth_db, attack, release):
    cr = 1000                                   # control rate
    m = np.zeros(int(n / SR * cr) + 2)
    for a, b in intervals:
        m[int(a * cr):int(b * cr)] = 1
    tgt = -depth_db * m
    g = np.zeros_like(tgt); cur = 0.0
    aa = np.exp(-1 / (attack * cr)); ar = np.exp(-1 / (release * cr))
    for i, t in enumerate(tgt):
        c = aa if t < cur else ar
        cur = c * cur + (1 - c) * t
        g[i] = cur
    lin = 10 ** (g / 20)
    return np.interp(np.arange(n) / SR, np.arange(len(lin)) / cr, lin)


def duck_windows(plan, n, edit=None):
    """Extra music-only duck under audible product demos (sponsored reels): the demo clip must be heard clearly,
    so the bed drops `extra_db` (default 8 dB) for the window and comes back after. Windows come from the plan
    (`duck_windows`: [[start, end, extra_db], ...] or {start, end, extra_db, label}) plus, when the plan says
    `"duck_demos": true` (default), every edit.json overlay of type promptDemo (its rows' audio spans).
    Returns (gain curve, resolved windows, mask of samples inside the windows)."""
    ws = []
    for w in plan.get("duck_windows", []):
        if isinstance(w, dict):
            ws.append({"start": float(w["start"]), "end": float(w["end"]), "extra_db": float(w.get("extra_db", 8.0)),
                       "label": w.get("label", "")})
        else:
            ws.append({"start": float(w[0]), "end": float(w[1]), "extra_db": float(w[2]) if len(w) > 2 else 8.0, "label": ""})
    if edit and plan.get("duck_demos", True):
        fps = edit["fps"]
        for o in edit.get("overlays", []):
            if o.get("type") != "promptDemo":
                continue
            rows = o.get("rows") or []   # build_edit.py writes row frames as rf/rt
            a = min((r.get("rf", r.get("from", o["from"])) for r in rows), default=o["from"]) / fps
            b = max((r.get("rt", r.get("to", o["to"])) for r in rows), default=o["to"]) / fps
            # emotion / before-after segments ride along so music_verify can check each one is audible (v6)
            segs = [{"label": g.get("label", ""), "start": round(g.get("sf", 0) / fps, 3), "end": round(g.get("st", 0) / fps, 3)}
                    for r in rows for g in (r.get("segments") or []) if "sf" in g]
            if not any(abs(w["start"] - a) < 0.05 and abs(w["end"] - b) < 0.05 for w in ws):
                ws.append({"start": a, "end": b, "extra_db": float(plan.get("demo_duck_db", 8.0)), "label": o["id"],
                           "segments": segs})
    cr = 1000
    tgt = np.zeros(int(n / SR * cr) + 2); mask = np.zeros(n, bool)
    att, rel = plan.get("demo_duck_attack", 0.12), plan.get("demo_duck_release", 0.25)
    for w in ws:
        # fully down by the first demo sample: the ramp starts ~3 attack constants early
        a = max(0.0, w["start"] - 3 * att * 0.5); b = w["end"]
        tgt[int(a * cr):int(b * cr)] = np.minimum(tgt[int(a * cr):int(b * cr)], -w["extra_db"])
        mask[int(w["start"] * SR):int(min(n / SR, w["end"] + rel) * SR)] = True
    g = np.zeros_like(tgt); cur = 0.0
    aa = np.exp(-1 / (att * 0.5 * cr)); ar = np.exp(-1 / (rel * cr))
    for i, t in enumerate(tgt):
        c = aa if t < cur else ar
        cur = c * cur + (1 - c) * t
        g[i] = cur
    lin = 10 ** (g / 20)
    return np.interp(np.arange(n) / SR, np.arange(len(lin)) / cr, lin), ws, mask


# ---------------------------------------------------------------- sections
def render_section(sec, tr, grid, n, report):
    r = grid.bpm / tr.bpm
    beat = grid.beat
    cue_src = tr.snap_cue(sec["cue"], sec.get("cue_tol", 0.06))
    cue_at = grid.snap(sec.get("cue_at", sec["start"]), sec.get("cue_snap", "bar"))
    start = grid.snap(sec["start"], sec.get("start_snap", "none"))
    end = grid.snap(sec["end"], sec.get("end_snap", "none"))
    out = sec.get("out", {"type": "cut"}); inn = sec.get("in", {"type": "cut"})
    tail = {"tape_stop": out.get("sec", out.get("beats", 1) * beat), "filter_out": out.get("sec", 0.4),
            "button": out.get("ring", 0.35) + 0.1}.get(out["type"], 0.0)
    head = inn.get("beats", 0) * beat if inn["type"] == "xfade" else 0.0
    t0 = max(0.0, start - head - 0.25); t1 = min(n / SR, end + tail + 0.25) + 0.25
    s0 = cue_src + (t0 - cue_at) * r; s1 = cue_src + (t1 - cue_at) * r
    i0, i1 = int(round(s0 * SR)), int(round(s1 * SR))
    pad_l = max(0, -i0); src = tr.audio[max(0, i0):max(0, i1)]
    if pad_l:
        src = np.concatenate([np.zeros((pad_l, 2)), src])
    y = atempo(src, r)
    buf = np.zeros((n + SR, 2))
    j0 = int(round(t0 * SR)); m = min(len(y), len(buf) - j0)
    buf[j0:j0 + m] = y[:m]
    S, E = int(round(start * SR)), int(round(end * SR))
    g = np.zeros(len(buf))
    g[S:E] = 1.0
    rec = {"track": tr.key, "grid": grid.name, "tempo_factor": round(r, 5), "cue_src": round(cue_src, 3),
           "cue_at": round(cue_at, 3), "start": round(start, 3), "end": round(end, 3)}
    # ---- in
    if inn["type"] == "fade":
        k = int(inn.get("sec", 0.3) * SR); g[S:S + k] *= ramp(k)
    elif inn["type"] == "xfade":
        k = int(head * SR); g[S - k:S] = ramp(k)
    elif inn["type"] == "sweep_in":
        k = int(inn.get("beats", 2) * beat * SR)
        tv_filter(buf, S, S + k, lambda u: inn.get("from_hz", 1500) * (20 / inn.get("from_hz", 1500)) ** u, "high")
        g[S:S + 240] *= ramp(240)
    elif inn["type"] == "join":
        pass                                   # continues a section of the same track/grid: no ramp
    else:
        g[S:S + 400] *= ramp(400)
    # ---- out
    ot = out["type"]
    if ot == "fade":
        k = int(out.get("sec", 0.5) * SR); g[E - k:E] *= ramp(k, False)
    elif ot == "xfade":
        k = int(out.get("beats", 1) * beat * SR); g[E - k:E] *= ramp(k, False)
    elif ot == "cut":
        g[E - 600:E] *= ramp(600, False)
    elif ot == "sweep_cut":
        k = int(out.get("beats", 4) * beat * SR); to = out.get("to_hz", 1200)
        tv_filter(buf, E - k, E, lambda u: 20 * (to / 20) ** (u ** 1.5), "high")
        g[E - 600:E] *= ramp(600, False)
    elif ot == "filter_out":
        k = int(out.get("sec", 0.4) * SR)
        tv_filter(buf, E, E + k, lambda u: 16000 * (200 / 16000) ** u, "low")
        g[E:E + k] = np.exp(-5 * np.linspace(0, 1, k)) * ramp(k, False, "lin")
        rec["tail_end"] = round(end + k / SR, 3)
    elif ot == "tape_stop":
        D = out.get("sec", out.get("beats", 1) * beat); k = int(D * SR)
        u = np.arange(k) / k
        pos = E + D * SR * (u - u ** 2 / 2)
        src_idx = np.arange(len(buf))
        ts = np.stack([np.interp(pos, src_idx, buf[:, c]) for c in range(2)], 1)
        buf[E:E + k] = ts
        g[E:E + k] = np.sqrt(1 - u) * np.minimum(1, (1 - u) / 0.25)
        rec["tail_end"] = round(end + D, 3)
    elif ot == "button":
        at = grid.snap(out["at"], out.get("snap", "beat")); A = int(round(at * SR))
        pg = int(out.get("pre_gap_beats", 0) * beat * SR)
        if pg:
            g[A - pg:A] = 0; g[A - pg - 480:A - pg] *= ramp(480, False)
        k = int(out.get("ring", 0.35) * SR)
        g[A:A + k] = np.exp(-6.9 * np.linspace(0, 1, k))      # -60 dB at the end of the ring
        g[A + k:] = 0; g[A:A + 96] *= ramp(96)
        rec["button_at"] = round(at, 3); rec["button_gap_from"] = round(at - pg / SR, 3)
    if ot not in ("filter_out", "tape_stop", "button"):
        g[E:] = 0
    # ---- dropouts (track keeps time underneath)
    rec["dropouts"] = []
    for d in sec.get("dropouts", []):
        a = grid.snap(d["start"], d.get("start_snap", "floor_beat"))
        b = grid.snap(d["end"], d.get("end_snap", "ceil_beat"))
        A, B = int(round(a * SR)), int(round(b * SR))
        do = d.get("out", {"type": "filter_out", "sec": 0.35})
        k = int(do.get("sec", 0.35) * SR)
        if do["type"] == "filter_out":
            tv_filter(buf, A, A + k, lambda u: 16000 * (250 / 16000) ** u, "low")
            g[A:A + k] *= np.exp(-5 * np.linspace(0, 1, k)) * ramp(k, False, "lin")
        else:
            g[A:A + k] *= ramp(k, False)
        g[A + k:B] = 0
        kin = int(d.get("in_sec", 0.01) * SR); g[B:B + kin] *= ramp(kin)
        rec["dropouts"].append({"out_at": round(a, 3), "back_at": round(b, 3)})
    return (buf * g[:, None])[:n], rec


# ---------------------------------------------------------------- SFX
AUTO = {  # overlay type -> list of (kind, anchor, offset_s)
    "titleCard": [("whoosh", "from", 0.10)],
    "steps": [("whoosh", "from", 0.10), ("ding", "items", 0.0)],
    "split": [("whoosh", "from", 0.10), ("pop", "newAt", 0.03)],
    "takeover": [("riser", "from", 0.0), ("whoosh", "from", 0.08)],
    "floatCard": [("pop", "from", 0.03)],
    "chipCard": [("pop", "from", 0.03)],
    "cta": [("whoosh", "from", 0.08), ("pop", "from", 0.13)],
}
# SFX level = its loudest 100 ms block relative to the speech's median loudest-100ms-per-word level (dB).
REL = {"whoosh": -13, "pop": -15, "ding": -14, "tick": -18, "riser": -14, "impact": -11, "thump": -12}


def ba_gaps(edit):
    """(overlay, gap start s, gap end s) for every before -> after row pair of a promptDemo (build_edit "ba", or the
    row kinds for an edit built before v7)."""
    out = []
    fps = (edit or {}).get("fps", 30)
    for o in (edit or {}).get("overlays", []):
        if o.get("type") != "promptDemo":
            continue
        rows = o.get("rows") or []
        for i in range(len(rows) - 1):
            if rows[i].get("kind") == "before" and rows[i + 1].get("kind") == "after" and "rt" in rows[i]:
                out.append((o, rows[i]["rt"] / fps, rows[i + 1]["rf"] / fps))
    return out


def plan_sfx(plan, edit, grids, style=None):
    scfg = (style or {}).get("mix", {}).get("sfx", {})
    cfg = {**scfg, **plan.get("sfx", {})}
    levels = {**REL, **scfg.get("levels", {}), **plan.get("sfx", {}).get("levels", {})}
    auto_map = {k: [tuple(r) for r in v] for k, v in cfg["auto_map"].items()} if cfg.get("auto_map") else AUTO
    skip = set(cfg.get("skip", []))
    fps = edit["fps"] if edit else 30
    hits = []
    if edit and cfg.get("auto", True):
        for o in edit["overlays"]:
            if o["id"] in skip:
                continue
            for kind, anchor, off in auto_map.get(o["type"], []):
                if f"{o['id']}:{kind}" in skip:
                    continue
                if anchor == "items":
                    degs = cfg.get("ding_degrees", [1, 3, 5, 8, 10])
                    for i, it in enumerate(o.get("items", [])):
                        t = it.get("f", it["at"] * fps) / fps
                        hits.append({"t": round(t + off, 3), "kind": kind, "degree": degs[i % len(degs)],
                                     "for": f"{o['id']}#{i + 1}"})
                    continue
                t = o["from"] / fps if anchor == "from" else o.get("newF", o.get("newAt", 0) * fps) / fps
                hits.append({"t": round(t + off, 3), "kind": kind, "for": o["id"]})
        # BEFORE -> AFTER switch (v7): a whoosh peaking mid-gap between a promptDemo's before and after clips, on top
        # of any style auto_map (a reel without one had its repeated line read as a glitch). build_edit writes the
        # pairs as o["ba"]; opt out with "switchSfx": false on the overlay or skip "<id>:switch".
        for o, a, b in ba_gaps(edit):
            if o["id"] in skip or f"{o['id']}:switch" in skip or not o.get("switchSfx", True):
                continue
            hits.append({"t": round((a + b) / 2, 3), "kind": cfg.get("switch_kind", "whoosh"), "for": f"{o['id']}:switch"})
    for x in cfg.get("extra", []):
        h = {**x, "for": x.get("for", "extra")}
        if x.get("grid"):
            h["t"] = round(grids[x["grid"]].snap(x["t"], x.get("snap", "beat")), 3)
        hits.append(h)
    # the reference style opens with a louder boom on the hook: hook_level applies to hook_kinds in the first
    # hook_window seconds unless the hit sets its own rel_db
    hook, win, hk = cfg.get("hook_level"), cfg.get("hook_window", 1.5), set(cfg.get("hook_kinds", ["boom", "impact"]))
    for h in hits:
        if hook is not None and "rel_db" not in h and h["t"] < win and h["kind"] in hk:
            h["rel_db"], h["hook"] = hook, True
        h.setdefault("rel_db", levels[h["kind"]])
    return sorted(hits, key=lambda h: h["t"])


def speech_ref(speech, words):
    """Median over words of each word's loudest 100 ms block: the reference the SFX levels are relative to."""
    v = [short_peak(speech[int(w["s"] * SR):int(w["e"] * SR) + 1]) for w in words if w["e"] - w["s"] >= 0.1]
    return float(np.median(v))


_SAMPLES = {}


def sample_sfx(spec):
    """A licensed SFX sample -> (stereo float64, hit index). spec: mixkit_sfx id (fetched to the SFX cache with a
    licence sidecar) or a local licensed `file`; optional trim [a, b] s, hit s (sample that lands on the graphic;
    default = start of its loudest 100 ms), fade_in/fade_out s, hp_hz/lp_hz, mono true (centre it)."""
    key = json.dumps(spec, sort_keys=True)
    if key not in _SAMPLES:
        path = MF.fetch_sfx(spec["mixkit_sfx"]) if spec.get("mixkit_sfx") else spec["file"]
        y = decode(path)
        if spec.get("trim"):
            y = y[int(spec["trim"][0] * SR):int(spec["trim"][1] * SR)]
        y = y.copy()
        if spec.get("mono"):
            y[:] = y.mean(1, keepdims=True)
        for k, kind in (("hp_hz", "high"), ("lp_hz", "low")):
            if spec.get(k):
                y = signal.sosfilt(signal.butter(2, spec[k] / (SR / 2), kind, output="sos"), y, axis=0)
        fi, fo = int(spec.get("fade_in", 0.003) * SR), int(spec.get("fade_out", 0.02) * SR)
        if fi: y[:fi] *= ramp(fi)[:, None]
        if fo: y[-fo:] *= ramp(fo, False)[:, None]
        if "hit" in spec:
            hit = int(spec["hit"] * SR)
        else:
            p = _blocks(kweight(y), 0.1, 0.005)
            hit = int(np.argmax(p) * 0.005 * SR)
        _SAMPLES[key] = (y, hit, path)
    return _SAMPLES[key]


def render_sfx(hits, n, track_at, ref, samples=None):
    bus = np.zeros((n, 2))
    samples = samples or {}
    for h in hits:
        kw = dict(h.get("params", {}))
        spec = h.get("sample") or samples.get(h["kind"])
        if spec:
            y, hit, path = sample_sfx(spec)
            h["sample"] = {**spec, "path": os.path.basename(path)}
        else:
            if h["kind"] == "ding" and "freq" not in kw:
                tr = track_at(h["t"])
                kw["freq"] = tr.scale_freq(h.get("degree", 1), h.get("ding_base_hz", 330.0)) if tr else 523.3
                h["freq"] = round(kw["freq"], 1)
            y, hit = SFX.make(h["kind"], **kw)
        y = y.astype(np.float64)
        h["level"] = round(ref + h["rel_db"], 1)
        y *= 10 ** ((h["level"] - short_peak(y)) / 20)
        i = int(round(h["t"] * SR)) - hit
        a, b = max(0, i), min(n, i + len(y))
        if b > a:
            bus[a:b] += y[a - i:b - i]
    return bus


def guard_regions(guard, thr_db=-1.0):
    """[start, end, min dB] spans where the peak guard trimmed music+SFX by more than 1 dB."""
    m = guard < 10 ** (thr_db / 20)
    out = []
    i = 0
    idx = np.flatnonzero(np.diff(np.concatenate([[0], m.astype(int), [0]])))
    for a, b in zip(idx[::2], idx[1::2]):
        out.append([round(a / SR, 3), round(b / SR, 3), round(float(20 * np.log10(guard[a:b].min() + 1e-9)), 1)])
    return out


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan"); ap.add_argument("--reel", required=True); ap.add_argument("--words", required=True)
    ap.add_argument("--edit"); ap.add_argument("--out", required=True)
    x = ap.parse_args()
    plan = json.load(open(x.plan)); pdir = os.path.dirname(os.path.abspath(x.plan))
    os.makedirs(x.out, exist_ok=True)
    words = json.load(open(x.words)); words = words if isinstance(words, list) else words["words"]
    edit = json.load(open(x.edit)) if x.edit else None

    speech = decode(x.reel); n = len(speech)
    print(f"speech: {n / SR:.3f}s  {lufs(speech):.2f} LUFS  TP {true_peak_db(speech):.2f} dBTP")
    tracks = {k: Track(k, v, pdir) for k, v in plan["tracks"].items()}
    for t in tracks.values():
        print(f"track {t.key}: {os.path.basename(t.path)}  {t.bpm} bpm  key {t.key_name}")
    grids = {k: Grid(k, v) for k, v in plan["grids"].items()}
    for g in grids.values():
        print(f"grid {g.name}: {g.bpm:.2f} bpm  bar {g.bar:.4f}s  max anchor err {g.max_anchor_err*1000:.0f} ms")

    style_path, style = load_style(plan, pdir)
    smix = style.get("mix", {})
    if style_path:
        print(f"style preset: {style.get('name')} ({style_path})")
    dk = {"depth_db": 7, "attack": 0.06, "release": 0.35, "lead": 0.08, "hold": 0.12, "merge_gap": 0.15,
          **smix.get("duck", {}), **plan.get("duck", {})}
    iv = speech_intervals(words, dk["lead"], dk["hold"], dk["merge_gap"])
    active = activity_mask(iv, n)
    duck = duck_gain(iv, n, dk["depth_db"], dk["attack"], dk["release"])
    dw_gain, dws, dw_mask = duck_windows(plan, n, edit)
    for w in dws:
        print(f"demo duck {w['label'] or 'window'} {w['start']:.2f}-{w['end']:.2f}  -{w['extra_db']:.1f} dB")
    cal_mask = active & ~dw_mask        # sections are calibrated on normal talking only, not under the demos
    eq = {"hp_hz": 35, "dip_hz": 3200, "dip_db": -4, "q": 0.8, **smix.get("eq", {}), **plan.get("eq", {})}
    default_level = smix.get("section_level", -28)

    music = np.zeros((n, 2)); recs = []
    for sec in plan["sections"]:
        y, rec = render_section(sec, tracks[sec["track"]], grids[sec["grid"]], n, recs)
        y = bus_eq(y, {**eq, **sec.get("eq", {})}) * duck[:, None]
        S, E = int(rec["start"] * SR), int(rec["end"] * SR)
        sel = cal_mask[S:E]
        meas = lufs(y[S:E][sel]) if sel.sum() > SR else lufs(y[S:E])
        gain = sec.get("level", default_level) - meas
        y *= 10 ** (gain / 20)
        y *= dw_gain[:, None]
        rec.update({"tone": sec.get("tone"), "level_target": sec.get("level", default_level), "gain_db": round(gain, 2)})
        recs.append(rec); music += y
        print(f"section {sec['track']} {rec['start']:.2f}-{rec['end']:.2f}  x{rec['tempo_factor']}  gain {gain:+.1f} dB")

    def track_at(t):
        for sec, rec in zip(plan["sections"], recs):
            if rec["start"] <= t < rec["end"]:
                return tracks[sec["track"]]
        return None
    hits = plan_sfx(plan, edit, grids, style)
    ref = speech_ref(speech, words)
    samples = {**smix.get("sfx", {}).get("samples", {}), **plan.get("sfx", {}).get("samples", {})}
    sfxb = render_sfx(hits, n, track_at, ref, samples)

    ms = {"lufs": -14.0, "tp": -1.0, "tp_margin": 0.3, "max_speech_change_db": 0.5, **plan.get("master", {})}
    bed = music + sfxb
    G = 0.0
    for _ in range(3):
        G = ms["lufs"] - lufs((speech + bed * 1.0) * 10 ** (G / 20)) + G
    if abs(G) > ms["max_speech_change_db"]:
        sys.exit(f"FAIL: master gain {G:+.2f} dB would change the speech level by more than "
                 f"{ms['max_speech_change_db']} dB; lower the music/SFX levels instead")
    lin = 10 ** (G / 20)
    thr = 10 ** ((ms["tp"] - ms["tp_margin"]) / 20)
    guard = np.ones(n)
    from scipy.ndimage import minimum_filter1d, uniform_filter1d
    for _ in range(8):
        mix = (speech + bed * guard[:, None]) * lin
        pk = np.abs(signal.resample_poly(mix, 4, 1, axis=0)).max(1)[:n * 4].reshape(-1, 4).max(1)
        over = pk > thr
        if not over.any():
            break
        bpk = np.abs(bed * guard[:, None] * lin).max(1) + 1e-9
        need = np.where(over, np.clip(1 - (pk - thr) / bpk, 0, 1) * 0.9, 1.0)
        k = uniform_filter1d(minimum_filter1d(need, int(0.04 * SR)), int(0.02 * SR))
        guard = guard * np.minimum(k, 1)          # only music+SFX are trimmed, never the speech
    mix = (speech + bed * guard[:, None]) * lin
    music_out = music * guard[:, None] * lin; sfx_out = sfxb * guard[:, None] * lin
    write_wav(os.path.join(x.out, "mix.wav"), mix)
    write_wav(os.path.join(x.out, "music_stem.wav"), music_out)
    write_wav(os.path.join(x.out, "sfx_stem.wav"), sfx_out)
    res = {"master_gain_db": round(G, 3), "mix_lufs": round(lufs(mix), 2), "mix_tp_dbtp": round(true_peak_db(mix), 2),
           "peak_guard_min_db": round(float(20 * np.log10(guard.min() + 1e-9)), 2),
           "peak_guard_regions": guard_regions(guard),
           "grids": {k: g.info() for k, g in grids.items()}, "sections": recs, "sfx": hits,
           "tracks": {k: {"file": t.path, "mixkit_id": t.spec.get("mixkit_id"), "title": t.spec.get("title"),
                          "bpm": t.bpm, "key": t.key_name, "license": plan.get("license", MF.LICENSE)}
                      for k, t in tracks.items()},
           "style_preset": style_path, "style": style.get("name"),
           "sfx_license": MF.SFX_LICENSE if any(h.get("sample", {}).get("mixkit_sfx") for h in hits) else None,
           "tone_map": plan.get("tone_map", []), "speech_ref_100ms": round(ref, 2), "duck": dk, "eq": eq,
           "speech_intervals": [[round(a, 3), round(b, 3)] for a, b in iv],
           "duck_windows": [{**w, "start": round(w["start"], 3), "end": round(w["end"], 3)} for w in dws],
           # before -> after gaps (v7): music_verify warns when no SFX hit lands in one
           "ba_gaps": [{"demo": o["id"], "start": round(a, 3), "end": round(b, 3)} for o, a, b in ba_gaps(edit)]}
    json.dump(res, open(os.path.join(x.out, "plan_resolved.json"), "w"), indent=1)
    print(f"master gain {G:+.2f} dB  mix {res['mix_lufs']} LUFS  TP {res['mix_tp_dbtp']} dBTP  "
          f"peak guard {res['peak_guard_min_db']} dB  -> {x.out}/mix.wav")


if __name__ == "__main__":
    main()
