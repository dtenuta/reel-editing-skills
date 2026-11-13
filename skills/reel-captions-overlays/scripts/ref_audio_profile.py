#!/usr/bin/env python3
"""Profile a reference creator's music + SFX style from their reels (audio only), and measure our own mixes and
candidate tracks the same way, so the music stage matches a style with numbers instead of taste words.

  ref_audio_profile.py profile <reel> [<reel> ...] --out DIR [--primary ID] [--primary-weight 3] [--name NAME]
                       [--work DIR] [--keep-stems] [--whisper-py PY] [--tagger panns|none] [--device cpu|mps]
  ref_audio_profile.py mix --speech <reel_without_music.mp4> --stems <music_mix out dir> --out <m.json> [--label v4]
  ref_audio_profile.py tracks <file[@start-end]> [...] --preset <preset.json> --out <cand.json> [--tagger panns|none]
  ref_audio_profile.py plot --profile DIR/profile.json --mix v4.json [--mix v3.json] --out <qc.png>

profile, per reel:
  1. Demucs htdemucs two-stem split (vocals vs accompaniment = bed + SFX).
  2. PANNs CNN14 AudioSet tagger on the accompaniment: genre / mood / instrument labels over 4 s windows; SFX
     classes framewise (Cnn14_DecisionLevelMax SED) and per detected event. --tagger none = DSP features only.
  3. DSP: BPM + beat strength (onset autocorrelation, kick spacing), key, spectral centroid, stereo width,
     bed loudness under speech vs in pauses (= ducking depth), bed vs voice, the bed contour (entry, exit,
     mid-reel stops, swells/drops, track switches by timbre/harmony novelty, intro and outro treatment), and
     SFX events (transients that stand out of the bed by >= 8 dB and >= 4 dB over its local peaks, plus
     tagger hits) with class, level vs the voice and placement (pause / phrase start / word onset / beat).
  4. A weighted "house style" (the --primary reel counts --primary-weight times) and a music-stage preset
     (DIR/preset.json, copy it to music_presets/<name>.json) with the ranges music_verify.py enforces.
Levels: "bed_under_speech_lufs_at_m14" is the bed loudness while the person talks after normalizing the whole
reel to -14 LUFS, i.e. directly comparable to a music_plan section `level`. SFX level = loudest 100 ms block
minus the voice's median per-word (or per-syllable chunk) loudest 100 ms, i.e. the music_mix.py REL scale.

Reference audio is ANALYSIS ONLY. Stems go to --work (default DIR/_work) and are deleted at the end unless
--keep-stems; only numbers are written to DIR. Never commit or reuse reference audio or anything derived from it
except the numeric profile. Run with `python -I` and keep downloaded reels in their own directory.
Needs the analysis venv (scripts/setup_audio.sh --analysis): torch, demucs, panns_inference, librosa, scipy.
"""
import argparse, datetime, json, os, shutil, subprocess, sys
import numpy as np
from scipy import signal
from scipy.ndimage import median_filter, uniform_filter1d

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from music_mix import SR, lufs, kweight, _blocks, short_peak  # noqa: E402  (48 kHz BS.1770 helpers)

PANNS_DIR = os.path.expanduser("~/panns_data")
AT_CKPT = os.path.join(PANNS_DIR, "Cnn14_mAP=0.431.pth")
SED_CKPT = os.path.join(PANNS_DIR, "Cnn14_DecisionLevelMax.pth")

GENRE = ["Pop music", "Hip hop music", "Rock music", "Rhythm and blues", "Soul music", "Reggae", "Funk",
         "Folk music", "Jazz", "Disco", "Classical music", "Electronic music", "House music", "Techno", "Dubstep",
         "Drum and bass", "Electronica", "Electronic dance music", "Ambient music", "Trance music",
         "Independent music", "New-age music", "Lullaby", "Video game music", "Soundtrack music", "Theme music",
         "Background music", "Jingle (music)", "Dance music", "Christmas music", "Blues", "Gospel music",
         "Music of Latin America", "Afrobeat", "Swing music", "Country", "Rock and roll", "Vocal music"]
MOOD = ["Happy music", "Funny music", "Sad music", "Tender music", "Exciting music", "Angry music", "Scary music"]
INSTR = ["Synthesizer", "Sampler", "Piano", "Electric piano", "Keyboard (musical)", "Organ", "Guitar",
         "Acoustic guitar", "Electric guitar", "Bass guitar", "Plucked string instrument", "Ukulele", "Drum",
         "Drum kit", "Drum machine", "Snare drum", "Bass drum", "Hi-hat", "Cymbal", "Percussion", "Clapping",
         "String section", "Violin, fiddle", "Cello", "Orchestra", "Brass instrument", "Saxophone", "Flute",
         "Harp", "Bell", "Glockenspiel", "Marimba, xylophone", "Vibraphone", "Choir", "Whistling", "Humming",
         "Scratching (performance technique)"]
SPEECHY = {"Speech", "Female speech, woman speaking", "Male speech, man speaking", "Narration, monologue",
           "Conversation", "Speech synthesizer", "Child speech, kid speaking", "Singing", "Female singing"}
SFX_KINDS = {
    "whoosh": ["Whoosh, swoosh, swish", "Whip", "Wind noise (microphone)"],
    "pop/click": ["Plop", "Clicking", "Tick", "Tap", "Finger snapping", "Knock", "Drip", "Bouncing", "Boing",
                  "Zing", "Squish", "Pop"],
    "ding/chime": ["Ding", "Chime", "Bell", "Jingle, tinkle", "Wind chime", "Ding-dong", "Glockenspiel",
                   "Tubular bells", "Bicycle bell", "Cowbell", "Triangle", "Ping"],
    "beep/notification": ["Beep, bleep", "Ringtone", "Telephone bell ringing", "Alarm", "Buzzer"],
    "typing": ["Typing", "Computer keyboard", "Typewriter"],
    "camera": ["Camera", "Single-lens reflex camera"],
    "cash": ["Cash register", "Coin (dropping)"],
    "impact/boom": ["Boom", "Thump, thud", "Explosion", "Bang", "Slam", "Smash, crash", "Whack, thwack",
                    "Slap, smack", "Thunk"],
    "record scratch": ["Scratch", "Scratching (performance technique)"],
    "laugh/crowd": ["Laughter", "Applause", "Cheering", "Crowd", "Giggle", "Chuckle, chortle"],
    "sound effect": ["Sound effect"],
}


# ---------------------------------------------------------------- io
def decode(path, sr=SR, ch=2):
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-vn", "-ac", str(ch), "-ar", str(sr),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, ch).astype(np.float64)


