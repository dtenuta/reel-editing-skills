"""Shared helpers for talking-head-edit scripts."""
import json, os, shutil, subprocess
import numpy as np

FFMPEG = os.environ.get("FFMPEG") or shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = os.environ.get("FFPROBE") or shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
SR = 48000


def repo_root():
    """The repo that holds assets/ (and usually .venv/): $REELKIT_HOME if set, else three levels above this
    file's real path (repo/skills/talking-head-edit/scripts). realpath() makes it work when the skill is
    symlinked into ~/.claude/skills; set REELKIT_HOME when the skill folder was copied instead."""
    env = os.environ.get("REELKIT_HOME")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", ".."))


# OpenCV YuNet face detector (assets/models/yunet.onnx). $YUNET_MODEL overrides the path.
YUNET = os.environ.get("YUNET_MODEL") or os.path.join(repo_root(), "assets", "models", "yunet.onnx")

DEFAULTS = {
    "fps": 30,
    "env_win": 0.020,      # RMS window (s)
    "env_hop": 0.005,      # hop (s)
    "highpass": 90,        # Hz, applied before the envelope only
    "thresh_db": -47.0,    # speech/silence threshold on the envelope
    # Word edges (v8, "tight but never clipped"). The -47 dB crossing is NOT the end of a word:
    # breathy vowels ("free"), fricatives (-s, -th, f-) and nasals (-ng) decay below it for
    # 50-300ms. tail_mode "decay" follows the word until BOTH the broadband envelope and the
    # 2-8 kHz band (hf_band, where f/s/th and breath live) sit within decay_margin_db of the
    # LOCAL noise floor for decay_hold, or until the next word starts (a re-rise of rise_db above
    # the running minimum: cut at the dip). Then a safety tail of post_offset is kept. In-points
    # stay tight: a soft attack (fricative / breath onset) is followed back up to attack_max,
    # then pre_onset is kept. Joins carry ~post_offset + pre_onset (~95ms) of true silence.
    # tail_mode "thresh" = v2-v7 behaviour (cut on the -47 crossing + fixed handles).
    # tail_mode "tight" is the default (tight butt-cut joins, measured on a hand-made reference re-cut):
    # every join is butt-cut ON the word: out-points a median 58ms before the -47 dB crossing
    # (where the tail has fallen to ~15 dB above the room tone), in-points ~12ms past the point
    # where the attack rises 15 dB above the room tone, ~0ms of room tone at joins (vs 87ms in v4).
    # Edges sit where the envelope crosses max(local floor, thresh-6) + tight_edge_db; no safety
    # tail, no handle, no join top-up. In-line pauses are still compressed with the decay logic
    # (the reference kept those, longest retained pause 125ms). "decay" = the v8 never-clip behaviour.
    "tail_mode": "tight",
    "tight_edge_db": 15.0,   # edge level above the local room tone (the reference out/in median sits here)
    "tight_out_offset": 0.0, # added to the out-point (negative = tighter)
    "tight_in_offset": 0.0,  # added to the in-point (positive = later, into the attack)
    "tight_max_trim": 0.200, # never move an edge more than this inside the -47 crossing
    "tight_join_gap": [0.0, 0.040],  # target room tone at a join in tight mode (no top-up)
    "hf_band": [2000, 8000],
    "decay_margin_db": 6.0,  # "decayed" = broadband within this many dB of the local floor ...
    "decay_margin_hf_db": 10.0,  # ... and 2-8 kHz within this (breath noise 8-10 dB over a -80 floor is inaudible)
    "decay_hold": 0.030,     # ... for at least this long
    "decay_max": 0.450,      # follow a decaying tail at most this far past the thresh crossing
    "rise_db": 8.0,          # re-rise above the running minimum that marks the next word
    "floor_win": 2.5,        # local noise floor = 10th percentile within +/- this many seconds
    "pre_onset": 0.015,      # keep this much before the (soft) attack (in-points stay tight)
    "post_offset": 0.080,    # safety tail kept after the true decay (word ENDINGS)
    "post_min": 0.065,       # frame fitting may trim the safety tail down to this, never below
    "join_gap": [0.060, 0.110],  # target true-silence gap at a join; short sides are topped up
    "soft_margin_db": 6.0,   # legacy (tail_mode thresh)
    "join_min": 0.025,       # legacy (tail_mode thresh): a join below this gets frames back
    "tail_max": 0.120,       # legacy (tail_mode thresh)
    "attack_max": 0.080,     # follow a soft word attack (breath, fricative) for at most this long
    "onset_slack": 0.35,   # onset may start this early relative to Whisper word start
    "offset_slack": 0.50,  # offset may run this late relative to Whisper word end (soft endings)
    "pause_min": 0.100,    # in-line pauses >= this get compressed (old 0.150)
    "pause_keep_each": 0.035,  # silence kept each side of a compressed pause (~70ms total, old 0.050)
    "pause_keep_tail": 0.050,  # decay mode: room tone kept after the word before an in-line pause
    "xfade": 0.010,        # crossfade at every audio join (s)
    # Legacy center punch-in (used only when reframe is false)
    "zooms": [1.0, 1.15],  # alternated per piece
    "zoom_center": [0.46, 0.45],  # punch-in center as fraction of frame (x, y)
    # Face-centered reframing (default on, see reframe.py)
    "reframe": True,
    # frame_mode "target" (v10): the rule is WHERE THE FACE SITS, not a zoom number. Every talking piece
    # is framed to per-role face targets measured per shot with the same YuNet detector on a hand-made
    # reference re-cut. Zoom is derived per (source, role) from that source's median face height, never
    # hardcoded. Wide/punch alternate (18 of 19 transitions in the reference do).
    # face_h = face box height / frame height, eye_y = eye line from the top.
    "frame_mode": "target",
    "frame_targets": [{"face_h": 0.197, "eye_y": 0.444},   # wide  (reference loose shots, 8 shots)
                      {"face_h": 0.226, "eye_y": 0.399}],  # punch (reference tight shots, 10 shots)
    "zoom_max": 2.25,      # safety cap on the source upscale. Reaching the targets on a 4K phone raw
                           # with a small face (~0.106 of the frame) takes 1.75x wide / 2.02x punch, i.e.
                           # 1906-2191 px of real height upscaled to 3840 (judged sharp enough). 2.25 leaves room
                           # for a slightly wider source and still keeps >= 1707 px. Hitting it prints a
                           # WARN with the face size actually reached, never a silent shortfall.
    "upscale_warn_px": 1700,  # WARN when the crop keeps fewer real source pixels of height than this
    "frame_tol": 0.10,     # verify: output face height within +/-10% of its role target
    "eye_tol": 0.03,       # verify: output eye line within +/-0.03 of its role target
    "handheld_zoom": 1.08, # handheld pieces (selfie hook) keep their natural tracked framing, left
    "handheld_zoom_max": 1.30,  # untouched (face ~36%, eyes ~42%), only zoomed enough to pan
    # frame_mode "zoom" = v2-v9 behaviour: fixed role zooms, eye line in eye_band, cap 1.30
    "reframe_zooms": [1.08, 1.25],  # legacy base zoom for wide / punch pieces (alternating)
    "zoom_contrast": 1.15, # legacy: punch zoom >= wide zoom * this
    "eye_y": 0.38,         # legacy target eye line (also handheld pieces), fraction from the top
    "eye_band": [0.35, 0.40],
    "face_samples": 7,     # frames sampled per piece; the median face position is used
    "track_spread": 0.10,  # face x spread above this = handheld piece -> smoothed pan track
    "track_step": 0.25,    # s between face samples on a tracked piece
    # Speech audibility (v10): the goal is that every piece can be heard well, not a specific boost.
    # Before the final loudnorm every talking piece gets ONE constant gain so its active
    # speech loudness (K-weighted, silence gated) sits at the median of the pieces. Pieces already
    # within level_tol are left alone. Boost is capped by level_max_boost_db and by the room tone:
    # the piece's boosted floor may rise at most level_floor_rise_db over the median piece floor
    # (level_floor_rise_masked_db when the piece has no exposed pause >= 0.15 s, i.e. the room
    # tone is masked by speech). No compression, no new fades: joins stay butt cuts.
    "level_pieces": True,
    "level_tol": 1.0,
    "level_max_boost_db": 10.0,
    "level_max_cut_db": 10.0,
    "level_floor_rise_db": 6.0,
    "level_floor_rise_masked_db": 10.0,
    "level_min_speech": 0.5,   # s of gated speech needed to trust a piece measurement
    # verify audibility gates
    "audible_piece_lu": 2.0,   # piece or sentence more than this below the median -> FLAG
    "audible_word_db": 8.0,    # a word whose peak is less than this above the local floor -> FLAG (reference approved cut: min 10)
    "audible_tail_db": 6.0,    # a sentence/piece-final word whose last 80 ms peak is less than this above it -> FLAG
    "width": 2160, "height": 3840,
    "crf": 16, "preset": "slow", "abitrate": "192k",
    "lufs": -14.0, "tp": -1.0, "lra": 11.0,
}