def to48(x, sr):
    if sr == SR:
        return x
    g = np.gcd(SR, sr)
    return signal.resample_poly(x, SR // g, sr // g, axis=0)


def mono22(x):
    return signal.resample_poly(x.mean(1), 147, 320).astype(np.float32)        # 48k -> 22.05k


def frames_db(x, win, hop):
    """K-weighted, channel-summed short-term level per frame (dB, LUFS-like, ungated). Frame i covers
    [i*hop, i*hop+win)."""
    p = _blocks(kweight(x), win, hop)
    return -0.691 + 10 * np.log10(p + 1e-15)


def rnd(v, k=1):
    return None if v is None or (isinstance(v, float) and not np.isfinite(v)) else round(float(v), k)


# ---------------------------------------------------------------- separation
_DEMUCS = {}


def separate(path, work, device="cpu"):
    """Demucs htdemucs two-stem split -> (vocals, accompaniment) at 48 kHz stereo, cached in `work`."""
    key = os.path.splitext(os.path.basename(path))[0]
    vp, ap = os.path.join(work, key + ".vocals.npy"), os.path.join(work, key + ".accomp.npy")
    if os.path.exists(vp) and os.path.exists(ap):
        return np.load(vp).astype(np.float64), np.load(ap).astype(np.float64)
    import torch
    from demucs.pretrained import get_model
    from demucs.apply import apply_model
    torch.set_num_threads(max(1, (os.cpu_count() or 4) - 2))
    if "m" not in _DEMUCS:
        _DEMUCS["m"] = get_model("htdemucs"); _DEMUCS["m"].eval()
    model = _DEMUCS["m"]
    x = decode(path, model.samplerate)
    wav = torch.tensor(x.T, dtype=torch.float32)
    ref = wav.mean(0); mu, sd = ref.mean(), ref.std() + 1e-8
    with torch.no_grad():
        src = apply_model(model, ((wav - mu) / sd)[None], device=device, shifts=1, split=True, overlap=0.25,
                          progress=False)[0]
    src = src * sd
    vi = model.sources.index("vocals")
    voc = src[vi].numpy().T.astype(np.float64)
    acc = (src.sum(0) - src[vi]).numpy().T.astype(np.float64)
    voc, acc = to48(voc, model.samplerate), to48(acc, model.samplerate)
    os.makedirs(work, exist_ok=True)
    np.save(vp, voc.astype(np.float32)); np.save(ap, acc.astype(np.float32))
    return voc, acc


# ---------------------------------------------------------------- tagger
class Tagger:
    def __init__(self, device="cpu"):
        from panns_inference import AudioTagging, SoundEventDetection, labels
        self.at = AudioTagging(checkpoint_path=AT_CKPT, device=device)
        self.sed = None
        self.device = device
        self.labels = list(labels)
        self.ix = {l: i for i, l in enumerate(self.labels)}
        self.kind_of = {}
        for k, names in SFX_KINDS.items():
            for nme in names:
                if nme in self.ix:
                    self.kind_of[self.ix[nme]] = k
        self.sfx_idx = sorted(self.kind_of)

    def _sedm(self):
        if self.sed is None:
            from panns_inference import SoundEventDetection
            self.sed = SoundEventDetection(checkpoint_path=SED_CKPT, device=self.device)
        return self.sed

    @staticmethod
    def to32(x48):
        return signal.resample_poly(x48.mean(1), 2, 3).astype(np.float32)

    def clips(self, x48, spans):
        """Clipwise AudioSet probabilities for each (start, end) span (seconds), batched."""
        y = self.to32(x48); out = []
        for i in range(0, len(spans), 16):
            batch = []
            L = max(int(round((b - a) * 32000)) for a, b in spans[i:i + 16])
            for a, b in spans[i:i + 16]:
                seg = y[max(0, int(a * 32000)):max(0, int(b * 32000))][:L]
                batch.append(np.pad(seg, (0, max(0, L - len(seg)))))
            co, _ = self.at.inference(np.stack(batch))
            out.extend(list(co))
        return np.array(out)

    def framewise(self, x48):
        fw = self._sedm().inference(self.to32(x48)[None])          # (1, T, 527), 100 frames/s
        return fw[0]

    def named(self, p, names, k=8, floor=0.0):
        rows = [(n, float(p[self.ix[n]])) for n in names if n in self.ix]
        rows.sort(key=lambda r: -r[1])
        return [[n, round(v, 3)] for n, v in rows[:k] if v >= floor]

    def top(self, p, k=5):
        idx = np.argsort(p)[::-1][:k]
        return [[self.labels[i], round(float(p[i]), 3)] for i in idx]

    def sfx_kind(self, p):
        if not self.sfx_idx:
            return None, 0.0, None
        i = max(self.sfx_idx, key=lambda j: p[j])
        return self.kind_of[i], float(p[i]), self.labels[i]


# ---------------------------------------------------------------- speech
def intervals_from_mask(m, hop):
    d = np.diff(np.concatenate([[0], m.astype(int), [0]]))
    a, b = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    return [[i * hop, j * hop] for i, j in zip(a, b)]


def speech_intervals(voice, words=None, merge_gap=0.15):
    if words:
        iv = sorted([w["s"], w["e"]] for w in words)
    else:
        hop = 0.01
        L = frames_db(voice, 0.03, hop)
        thr = max(np.percentile(L, 95) - 26, -60)
        m = L > thr
        iv = intervals_from_mask(m, hop)
        iv = [[a + 0.01, b + 0.02] for a, b in iv]
    out = []
    for a, b in iv:
        if out and a - out[-1][1] < merge_gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [[round(a, 3), round(b, 3)] for a, b in out if b - a >= 0.08]


def mask_of(iv, n, pad_a=0.0, pad_b=0.0):
    m = np.zeros(n, bool)
    for a, b in iv:
        m[max(0, int((a - pad_a) * SR)):max(0, int((b + pad_b) * SR))] = True
    return m


def voice_ref_level(voice, words, iv):
    """Median loudest-100 ms per word (whisper words) or per 0.3 s speech chunk: the SFX reference level."""
    segs = [(w["s"], w["e"]) for w in words if w["e"] - w["s"] >= 0.1] if words else \
        [(t, min(b, t + 0.3)) for a, b in iv for t in np.arange(a, b - 0.1, 0.3)]
    v = [short_peak(voice[int(a * SR):int(b * SR) + 1]) for a, b in segs]
    return float(np.median(v)) if v else None


def vocal_onsets(voice):
    import librosa
    y = mono22(voice)
    return librosa.onset.onset_detect(y=y, sr=22050, units="time", hop_length=128, backtrack=False)


# ---------------------------------------------------------------- music features
def tempo_features(bed):
    import librosa
    y = mono22(bed); sr = 22050; hop = 256
    if np.sqrt(np.mean(y ** 2)) < 1e-5:
        return {"bpm": None, "beat_ac": 0.0, "bpm_candidates": [], "kick_bpm": None, "beats": [],
                "percussive_ratio": None}
    oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    o = oenv - oenv.mean()
    ac = np.correlate(o, o, "full")[len(o) - 1:]
    ac = ac / (ac[0] + 1e-12)
    lags = np.arange(len(ac)) * hop / sr
    cands = []
    for lo, hi in ((55, 80), (80, 115), (115, 160)):
        sel = np.flatnonzero((lags > 60 / hi) & (lags < 60 / lo))
        if len(sel):
            i = sel[np.argmax(ac[sel])]
            cands.append((60 / lags[i], float(ac[i])))
    # prefer the 80-160 octave band unless a slower candidate is clearly stronger
    best = max(cands, key=lambda c: c[1] * (1.15 if 80 <= c[0] <= 160 else 1.0)) if cands else (None, 0.0)
    tempo, beats = librosa.beat.beat_track(onset_envelope=oenv, sr=sr, hop_length=hop,
                                           start_bpm=best[0] or 100, tightness=200)
    bt = librosa.frames_to_time(beats, sr=sr, hop_length=hop)
    low = signal.sosfilt(signal.butter(4, 150 / (sr / 2), output="sos"), y).astype(np.float32)
    kon = librosa.onset.onset_detect(y=low, sr=sr, units="time", hop_length=128, backtrack=False)
    kick = None
    if len(kon) > 8:
        ioi = np.diff(kon); ioi = ioi[(ioi > 0.25) & (ioi < 1.6)]
        if len(ioi) > 4:
            h, e = np.histogram(ioi, bins=np.arange(0.25, 1.6, 0.02))
            p = e[np.argmax(h)] + 0.01
            kick = 60 / p
    H, P = librosa.effects.hpss(y)
    return {"bpm": rnd(best[0], 1), "beat_ac": rnd(best[1], 3), "bpm_candidates": [[rnd(b, 1), rnd(a, 3)] for b, a in cands],
            "beat_tracker_bpm": rnd(float(np.atleast_1d(tempo)[0]), 1), "kick_ioi_bpm": rnd(kick, 1),
            "beats": [round(float(b), 3) for b in bt],
            "percussive_ratio": rnd(float(np.sum(P ** 2) / (np.sum(y ** 2) + 1e-12)), 3)}


def spectral_features(bed, present):
    import librosa
    from music_fetch import key_of
    y = mono22(bed); sr = 22050
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=512)) ** 2
    f = librosa.fft_frequencies(sr=sr, n_fft=2048)
    e = S.sum(0)
    t = librosa.frames_to_time(np.arange(S.shape[1]), sr=sr, hop_length=512)
    pm = np.interp(t, np.arange(len(present)) / SR, present.astype(float)) > 0.5 if present is not None else np.ones(len(t), bool)
    pm &= e > np.percentile(e, 20)
    if pm.sum() < 10:
        pm = e > 0
    cent = (f[:, None] * S).sum(0) / (e + 1e-12)
    bands = {}
    tot = S[:, pm].sum()
    for nm, lo, hi in (("<150", 0, 150), ("150-500", 150, 500), ("500-2k", 500, 2000), ("2k-5k", 2000, 5000),
                       (">5k", 5000, 1e9)):
        bands[nm] = rnd(S[(f >= lo) & (f < hi)][:, pm].sum() / (tot + 1e-12), 3)
    kpc, mode, name = key_of(y, sr)
    ch = librosa.feature.chroma_cqt(y=y, sr=sr).mean(1)
    return {"centroid_hz": rnd(np.average(cent[pm], weights=e[pm]), 0),
            "centroid_median_hz": rnd(np.median(cent[pm]), 0), "band_energy": bands, "key": name,
            "chroma_peakiness": rnd(float(ch.max() / (ch.mean() + 1e-9)), 2)}


def stereo_width(bed, present):
    x = bed[present] if present is not None and present.sum() > SR else bed
    M = (x[:, 0] + x[:, 1]) / 2; S = (x[:, 0] - x[:, 1]) / 2
    em, es = np.sum(M ** 2) + 1e-15, np.sum(S ** 2) + 1e-15
    c = np.sum(x[:, 0] * x[:, 1]) / np.sqrt(np.sum(x[:, 0] ** 2) * np.sum(x[:, 1] ** 2) + 1e-15)
    return {"side_mid_db": rnd(10 * np.log10(es / em), 1), "lr_corr": rnd(c, 3)}


# ---------------------------------------------------------------- SFX detection
def event_features(bed, a, b, pk):
    import librosa
    i0, i1 = int(max(0, a) * SR), int(b * SR)
    seg = bed[i0:i1].mean(1)
    if len(seg) < 512:
        seg = np.pad(seg, (0, 512 - len(seg)))
    S = np.abs(librosa.stft(seg.astype(np.float32), n_fft=1024, hop_length=240)) ** 2
    f = librosa.fft_frequencies(sr=SR, n_fft=1024)
    e = S.sum(0) + 1e-12
    cent = (f[:, None] * S).sum(0) / e
    w = e / e.sum()
    half = len(cent) // 2
    c0 = np.average(cent[:max(1, half)], weights=w[:max(1, half)])
    c1 = np.average(cent[half:], weights=w[half:]) if len(cent) > 1 else c0
    flat = float(np.exp(np.mean(np.log(S + 1e-12), 0)).mean() / (S.mean(0).mean() + 1e-12))
    sm = S.sum(1)
    peaky = float(np.sort(sm)[-3:].sum() / (sm.sum() + 1e-12))
    low = float(sm[f < 200].sum() / (sm.sum() + 1e-12))
    rise = (pk - a) / max(1e-3, b - a)
    return {"centroid_hz": round(float(np.average(cent, weights=w))), "centroid_first_half": round(float(c0)),
            "centroid_second_half": round(float(c1)), "flatness": round(flat, 3), "tonal_peak_share": round(peaky, 3),
            "low_share": round(low, 3), "rise_fraction": round(float(rise), 2)}


def dsp_kind(ev):
    d, ft = ev["dur"], ev["feat"]
    if ft["low_share"] > 0.55:
        return "impact/boom"
    if ft["tonal_peak_share"] > 0.25 and ft["flatness"] < 0.08 and d >= 0.08:
        return "ding/chime"
    if d >= 0.6 and ft["rise_fraction"] > 0.7:
        return "riser"
    if d < 0.14:
        return "pop/click"
    if ft["flatness"] > 0.12 and d >= 0.2:
        return "whoosh"
    return "hit"


def detect_sfx(bed, speech_mask, tagger=None, beats=None, beat_ac=0.0):
    """Transients that stand out of the bed: >= 8 dB over the 2 s median level and >= 4 dB over the bed's own
    local 95th-percentile peaks (so regular kicks/hats are not counted), plus SED tagger SFX-class hits."""
    hop, win = 0.01, 0.05
    L = frames_db(bed, win, hop)
    if not len(L):
        return [], []
    med = median_filter(L, size=int(2.0 / hop), mode="nearest")
    exc = L - med
    pk, _ = signal.find_peaks(exc, height=8, distance=int(0.15 / hop))
    cands = []
    for i in pk:
        lo, hi = max(0, i - 300), min(len(L), i + 300)
        local = np.concatenate([L[lo:max(lo, i - 50)], L[min(hi, i + 50):hi]])
        if len(local) < 50:
            continue
        p95 = np.percentile(local, 95)
        if L[i] - p95 < 4:
            continue
        a = i
        while a > 0 and exc[a - 1] > 3 and i - a < 150:
            a -= 1
        b = i
        while b < len(L) - 1 and exc[b + 1] > 3 and b - i < 150:
            b += 1
        cands.append({"a": a * hop, "b": b * hop + win, "pk": i * hop + win / 2, "src": "dsp",
                      "excess_db": round(float(exc[i]), 1), "over_local_peaks_db": round(float(L[i] - p95), 1)})
    sed_hits = []
    if tagger is not None:
        fw = tagger.framewise(bed)
        for j in tagger.sfx_idx:
            m = fw[:, j] > 0.3
            for a, b in intervals_from_mask(m, 0.01):
                if b - a >= 0.05:
                    sed_hits.append({"a": a, "b": b, "pk": a + 0.01 * int(np.argmax(fw[int(a * 100):int(b * 100) + 1, j])),
                                     "src": "sed", "sed_label": tagger.labels[j],
                                     "sed_p": round(float(fw[int(a * 100):int(b * 100) + 1, j].max()), 3)})
    evs = sorted(cands + sed_hits, key=lambda e: e["a"])
    merged = []
    for e in evs:
        if merged and e["a"] <= merged[-1]["b"] + 0.1:
            m = merged[-1]
            m["b"] = max(m["b"], e["b"]); m["src"] = "+".join(sorted(set(m["src"].split("+")) | {e["src"]}))
            for k in ("sed_label", "sed_p", "excess_db", "over_local_peaks_db"):
                if k in e and (k not in m or (k == "sed_p" and e[k] > m.get(k, 0))):
                    m[k] = e[k]
            if e["src"] == "dsp" and "dsp" not in m.get("_pk_from", ""):
                m["pk"] = e["pk"]; m["_pk_from"] = "dsp"
        else:
            merged.append(dict(e))
    for e in merged:
        e["dur"] = round(e["b"] - e["a"], 3)
        e["feat"] = event_features(bed, e["a"], e["b"], e["pk"])
        e["dsp_kind"] = dsp_kind(e)
    if tagger is not None and merged:
        P = tagger.clips(bed, [(e["pk"] - 0.4, e["pk"] + 0.6) for e in merged])
        for e, p in zip(merged, P):
            k, pv, lab = tagger.sfx_kind(p)
            e["tag_top"] = tagger.top(p, 4); e["tag_sfx"] = [lab, round(pv, 3)]
            e["kind"] = k if pv >= 0.08 else e["dsp_kind"]
            e["_speechy"] = sum(float(p[tagger.ix[s]]) for s in SPEECHY if s in tagger.ix)
            e["_sfxp"] = pv
            e["_music_top"] = e["tag_top"][0][0] in INSTR + GENRE + ["Music"]
    else:
        for e in merged:
            e["kind"] = e["dsp_kind"]
    kept, rejected = [], []
    for e in merged:
        i = int(e["pk"] * SR)
        talking = bool(speech_mask[max(0, i - 2400):i + 2400].any()) if i < len(speech_mask) else False
        on_beat = bool(beats is not None and len(beats) and beat_ac >= 0.2 and np.min(np.abs(np.asarray(beats) - e["pk"])) < 0.05)
        why = None
        if e["src"] == "sed" and e.get("sed_p", 0) < 0.5 and e.get("_sfxp", 0) < 0.15:
            why = "weak tagger-only hit"
        elif talking and e.get("_speechy", 0) > 0.5 and e.get("_sfxp", 0) < 0.1:
            why = "voice leaking into the accompaniment stem"
        elif e["src"] == "dsp" and on_beat and e.get("_music_top") and e.get("_sfxp", 0) < 0.1:
            why = "a hit of the music bed itself (on the beat, tagged as an instrument)"
        e["on_beat"] = on_beat
        for k in [k for k in e if k.startswith("_")]:
            e.pop(k)
        for k in ("a", "b", "pk"):
            e[k] = round(e[k], 3)
        (rejected if why else kept).append({**e, **({"rejected": why} if why else {})})
    return kept, rejected


def placement(ev, iv, onsets, dur):
    t = ev["pk"]
    talking = any(a - 0.05 <= t <= b + 0.05 for a, b in iv)
    ph_start = any(-0.35 <= a - t <= 0.35 for a, b in iv)
    ph_end = any(-0.1 <= t - b <= 0.4 for a, b in iv)
    near_on = float(np.min(np.abs(onsets - t))) if len(onsets) else None
    zone = "hook (first 3 s)" if t < 3 else "end (last 4 s)" if t > dur - 4 else "body"
    return {"over_speech": talking, "in_pause": not talking, "at_phrase_start": ph_start, "at_phrase_end": ph_end,
            "near_word_onset_ms": None if near_on is None else round(near_on * 1000), "zone": zone}