def load_plan(path):
    plan = json.load(open(path))
    s = dict(DEFAULTS)
    s.update(plan.get("settings", {}))
    plan["settings"] = s
    return plan


def load_audio(path, channels=2, highpass=None, sr=SR):
    af = ["-af", f"highpass=f={highpass}"] if highpass else []
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", path, "-vn", *af, "-f", "f32le",
                          "-ac", str(channels), "-ar", str(sr), "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.float32)
    return x.reshape(-1, channels) if channels > 1 else x.copy()


def envelope(x, sr=SR, win=0.020, hop=0.005):
    """RMS envelope in dB. Returns (db, t_center)."""
    w, h = int(win * sr), int(hop * sr)
    n = max(0, (len(x) - w) // h)
    idx = np.arange(n) * h
    c = np.cumsum(np.concatenate([[0.0], x.astype(np.float64) ** 2]))
    r = np.sqrt((c[idx + w] - c[idx]) / w) + 1e-9
    return 20 * np.log10(r), (idx + w / 2) / sr


def probe(path):
    out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate:stream_side_data=rotation",
                          "-of", "json", path], capture_output=True, text=True, check=True).stdout
    st = json.loads(out)["streams"][0]
    rot = 0
    for sd in st.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    w, h = st["width"], st["height"]
    if abs(rot) in (90, 270):
        w, h = h, w
    num, den = st["r_frame_rate"].split("/")
    return {"width": w, "height": h, "rotation": rot, "fps": float(num) / float(den)}