# ---------------------------------------------------------------- bed contour
def bed_contour(bed, sfx_events, iv, dur):
    hop = 0.1
    L = frames_db(bed, 0.4, hop)
    t = np.arange(len(L)) * hop + 0.2
    m = np.ones(len(L), bool)
    for e in sfx_events:
        m &= ~((t > e["a"] - 0.3) & (t < e["b"] + 0.3))
    Lc = np.interp(t, t[m], L[m]) if m.sum() > 3 else L
    sm = median_filter(Lc, size=11, mode="nearest")                     # ~1 s
    s0 = iv[0][0] if iv else 0; s1 = iv[-1][1] if iv else dur
    body_sel = (t >= s0) & (t <= s1)
    body = float(np.median(sm[body_sel])) if body_sel.any() else float(np.median(sm))
    present = sm > body - 12
    entry = float(t[np.argmax(present)]) - 0.2 if present.any() else None
    exit_ = float(t[len(present) - 1 - np.argmax(present[::-1])]) + 0.2 if present.any() else None
    stops = []
    if entry is not None:
        inner = (t > entry + 0.3) & (t < exit_ - 0.3)
        for a, b in intervals_from_mask(~present & inner, hop):
            if b - a >= 0.3:
                stops.append({"start": round(a + 0.2, 2), "end": round(b + 0.2, 2),
                              "depth_db": round(float(body - sm[int(a / hop):int(b / hop) + 1].min()), 1)})
    c2 = uniform_filter1d(Lc, 21, mode="nearest")                       # 2 s mean
    k = 20
    moves = []
    if len(c2) > 2 * k:
        d = c2[k:] - c2[:-k]
        for sign, name in ((1, "swell"), (-1, "drop")):
            sel = sign * d > 4
            for a, b in intervals_from_mask(sel, hop):
                i = int(a / hop) + int(np.argmax(sign * d[int(a / hop):int(b / hop) + 1]))
                moves.append({"type": name, "from": round(float(t[i]), 1), "to": round(float(t[i] + 2.0), 1),
                              "change_db": round(float(d[i]), 1)})
    first2 = float(np.median(sm[t < min(2.0, dur)])) - body if (t < 2.0).any() else None
    last2 = float(np.median(sm[t > dur - 2.0])) - body if (t > dur - 2.0).any() else None
    last03 = float(np.median(Lc[t > dur - 0.4])) - body if (t > dur - 0.4).any() else None
    after = (t > s1 + 0.1)
    tail_after_speech = float(np.median(sm[after])) - body if after.sum() >= 3 else None
    end_gap = dur - exit_ if exit_ is not None else None
    if end_gap is not None and end_gap > 0.4:
        end_style = "bed stops before the video ends"
    elif last03 is not None and last03 > -4:
        end_style = "bed runs to the last frame (hard cut with the video)"
    elif last2 is not None and last2 < -6:
        end_style = "fade-out over the last ~2 s"
    else:
        end_style = "short tail"
    return {"t": t, "level": Lc, "smooth": sm, "body_lufs": body, "entry_s": rnd(entry, 2),
            "exit_s": rnd(exit_, 2), "end_gap_s": rnd(end_gap, 2), "stops": stops, "moves": moves,
            "intro_first2s_vs_body_db": rnd(first2), "outro_last2s_vs_body_db": rnd(last2),
            "last_400ms_vs_body_db": rnd(last03), "after_last_word_vs_body_db": rnd(tail_after_speech),
            "end_style": end_style}


def switches(bed, present_t, dur):
    """Timbre/harmony novelty between the 3 s before and after each point (MFCC + chroma). z >= 4 = likely switch."""
    import librosa
    y = mono22(bed); sr = 22050; hop = 512
    if len(y) < sr * 8:
        return []
    mf = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13, hop_length=hop)
    ch = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop)
    F = np.vstack([(mf - mf.mean(1, keepdims=True)) / (mf.std(1, keepdims=True) + 1e-9), ch * 3])
    fps = sr / hop; w = int(3 * fps); step = int(0.5 * fps)
    nov, tt = [], []
    for c in range(w, F.shape[1] - w, step):
        A, B = F[:, c - w:c].mean(1), F[:, c:c + w].mean(1)
        nov.append(1 - np.dot(A, B) / (np.linalg.norm(A) * np.linalg.norm(B) + 1e-9)); tt.append(c / fps)
    nov = np.array(nov)
    if len(nov) < 5:
        return []
    medv = np.median(nov); mad = np.median(np.abs(nov - medv)) + 1e-9
    z = (nov - medv) / (1.4826 * mad)
    pk, _ = signal.find_peaks(z, height=3, distance=6)
    out = [{"t": round(tt[i], 1), "z": round(float(z[i]), 1), "novelty": round(float(nov[i]), 3)} for i in pk]
    out.sort(key=lambda r: -r["z"])
    return out[:4]


# ---------------------------------------------------------------- one reel
def drop_plosives(kept, rej, words):
    """A sub-only "boom" in the body that starts on a p/b word is usually the creator's mic pop, which Demucs files under
    the accompaniment: reject it (hook events in the first 1.5 s are kept: openers there are deliberate)."""
    still = []
    for e in kept:
        near = [w for w in words if -0.12 <= w["s"] - e["a"] <= max(0.12, e["pk"] - e["a"] + 0.05)]
        plos = [w for w in near if w["w"].strip(" \"'(-").lower()[:1] in ("p", "b")]
        if (e.get("feat") or {}).get("low_share", 0) > 0.9 and e["pk"] > 1.5 and e["placement"]["over_speech"] and plos:
            rej.append({**e, "rejected": f"possible mic pop on '{plos[0]['w']}' leaking into the accompaniment stem"})
        else:
            still.append(e)
    return still, rej


def analyze(name, voice, bed, words=None, tagger=None, known_sfx=None):
    n = min(len(voice), len(bed)); voice, bed = voice[:n], bed[:n]
    dur = n / SR
    iv = speech_intervals(voice, words)
    sp = mask_of(iv, n)
    tf = tempo_features(bed)
    kept, rej = detect_sfx(bed, sp, tagger, tf["beats"], tf["beat_ac"] or 0.0)
    if known_sfx is not None:                     # our own mix: SFX times are known exactly
        kept_eval = kept
        kept = []                                 # hits within 0.6 s (a whoosh into a boom) are one event,
        for h in sorted(known_sfx, key=lambda h: h["t"]):   # as the reference detector would hear them
            if kept and h["t"] - kept[-1]["pk"] <= 0.6:
                e = kept[-1]; e["b"] = max(e["b"], h["t"] + 0.35); e["dur"] = round(e["b"] - e["a"], 3)
                if h.get("rel_db", -99) > e["_rel"]:
                    e["kind"], e["_rel"] = h["kind"], h.get("rel_db", -99)
                continue
            kept.append({"a": h["t"] - 0.15, "b": h["t"] + 0.35, "pk": h["t"], "kind": h["kind"], "src": "plan",
                         "dur": 0.5, "_rel": h.get("rel_db", -99)})
        for e in kept:
            e.pop("_rel")
    sfxm = np.zeros(n, bool)
    for e in kept:
        sfxm[max(0, int((e["a"] - 0.05) * SR)):int((e["b"] + 0.05) * SR)] = True
    con = bed_contour(bed, kept, iv, dur)
    present = np.repeat(con["smooth"] > con["body_lufs"] - 12, int(0.1 * SR))[:n]
    present = np.pad(present, (0, n - len(present)))
    mix_l = lufs(voice + bed)
    norm = -14.0 - mix_l
    v_under = lufs(voice[sp]) if sp.sum() > SR else lufs(voice)
    under_sel = sp & ~sfxm
    b_under = lufs(bed[under_sel]) if under_sel.sum() > SR * 0.5 else None
    s0 = iv[0][0] if iv else 0; s1 = iv[-1][1] if iv else dur
    pause = mask_of([[b + 0.12, a - 0.06] for (_, b), (a, _) in zip(iv[:-1], iv[1:]) if a - b >= 0.3], n)
    pause &= ~sfxm
    b_pause = lufs(bed[pause]) if pause.sum() > SR * 0.4 else None
    lowf = signal.sosfilt(signal.butter(4, 150 / (SR / 2), output="sos"), bed, axis=0)
    bl_u = lufs(lowf[under_sel]) if under_sel.sum() > SR * 0.5 else None
    bl_p = lufs(lowf[pause]) if pause.sum() > SR * 0.4 else None
    vref = voice_ref_level(voice, words, iv)
    onsets = np.array([w["s"] for w in words]) if words else vocal_onsets(voice)
    for e in kept:
        e["level_vs_voice_db"] = rnd(short_peak(bed[int(e["a"] * SR):int(e["b"] * SR) + 1]) - vref) if vref else None
        e["placement"] = placement(e, iv, onsets, dur)
    if words and known_sfx is None:
        kept, rej = drop_plosives(kept, rej, words)
    rec = {
        "name": name, "duration_s": rnd(dur, 2), "words_source": "whisper" if words else "voice-stem VAD",
        "speech_intervals": len(iv), "speech_share": rnd(sp.mean(), 3),
        "inner_pauses_ge_300ms": int(sum(1 for (_, b), (a, _) in zip(iv[:-1], iv[1:]) if a - b >= 0.3)),
        "loudness": {"mix_lufs": rnd(mix_l), "voice_lufs_while_talking": rnd(v_under),
                     "bed_lufs_while_talking": rnd(b_under), "bed_lufs_in_pauses": rnd(b_pause),
                     "bed_under_speech_lufs_at_m14": rnd(b_under + norm) if b_under is not None else None,
                     "bed_below_voice_db": rnd(v_under - b_under) if b_under is not None else None,
                     "duck_db": rnd(b_pause - b_under) if b_pause is not None and b_under is not None else None,
                     "duck_db_below_150hz": rnd(bl_p - bl_u) if bl_p is not None and bl_u is not None else None,
                     "voice_ref_100ms": rnd(vref)},
        "tempo": {k: v for k, v in tf.items() if k != "beats"},
        "spectral": spectral_features(bed, present),
        "stereo": stereo_width(bed, present),
        "contour": {k: v for k, v in con.items() if k not in ("t", "level", "smooth")},
        "switch_candidates": switches(bed, None, dur),
        "sfx": {"count": len(kept), "per_min": rnd(len(kept) / (dur / 60), 1), "events": kept,
                "rejected": rej if known_sfx is None else [],
                "kinds": dict(sorted({k: sum(1 for e in kept if e.get("kind") == k)
                                      for k in {e.get("kind") for e in kept}}.items()))},
    }
    if known_sfx is not None:
        rec["sfx"]["detected_by_dsp"] = len(kept_eval)
    if tagger is not None:
        spans = [(a, a + 4.0) for a in np.arange(0, max(0.01, dur - 4.0) + 1e-6, 2.0)]
        P = tagger.clips(bed, spans)
        w = np.array([max(0.0, lufs(bed[int(a * SR):int(min(b, dur) * SR)]) - (con["body_lufs"] - 12)) for a, b in spans])
        w = w if w.sum() > 0 else np.ones(len(spans))
        p = (P * w[:, None]).sum(0) / w.sum()
        rec["tags"] = {"genre": tagger.named(p, GENRE, 6), "mood": tagger.named(p, MOOD, 7),
                       "instrument": tagger.named(p, INSTR, 8), "top_overall": tagger.top(p, 6),
                       "_vec": {nm: round(float(p[tagger.ix[nm]]), 4) for nm in GENRE + MOOD + INSTR if nm in tagger.ix}}
    vu = v_under
    rel = con["level"] - vu
    tn = con["t"] / dur
    grid = np.linspace(0, 1, 101)
    contour = {"t_norm": grid.round(3).tolist(),
               "bed_vs_voice_db": np.round(np.interp(grid, tn, median_filter(rel, 5, mode="nearest")), 1).tolist(),
               "t_s": np.round(con["t"][::2], 2).tolist(), "bed_vs_voice_db_s": np.round(rel[::2], 1).tolist(),
               "sfx": [[round(e["pk"] / dur, 4), round(e["pk"], 2), e.get("kind"), e.get("level_vs_voice_db")] for e in kept]}
    return rec, contour


# ---------------------------------------------------------------- house style
def wstats(vals, w):
    v = np.array([x for x in vals if x is not None], float)
    ww = np.array([wi for x, wi in zip(vals, w) if x is not None], float)
    if not len(v):
        return None
    o = np.argsort(v); v, ww = v[o], ww[o]
    c = np.cumsum(ww) / ww.sum()
    q = lambda p: float(np.interp(p, c - ww / ww.sum() / 2, v))
    return {"median": rnd(q(0.5), 1), "p25": rnd(q(0.25), 1), "p75": rnd(q(0.75), 1), "min": rnd(v.min(), 1),
            "max": rnd(v.max(), 1), "n": int(len(v))}


def house_style(recs, weights, primary, name):
    w = [weights.get(r["name"], 1.0) for r in recs]
    g = lambda f: wstats([f(r) for r in recs], w)
    pr = next((r for r in recs if r["name"] == primary), None)
    L = lambda k: (lambda r: r["loudness"][k])
    hs = {
        "bed_under_speech_lufs_at_m14": g(L("bed_under_speech_lufs_at_m14")),
        "bed_below_voice_db": g(L("bed_below_voice_db")),
        "duck_db": g(L("duck_db")), "duck_db_below_150hz": g(L("duck_db_below_150hz")),
        "bpm": g(lambda r: r["tempo"]["bpm"] if (r["tempo"]["beat_ac"] or 0) >= 0.15 else None),
        "beat_ac": g(lambda r: r["tempo"]["beat_ac"]), "percussive_ratio": g(lambda r: r["tempo"]["percussive_ratio"]),
        "centroid_hz": g(lambda r: r["spectral"]["centroid_hz"]),
        "side_mid_db": g(lambda r: r["stereo"]["side_mid_db"]), "lr_corr": g(lambda r: r["stereo"]["lr_corr"]),
        "sfx_per_min": g(lambda r: r["sfx"]["per_min"]),
        "sfx_level_vs_voice_db": wstats([e["level_vs_voice_db"] for r in recs for e in r["sfx"]["events"]],
                                        [weights.get(r["name"], 1.0) for r in recs for e in r["sfx"]["events"]]),
        "stops_per_reel": g(lambda r: len(r["contour"]["stops"])),
        "switches_per_reel": g(lambda r: sum(1 for s in r["switch_candidates"] if s["z"] >= 4)),
        "intro_first2s_vs_body_db": g(lambda r: r["contour"]["intro_first2s_vs_body_db"]),
        "outro_last2s_vs_body_db": g(lambda r: r["contour"]["outro_last2s_vs_body_db"]),
        "entry_s": g(lambda r: r["contour"]["entry_s"]), "end_gap_s": g(lambda r: r["contour"]["end_gap_s"]),
    }
    kinds = {}
    for r, wi in zip(recs, w):
        for e in r["sfx"]["events"]:
            kinds[e.get("kind")] = kinds.get(e.get("kind"), 0) + wi
    tot = sum(kinds.values()) or 1
    hs["sfx_palette_weighted_share"] = {k: round(v / tot, 2) for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])}
    pl = {}
    evw = [(e, wi) for r, wi in zip(recs, w) for e in r["sfx"]["events"]]
    if evw:
        W = sum(wi for _, wi in evw)
        for key in ("in_pause", "at_phrase_start", "at_phrase_end", "over_speech"):
            pl[key] = round(sum(wi for e, wi in evw if e["placement"][key]) / W, 2)
        for z in ("hook (first 3 s)", "body", "end (last 4 s)"):
            pl["zone: " + z] = round(sum(wi for e, wi in evw if e["placement"]["zone"] == z) / W, 2)
        pl["on_beat"] = round(sum(wi for e, wi in evw if e.get("on_beat")) / W, 2)
        pl["near_word_onset_<=80ms"] = round(sum(wi for e, wi in evw if (e["placement"]["near_word_onset_ms"] or 1e9) <= 80) / W, 2)
    hs["sfx_placement_share"] = pl
    hook = [(r, wi) for r, wi in zip(recs, w)]
    hs["hook_sfx_share"] = round(sum(wi for r, wi in hook if any(e["pk"] < 1.5 for e in r["sfx"]["events"])) / sum(w), 2)
    hs["hook_sfx_level_vs_voice_db"] = wstats([e["level_vs_voice_db"] for r in recs for e in r["sfx"]["events"] if e["pk"] < 1.5],
                                              [weights.get(r["name"], 1.0) for r in recs for e in r["sfx"]["events"] if e["pk"] < 1.5])
    hs["sfx_level_by_kind"] = {}
    for k in {e.get("kind") for r in recs for e in r["sfx"]["events"]}:
        hs["sfx_level_by_kind"][k] = wstats([e["level_vs_voice_db"] for r in recs for e in r["sfx"]["events"] if e.get("kind") == k],
                                            [weights.get(r["name"], 1.0) for r in recs for e in r["sfx"]["events"] if e.get("kind") == k])
    ends = {}
    for r, wi in zip(recs, w):
        ends[r["contour"]["end_style"]] = ends.get(r["contour"]["end_style"], 0) + wi
    hs["end_style_share"] = {k: round(v / sum(ends.values()), 2) for k, v in sorted(ends.items(), key=lambda kv: -kv[1])}
    if all("tags" in r for r in recs):
        keys = recs[0]["tags"]["_vec"].keys()
        vec = {k: sum(r["tags"]["_vec"][k] * wi for r, wi in zip(recs, w)) / sum(w) for k in keys}
        hs["tags_weighted"] = {grp: sorted([[k, round(vec[k], 3)] for k in names if k in vec], key=lambda r: -r[1])[:n]
                               for grp, names, n in (("genre", GENRE, 6), ("mood", MOOD, 7), ("instrument", INSTR, 8))}
        hs["_tag_vec"] = {k: round(v, 4) for k, v in vec.items()}
    hs["primary"] = primary; hs["primary_reel"] = pr and {"bed_under_speech_lufs_at_m14": pr["loudness"]["bed_under_speech_lufs_at_m14"],
                                                          "bed_below_voice_db": pr["loudness"]["bed_below_voice_db"],
                                                          "duck_db": pr["loudness"]["duck_db"], "bpm": pr["tempo"]["bpm"],
                                                          "beat_ac": pr["tempo"]["beat_ac"], "sfx_per_min": pr["sfx"]["per_min"],
                                                          "sfx_kinds": pr["sfx"]["kinds"], "end_style": pr["contour"]["end_style"]}
    return hs


# our SFX kinds (music_mix) -> the profile's SFX families
KIND_FAMILY = {"impact": "impact/boom", "thump": "impact/boom", "boom": "impact/boom", "whoosh_boom": "impact/boom",
               "pop": "pop/click", "tick": "pop/click", "click": "pop/click", "ding": "ding/chime", "whoosh": "whoosh",
               "riser": "riser"}