def write_wav(path, x, sr=SR):
    import wave
    x = np.clip(x, -1, 1)
    if x.ndim == 1:
        x = x[:, None]
    wf = wave.open(path, "wb")
    wf.setnchannels(x.shape[1]); wf.setsampwidth(2); wf.setframerate(sr)
    wf.writeframes((x * 32767).astype(np.int16).tobytes()); wf.close()


def load_source(C, k, channels=1, highpass=None, sr=SR):
    """Load source k of a plan/cuts dict. Insert sources (an audio file dropped in later, e.g. a
    pickup line recorded separately) are padded with settings.source_pad[k] s of digital silence on both ends,
    so their cut times live on that padded timeline."""
    x = load_audio(C["sources"][k], channels=channels, highpass=highpass, sr=sr)
    pad = int(C["settings"].get("source_pad", {}).get(k, 0) * sr)
    if pad:
        z = np.zeros((pad,) + x.shape[1:], x.dtype)
        x = np.concatenate([z, x, z])
    return x


def band_envelopes(C, k, S):
    """(broadband dB, 2-8 kHz dB, t) for source k: broadband = 90 Hz high-pass (as before),
    HF = band-passed to settings.hf_band, where fricatives and breathy endings carry their energy."""
    bb, t = envelope(load_source(C, k, 1, S["highpass"]), win=S["env_win"], hop=S["env_hop"])
    lo, hi = S.get("hf_band", [2000, 8000])
    x = load_source(C, k, 1, None)
    try:
        from scipy.signal import butter, sosfiltfilt
        x = sosfiltfilt(butter(4, [lo, hi], btype="band", fs=SR, output="sos"), x).astype(np.float32)
    except ImportError:  # FFT band-pass fallback
        X = np.fft.rfft(x); f = np.fft.rfftfreq(len(x), 1 / SR)
        X[(f < lo) | (f > hi)] = 0; x = np.fft.irfft(X, len(x)).astype(np.float32)
    hf, _ = envelope(x, win=S["env_win"], hop=S["env_hop"])
    n = min(len(bb), len(hf))
    return bb[:n], hf[:n], t[:n]


class Floors:
    """Local noise floor (10th percentile within +/- floor_win s), per band, capped at the
    global 10th percentile + 3 dB so a stretch of dense speech never reads as 'room tone'."""
    def __init__(self, bb, hf, t, S):
        self.bb, self.hf, self.t, self.S = bb, hf, t, S
        # clamp at -85 dB: a clean/digital file (TTS insert) has no room tone to decay into
        self.g = (max(-85.0, float(np.percentile(bb, 10))), max(-85.0, float(np.percentile(hf, 10))))

    def at(self, tt):
        w = self.S.get("floor_win", 2.5)
        m = np.abs(self.t - tt) <= w
        fb = max(-85.0, min(float(np.percentile(self.bb[m], 10)), self.g[0] + 3))
        fh = max(-85.0, min(float(np.percentile(self.hf[m], 10)), self.g[1] + 3))
        return fb, fh


def quiet_mask(bb, hf, fl, S):
    return (bb < fl[0] + S.get("decay_margin_db", 6.0)) & (hf < fl[1] + S.get("decay_margin_hf_db", 10.0))