def make_preset(hs, name, nreels, primary, pw, bed_bias_db=0.5):
    """Music-stage preset: centre values lean on the primary reel; ranges cover the weighted IQR plus tolerance.
    `mix` is the music_mix default block (section level, duck, SFX levels per kind); bed_bias_db corrects Demucs's
    under-read of the bed (calibrated by separating our own mix with known stems)."""
    def rng(k, pad, lo=None, hi=None, centre=None):
        s = hs.get(k)
        if not s:
            return None
        a, b = s["p25"] - pad, s["p75"] + pad
        if centre is not None:
            a, b = min(a, centre - pad), max(b, centre + pad)
        if lo is not None: a = max(a, lo)
        if hi is not None: b = min(b, hi)
        return [round(a, 1), round(b, 1)]
    pr = hs.get("primary_reel") or {}
    lvl = pr.get("bed_under_speech_lufs_at_m14") or hs["bed_under_speech_lufs_at_m14"]["median"]
    c = hs.get("centroid_hz")
    crange = [round(max(40.0, c["p25"] * 0.6)), round(c["p75"] * 1.6)] if c else None
    byk = hs.get("sfx_level_by_kind") or {}
    fam_lvl = {f: (byk.get(f) or {}).get("median") for f in set(KIND_FAMILY.values())}
    levels = {k: round(fam_lvl[f], 1) for k, f in KIND_FAMILY.items() if fam_lvl.get(f) is not None}
    hook_lvl = (hs.get("hook_sfx_level_vs_voice_db") or {}).get("median")
    duck = max(0.0, (hs.get("duck_db") or {}).get("median") or 0.0)
    mix = {"section_level": round(lvl + bed_bias_db, 1), "bed_bias_db": bed_bias_db,
           "section_level_range": [round(lvl + bed_bias_db - 1.5, 1), round(lvl + bed_bias_db + 1.5, 1)],
           "duck": {"depth_db": round(min(duck, 2.0), 1)},
           "sfx": {"levels": levels, "hook_level": hook_lvl}}
    return {"name": name, "measured": datetime.date.today().isoformat(),
            "source": f"{nreels} reference reels, ref_audio_profile.py (Demucs htdemucs + PANNs CNN14), primary {primary} x{pw}",
            "bed": {"level_lufs": round(lvl, 1), "level_range": rng("bed_under_speech_lufs_at_m14", 1.5, centre=lvl),
                    "below_voice_db_range": rng("bed_below_voice_db", 1.5),
                    "duck_db": round(max(0.0, (hs.get("duck_db") or {}).get("median") or 0.0), 1),
                    "duck_db_range": rng("duck_db", 1.5),
                    "bpm_range": rng("bpm", 6), "centroid_hz_range": crange,
                    "side_mid_db_range": rng("side_mid_db", 3),
                    "max_switches": int(round((hs.get("switches_per_reel") or {}).get("median") or 0)),
                    "max_stops": int(round((hs.get("stops_per_reel") or {}).get("median") or 0)),
                    "end_style": next(iter(hs.get("end_style_share", {})), None),
                    "tags": hs.get("tags_weighted")},
            "sfx": {"per_min": (hs.get("sfx_per_min") or {}).get("median"),
                    "per_min_range": rng("sfx_per_min", 1.0, lo=0.0),
                    "level_vs_voice_db": (hs.get("sfx_level_vs_voice_db") or {}).get("median"),
                    "level_range": rng("sfx_level_vs_voice_db", 2.0),
                    "palette": hs.get("sfx_palette_weighted_share"), "placement": hs.get("sfx_placement_share"),
                    "hook_share": hs.get("hook_sfx_share"), "kind_family": KIND_FAMILY},
            "mix": mix}


# ---------------------------------------------------------------- report
def md_report(recs, hs, preset, weights, path, notes=None):
    W = lambda r: weights.get(r["name"], 1.0)
    out = [f"# Reference audio profile: {preset['name']}", "",
           f"Measured {preset['measured']} with `ref_audio_profile.py` on {len(recs)} reels (audio only). "
           "Demucs htdemucs split voice from accompaniment; PANNs CNN14 tagged the accompaniment; levels are BS.1770. "
           "`bed @-14` = bed loudness while talking with the reel normalized to -14 LUFS (same scale as a music_plan "
           "section `level`). SFX dB = loudest 100 ms vs the voice's median per-word loudest 100 ms.", "",
           "## Per reel", "",
           "| reel | w | dur s | bed @-14 LUFS | bed below voice dB | duck dB (all / <150 Hz) | BPM (beat strength) | centroid Hz | side/mid dB | stops | switch z>=4 | SFX (/min) | SFX dB vs voice | end |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in recs:
        lo, tp, c = r["loudness"], r["tempo"], r["contour"]
        sl = [e["level_vs_voice_db"] for e in r["sfx"]["events"] if e.get("level_vs_voice_db") is not None]
        out.append(f"| {r['name']} | {W(r):g} | {r['duration_s']} | {lo['bed_under_speech_lufs_at_m14']} | "
                   f"{lo['bed_below_voice_db']} | {lo['duck_db']} / {lo['duck_db_below_150hz']} | {tp['bpm']} ({tp['beat_ac']}) | "
                   f"{r['spectral']['centroid_hz']} | {r['stereo']['side_mid_db']} | {len(c['stops'])} | "
                   f"{sum(1 for s in r['switch_candidates'] if s['z'] >= 4)} | {r['sfx']['count']} ({r['sfx']['per_min']}) | "
                   f"{(str(round(float(np.median(sl)), 1)) if sl else '-')} | {c['end_style']} |")
    if all("tags" in r for r in recs):
        out += ["", "## Tagger (PANNs CNN14 on the accompaniment, loudness-weighted 4 s windows)", "",
                "| reel | genre | mood | instruments |", "|---|---|---|---|"]
        for r in recs:
            t = r["tags"]
            f = lambda xs, k: ", ".join(f"{a} {b:.2f}" for a, b in xs[:k])
            out.append(f"| {r['name']} | {f(t['genre'], 3)} | {f(t['mood'], 3)} | {f(t['instrument'], 4)} |")
    out += ["", "## Bed contour and SFX events", ""]
    for r in recs:
        c = r["contour"]
        out.append(f"**{r['name']}** ({r['duration_s']} s, key {r['spectral']['key']}, percussive share "
                   f"{r['tempo']['percussive_ratio']}): bed enters {c['entry_s']} s, exits {c['exit_s']} s "
                   f"({c['end_style']}); first 2 s {c['intro_first2s_vs_body_db']} dB vs body, last 2 s "
                   f"{c['outro_last2s_vs_body_db']} dB, after the last word {c['after_last_word_vs_body_db']} dB.")
        if c["stops"]:
            out.append("  Stops: " + "; ".join(f"{s['start']}-{s['end']} s (-{s['depth_db']} dB)" for s in c["stops"]))
        if c["moves"]:
            out.append("  Swells/drops: " + "; ".join(f"{m['type']} {m['from']}->{m['to']} s ({m['change_db']:+} dB)" for m in c["moves"]))
        if r["switch_candidates"]:
            out.append("  Novelty peaks (z>=4 = likely switch): " + ", ".join(f"{s['t']} s z{s['z']}" for s in r["switch_candidates"]))
        for e in r["sfx"]["events"]:
            p = e["placement"]
            where = ("in a pause" if p["in_pause"] else "over speech") + (", phrase start" if p["at_phrase_start"] else "") + \
                    (", phrase end" if p["at_phrase_end"] else "") + f", {p['zone']}"
            lab = (e.get("tag_sfx") or [None])[0]
            out.append(f"  - SFX {e['pk']} s: {e.get('kind')} ({lab or e['dsp_kind']}; {e['src']}), {e['dur']} s, "
                       f"{e.get('level_vs_voice_db')} dB vs voice, {where}, word onset {p['near_word_onset_ms']} ms")
        if r["sfx"]["rejected"]:
            out.append(f"  - ({len(r['sfx']['rejected'])} candidate(s) rejected: " +
                       "; ".join(f"{e['pk']} s {e['rejected']}" for e in r["sfx"]["rejected"][:6]) + ")")
        out.append("")
    out += ["## House style (weighted; primary reel x{})".format(weights.get(hs["primary"], 1)), "",
            "| metric | weighted median | IQR | min..max | primary |", "|---|---|---|---|---|"]
    prm = hs.get("primary_reel") or {}
    for k in ("bed_under_speech_lufs_at_m14", "bed_below_voice_db", "duck_db", "duck_db_below_150hz", "bpm", "beat_ac",
              "percussive_ratio", "centroid_hz", "side_mid_db", "lr_corr", "sfx_per_min", "sfx_level_vs_voice_db",
              "stops_per_reel", "switches_per_reel", "intro_first2s_vs_body_db", "outro_last2s_vs_body_db", "entry_s", "end_gap_s"):
        s = hs.get(k)
        if s:
            out.append(f"| {k} | {s['median']} | {s['p25']}..{s['p75']} | {s['min']}..{s['max']} | {prm.get(k, '')} |")
    out += ["", f"SFX palette (weighted share): {hs.get('sfx_palette_weighted_share')}",
            f"SFX placement (weighted share): {hs.get('sfx_placement_share')}",
            f"Hook SFX (an SFX in the first 1.5 s): {hs.get('hook_sfx_share')} of reels (weighted), level "
            f"{(hs.get('hook_sfx_level_vs_voice_db') or {}).get('median')} dB vs voice",
            "SFX level by kind: " + ", ".join(f"{k} {v['median']} dB (n {v['n']})" for k, v in (hs.get('sfx_level_by_kind') or {}).items() if v),
            f"End style: {hs.get('end_style_share')}"]
    if hs.get("tags_weighted"):
        out.append(f"Tags: {hs['tags_weighted']}")
    if notes:
        out += ["", "## Reading notes", "", open(notes).read().strip()]
    out += ["", "## Preset (music stage)", "", "```json", json.dumps(preset, indent=1), "```", ""]
    open(path, "w").write("\n".join(out))


# ---------------------------------------------------------------- commands
def cmd_profile(x):
    os.makedirs(x.out, exist_ok=True)
    work = x.work or os.path.join(x.out, "_work")
    os.makedirs(work, exist_ok=True)
    tagger = Tagger(x.device) if x.tagger == "panns" else None
    recs, cons = [], {}
    weights = {}
    if x.from_json:                                    # re-derive from saved numbers only (stems long deleted)
        old = json.load(open(x.from_json))
        recs = old["reels"]
        cons = json.load(open(os.path.join(os.path.dirname(x.from_json), "contours.json")))
        weights = {r["name"]: (x.primary_weight if r["name"] == x.primary else 1.0) for r in recs}
        tv = old.get("tag_vectors") or {}
        for r in recs:
            if r["name"] in tv and "tags" in r:
                r["tags"]["_vec"] = tv[r["name"]]
            else:
                r.pop("tags", None)
    for path in ([] if x.from_json else x.files):
        name = os.path.splitext(os.path.basename(path))[0]
        weights[name] = x.primary_weight if name == x.primary else 1.0
        print(f"[{name}] separating", flush=True)
        voc, acc = separate(path, work, x.device)
        words = None
        if x.whisper_py:
            wj = os.path.join(work, name + ".words.json")
            if not os.path.exists(wj):
                wav = os.path.join(work, name + ".vocals.wav")
                subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2",
                                "-i", "-", wav], input=voc.astype(np.float32).tobytes(), check=True)
                subprocess.run([x.whisper_py, "-I", os.path.join(HERE, "transcribe.py"), wav, wj], check=True,
                               capture_output=True)
            d = json.load(open(wj)); words = d["words"] if isinstance(d, dict) else d
        print(f"[{name}] analyzing", flush=True)
        rec, con = analyze(name, voc, acc, words, tagger)
        recs.append(rec); cons[name] = con
        print(f"[{name}] bed@-14 {rec['loudness']['bed_under_speech_lufs_at_m14']}  below voice "
              f"{rec['loudness']['bed_below_voice_db']}  duck {rec['loudness']['duck_db']}  bpm {rec['tempo']['bpm']} "
              f"({rec['tempo']['beat_ac']})  sfx {rec['sfx']['count']}", flush=True)
    hs = house_style(recs, weights, x.primary, x.name)
    preset = make_preset(hs, x.name, len(recs), x.primary, x.primary_weight, x.bed_bias_db)
    tag_vectors = {}
    for r in recs:
        if "tags" in r:
            tag_vectors[r["name"]] = r["tags"].pop("_vec", None)
    vec = hs.pop("_tag_vec", None)
    json.dump({"name": x.name, "weights": weights, "reels": recs, "house_style": hs, "preset": preset,
               "tag_vector": vec, "tag_vectors": tag_vectors}, open(os.path.join(x.out, "profile.json"), "w"), indent=1)
    json.dump(cons, open(os.path.join(x.out, "contours.json"), "w"))
    json.dump(preset, open(os.path.join(x.out, "preset.json"), "w"), indent=1)
    md_report(recs, hs, preset, weights, os.path.join(x.out, "profile.md"), x.notes)
    if not x.keep_stems:
        shutil.rmtree(work, ignore_errors=True)
        print(f"deleted stems in {work}")
    print(f"wrote {x.out}/profile.json, profile.md, preset.json, contours.json")


def cmd_mix(x):
    from scipy.io import wavfile
    res = json.load(open(os.path.join(x.stems, "plan_resolved.json")))
    G = 10 ** (res["master_gain_db"] / 20)
    sp = decode(x.speech) * G
    _, mus = wavfile.read(os.path.join(x.stems, "music_stem.wav")); _, sfx = wavfile.read(os.path.join(x.stems, "sfx_stem.wav"))
    n = min(len(sp), len(mus))
    bed = mus[:n].astype(np.float64) + sfx[:n].astype(np.float64)
    words = json.load(open(x.words)) if x.words else None
    words = (words if isinstance(words, list) else words["words"]) if words else None
    tagger = Tagger(x.device) if x.tagger == "panns" else None
    rec, con = analyze(x.label, sp[:n], bed, words, tagger, known_sfx=res.get("sfx", []))
    if "tags" in rec:
        rec["tags"].pop("_vec", None)
    rec["music_only_lufs_while_talking_at_m14"] = rnd(lufs(mus[:n][mask_of(speech_intervals(sp[:n], words), n)].astype(np.float64)))
    rec["sections"] = len(res.get("sections", [])); rec["tracks"] = list(res.get("tracks", {}).keys())
    json.dump({"record": rec, "contour": con}, open(x.out, "w"), indent=1)
    print(json.dumps({k: rec[k] for k in ("loudness", "tempo", "sfx")}, indent=1)[:3000])


def track_features(path, tagger, seg=None):
    x = decode(path)
    if seg:
        x = x[int(seg[0] * SR):int(seg[1] * SR)]
    tf = tempo_features(x)
    L = frames_db(x, 0.4, 0.1)
    e4 = [round(float(np.median(L[i:i + 40])), 1) for i in range(0, len(L) - 20, 40)]
    rec = {"file": os.path.basename(path), "segment": seg, "duration_s": rnd(len(x) / SR, 1), "lufs": rnd(lufs(x)),
           "tempo": {k: v for k, v in tf.items() if k != "beats"}, "spectral": spectral_features(x, None),
           "stereo": stereo_width(x, None), "energy_4s": e4,
           "short_term_spread_db": rnd(float(np.percentile(L, 90) - np.percentile(L, 10)))}
    if tagger is not None:
        dur = len(x) / SR
        spans = [(a, a + 4.0) for a in np.arange(0, max(0.01, dur - 4.0) + 1e-6, 2.0)]
        p = tagger.clips(x, spans).mean(0)
        rec["tags"] = {"genre": tagger.named(p, GENRE, 5), "mood": tagger.named(p, MOOD, 7),
                       "instrument": tagger.named(p, INSTR, 6)}
        rec["_vec"] = {nm: float(p[tagger.ix[nm]]) for nm in GENRE + MOOD + INSTR if nm in tagger.ix}
    return rec


def score_track(rec, preset, tag_vec):
    """Lower is closer. Range misses in units of the range width, plus 2x (1 - tag cosine similarity)."""
    b = preset["bed"]; parts = {}
    def miss(v, r, nm):
        if v is None or not r:
            return
        w = max(1e-6, r[1] - r[0])
        parts[nm] = round(max(0.0, r[0] - v, v - r[1]) / w, 2)
    bpm = rec["tempo"]["bpm"]
    if bpm and b.get("bpm_range"):          # tempo octave: accept half/double time
        bpm = min([bpm, bpm / 2, bpm * 2], key=lambda v: max(0, b["bpm_range"][0] - v, v - b["bpm_range"][1]))
    miss(bpm, b.get("bpm_range"), "bpm")
    miss(rec["spectral"]["centroid_hz"], b.get("centroid_hz_range"), "centroid")
    miss(rec["stereo"]["side_mid_db"], b.get("side_mid_db_range"), "width")
    if tag_vec and rec.get("_vec"):
        k = sorted(set(tag_vec) & set(rec["_vec"]))
        u = np.array([tag_vec[i] for i in k]); v = np.array([rec["_vec"][i] for i in k])
        cos = float(u @ v / (np.linalg.norm(u) * np.linalg.norm(v) + 1e-9))
        parts["tags"] = round(2 * (1 - cos), 2); rec["tag_cosine"] = round(cos, 3)
    parts["spread"] = round(max(0.0, (rec["short_term_spread_db"] or 0) - 8) / 8, 2)   # steady beds preferred
    return round(sum(parts.values()), 2), parts