def decay_end(bb, hf, t, off, hi, fl, S):
    """Follow a word ending forward from the thresh crossing `off` to where it has truly decayed
    (both bands within decay_margin_db of the local floor for decay_hold), or to the dip before
    the next word (re-rise of rise_db above the running minimum, above thresh-6). Returns
    (time, reason): reason in decayed / next-word / bound / max."""
    hop = S["env_hop"]; hold = max(1, int(round(S.get("decay_hold", 0.03) / hop)))
    q = quiet_mask(bb, hf, fl, S)
    i = int(np.searchsorted(t, off)); i0 = i
    run = 0; mn = bb[min(i, len(bb) - 1)]; amn = i
    lim = min(hi, off + S.get("decay_max", 0.45))
    while i < len(t) and t[i] <= lim:
        if q[i]:
            run += 1
            if run >= hold:
                return float(t[i - run + 1]), "decayed"
        else:
            run = 0
        if bb[i] < mn:
            mn, amn = bb[i], i
        elif bb[i] > mn + S.get("rise_db", 8.0) and bb[i] > S["thresh_db"] - 6 and i > i0:
            return float(t[amn]), "next-word"
        i += 1
    i = min(i, len(t) - 1)
    return float(min(t[i], lim)), ("bound" if lim >= hi - 1e-9 else "max")


def attack_start(bb, hf, t, on, lo, fl, S):
    """Mirror of decay_end for in-points: follow a soft attack (f-, s-, th-, breath) back from
    the thresh crossing `on` until both bands are quiet for decay_hold/2, at most attack_max."""
    hop = S["env_hop"]; hold = max(1, int(round(S.get("decay_hold", 0.03) / 2 / hop)))
    q = quiet_mask(bb, hf, fl, S)
    i = int(np.searchsorted(t, on)); run = 0
    lim = max(lo, on - S.get("attack_max", 0.08))
    while i > 0 and t[i - 1] >= lim:
        i -= 1
        if q[i]:
            run += 1
            if run >= hold:
                return float(t[i + run - 1])
        else:
            run = 0
    return float(max(t[i], lim))


def kweight(x, sr=SR):
    """BS.1770 K-weighting (high shelf +4 dB ~1.7 kHz, high-pass ~38 Hz) applied as a magnitude
    response in the frequency domain. Loudness is energy based, so phase does not matter; numpy only."""
    x = np.asarray(x, np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    X = np.fft.rfft(x); f = np.fft.rfftfreq(len(x), 1 / sr)
    f0, G, Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    Vh, Vb = 10 ** (G / 20), (10 ** (G / 20)) ** 0.4996667741545416
    s = 1j * f / f0
    shelf = (Vh * s ** 2 + Vb * s / Q + 1) / (s ** 2 + s / Q + 1)
    s1 = 1j * f / 38.13547087602444
    hp = s1 ** 2 / (s1 ** 2 + s1 / 0.5003270373238773 + 1)
    return np.fft.irfft(X * np.abs(shelf * hp), len(x))


def block_loudness(y, sr=SR, block=0.1, hop=0.05):
    """K-weighted block loudness (dB, LUFS scale) of an already K-weighted signal."""
    b, h = int(block * sr), int(hop * sr)
    n = max(1, (len(y) - b) // h + 1)
    c = np.cumsum(np.concatenate([[0.0], y ** 2]))
    idx = np.arange(n) * h
    e = (c[np.minimum(idx + b, len(y))] - c[idx]) / b + 1e-12
    return 10 * np.log10(e) - 0.691, e


def speech_loudness(x, sr=SR, floor_db=None, rel_gate=20.0, floor_gate=10.0):
    """Active speech loudness of a stretch of speech: K-weighted 100 ms blocks (50 ms hop); a block
    counts when it is within rel_gate dB of the stretch's 95th-percentile block and (if floor_db is
    given, dBFS RMS of the room tone) at least floor_gate dB above the room tone. Returns
    (loudness, seconds of gated speech). Pauses and room tone do not dilute it, so pieces with
    different pause content compare fairly."""
    y = kweight(x, sr)
    if len(y) < int(0.1 * sr):
        return float("nan"), 0.0
    L, e = block_loudness(y, sr)
    thr = np.percentile(L, 95) - rel_gate
    if floor_db is not None:
        thr = max(thr, floor_db + floor_gate)
    m = L > thr
    if not m.any():
        return float("nan"), 0.0
    return float(10 * np.log10(e[m].mean()) - 0.691), float(m.sum() * 0.05)


def room_floor(x, sr=SR, win=0.02, pct=10):
    """Room-tone floor: pct-th percentile of win-s RMS (dBFS) over x (mono or stereo)."""
    x = np.asarray(x, np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    db, _ = envelope(x, sr=sr, win=win, hop=win / 4)
    return float(np.percentile(db, pct)) if len(db) else -90.0


def longest_quiet(x, sr, floor_db, margin=10.0, win=0.02):
    """Longest run (s) where the win-s RMS stays below floor_db + margin: an exposed pause."""
    x = np.asarray(x, np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    db, _ = envelope(x, sr=sr, win=win, hop=win / 2)
    q = db < floor_db + margin; best = run = 0
    for v in q:
        run = run + 1 if v else 0; best = max(best, run)
    return best * win / 2