def cmd_tracks(x):
    prof = json.load(open(x.preset))
    preset = prof.get("preset", prof)
    tag_vec = prof.get("tag_vector")
    tagger = Tagger(x.device) if x.tagger == "panns" else None
    out = []
    for f in x.files:
        seg = None
        if "@" in f:
            f, s = f.rsplit("@", 1); seg = [float(v) for v in s.split("-")]
        rec = track_features(f, tagger, seg)
        rec["score"], rec["score_parts"] = score_track(rec, preset, tag_vec)
        pv = (prof.get("tag_vectors") or {}).get((prof.get("house_style") or {}).get("primary"))
        if pv and rec.get("_vec"):               # similarity to the primary reel alone, for the tie-breaks
            k = sorted(set(pv) & set(rec["_vec"]))
            u = np.array([pv[i] for i in k]); v = np.array([rec["_vec"][i] for i in k])
            rec["tag_cosine_primary"] = round(float(u @ v / (np.linalg.norm(u) * np.linalg.norm(v) + 1e-9)), 3)
        rec.pop("_vec", None)
        out.append(rec)
        print(f"{rec['file']:28s} score {rec['score']:5.2f} {rec['score_parts']}  bpm {rec['tempo']['bpm']} "
              f"({rec['tempo']['beat_ac']})  cent {rec['spectral']['centroid_hz']}  width {rec['stereo']['side_mid_db']}  "
              f"spread {rec['short_term_spread_db']}  cos {rec.get('tag_cosine')}/{rec.get('tag_cosine_primary')}  {rec.get('tags', {}).get('genre', [])[:2]} {rec.get('tags', {}).get('mood', [])[:2]}",
              flush=True)
    out.sort(key=lambda r: r["score"])
    json.dump(out, open(x.out, "w"), indent=1)


def cmd_plot(x):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    prof = json.load(open(x.profile))
    cons = json.load(open(os.path.join(os.path.dirname(x.profile), "contours.json")))
    preset = prof["preset"]; prim = prof["house_style"]["primary"]
    mixes = [json.load(open(m)) for m in x.mix]
    fig = plt.figure(figsize=(16, 11))
    gs = fig.add_gridspec(3, 2, height_ratios=[1.3, 0.9, 1.0])
    ax = fig.add_subplot(gs[0, :])
    M = np.array([c["bed_vs_voice_db"] for c in cons.values()])
    tn = np.array(next(iter(cons.values()))["t_norm"])
    ax.fill_between(tn, np.percentile(M, 25, 0), np.percentile(M, 75, 0), color="#999", alpha=0.25,
                    label="reference reels IQR")
    for nm, c in cons.items():
        ax.plot(tn, c["bed_vs_voice_db"], color="#222" if nm == prim else "#aaa", lw=2 if nm == prim else 0.7,
                label=f"{nm} (primary)" if nm == prim else None)
    ax.plot(tn, np.median(M, 0), color="#555", lw=1.5, ls="--", label="reference median")
    rng = preset["bed"].get("below_voice_db_range")
    if rng:
        ax.axhspan(-rng[1], -rng[0], color="#2a6", alpha=0.08, label="preset bed-below-voice range")
    cols = ["#1b9e77", "#d95f02", "#7570b3"]
    for m, cl in zip(mixes, cols):
        ax.plot(m["contour"]["t_norm"], m["contour"]["bed_vs_voice_db"], color=cl, lw=2, label=m["record"]["name"])
    ax.set_ylim(-45, 0); ax.set_xlim(0, 1); ax.set_ylabel("bed (music + SFX) minus voice, dB\n(400 ms, SFX masked)")
    ax.set_xlabel("position in reel (0 = start, 1 = end)"); ax.legend(fontsize=8, ncol=3, loc="lower center")
    ax.set_title("Bed level vs voice over the reel")
    ax2 = fig.add_subplot(gs[1, :])
    rows = list(cons.items()) + [(m["record"]["name"], m["contour"]) for m in mixes]
    mk = {"whoosh": ">", "pop/click": "o", "ding/chime": "*", "riser": "^", "impact/boom": "s", "hit": "x"}
    for y, (nm, c) in enumerate(rows):
        ax2.axhline(y, color="#eee", lw=0.5)
        for tnorm, ts, kind, lvl in c["sfx"]:
            s = 30 + 4 * max(0, 25 + (lvl if lvl is not None else -20))
            ax2.scatter(tnorm, y, marker=mk.get(kind, "D"), s=s, color=(cols[(y - len(cons)) % 3] if y >= len(cons) else "#333"))
        n = len(c["sfx"])
        ax2.text(1.005, y, f"{n}", va="center", fontsize=8)
    ax2.set_yticks(range(len(rows))); ax2.set_yticklabels([r[0] for r in rows], fontsize=8)
    ax2.set_xlim(0, 1); ax2.set_title("SFX events over the reel (marker = kind, size = level vs voice; count at right)")
    hs = prof["house_style"]
    mets = [("bed @-14 LUFS", "bed_under_speech_lufs_at_m14", lambda r: r["loudness"]["bed_under_speech_lufs_at_m14"]),
            ("bed below voice dB", "bed_below_voice_db", lambda r: r["loudness"]["bed_below_voice_db"]),
            ("duck dB", "duck_db", lambda r: r["loudness"]["duck_db"]),
            ("SFX / min", "sfx_per_min", lambda r: r["sfx"]["per_min"]),
            ("SFX dB vs voice", "sfx_level_vs_voice_db",
             lambda r: float(np.median([e["level_vs_voice_db"] for e in r["sfx"]["events"] if e.get("level_vs_voice_db") is not None]))
             if any(e.get("level_vs_voice_db") is not None for e in r["sfx"]["events"]) else None)]
    for j, half in enumerate((mets[:3], mets[3:])):
        a = fig.add_subplot(gs[2, j])
        for i, (lab, k, f) in enumerate(half):
            vals = [f(r) for r in prof["reels"]]
            a.scatter([v for v in vals if v is not None], [i] * sum(v is not None for v in vals), color="#aaa", s=25)
            s = hs.get(k)
            if s:
                a.plot([s["p25"], s["p75"]], [i, i], color="#555", lw=6, alpha=0.4, solid_capstyle="butt")
                a.scatter([s["median"]], [i], color="#222", marker="|", s=300)
            for m, cl in zip(mixes, cols):
                v = f(m["record"])
                if v is not None:
                    a.scatter([v], [i + 0.15], color=cl, s=60, marker="D", label=m["record"]["name"] if i == 0 else None)
        a.set_yticks(range(len(half))); a.set_yticklabels([h[0] for h in half]); a.set_ylim(-0.6, len(half) - 0.4)
        a.grid(axis="x", alpha=0.3)
        if j == 0:
            a.legend(fontsize=8)
    a.set_title("grey dots = reference reels, bar = weighted IQR, tick = weighted median", fontsize=9)
    fig.suptitle(f"Mix vs reference style profile: {prof['name']}")
    fig.tight_layout(); fig.savefig(x.out, dpi=110)
    print("wrote", x.out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("profile"); p.add_argument("files", nargs="*"); p.add_argument("--out", required=True)
    p.add_argument("--name", default="reference"); p.add_argument("--primary"); p.add_argument("--primary-weight", type=float, default=3.0)
    p.add_argument("--work"); p.add_argument("--keep-stems", action="store_true"); p.add_argument("--whisper-py")
    p.add_argument("--tagger", default="panns", choices=["panns", "none"]); p.add_argument("--device", default="cpu")
    p.add_argument("--bed-bias-db", type=float, default=0.5, help="Demucs bed under-read to add back (calibrated 0.5 dB)")
    p.add_argument("--notes", help="markdown file appended to profile.md as 'Reading notes'")
    p.add_argument("--from-json", help="re-derive house style/preset/report from an existing profile.json (no audio)")
    m = sub.add_parser("mix"); m.add_argument("--speech", required=True); m.add_argument("--stems", required=True)
    m.add_argument("--out", required=True); m.add_argument("--label", default="mix"); m.add_argument("--words")
    m.add_argument("--tagger", default="none", choices=["panns", "none"]); m.add_argument("--device", default="cpu")
    t = sub.add_parser("tracks"); t.add_argument("files", nargs="+"); t.add_argument("--preset", required=True)
    t.add_argument("--out", required=True); t.add_argument("--tagger", default="panns", choices=["panns", "none"])
    t.add_argument("--device", default="cpu")
    q = sub.add_parser("plot"); q.add_argument("--profile", required=True); q.add_argument("--mix", action="append", default=[])
    q.add_argument("--out", required=True)
    x = ap.parse_args()
    {"profile": cmd_profile, "mix": cmd_mix, "tracks": cmd_tracks, "plot": cmd_plot}[x.cmd](x)


if __name__ == "__main__":
    main()
