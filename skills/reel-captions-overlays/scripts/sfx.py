#!/usr/bin/env python3
"""Procedural sound design for reel graphics: whoosh, pop, ding, tick, riser, impact, thump.

Everything is synthesized from noise and sines with numpy/scipy, so there is no licence question and
nothing to download or commit. Each generator returns (stereo float32 array [n, 2], hit_index): hit_index
is the sample that should land on the graphic's entrance (a whoosh peaks there, a riser ends there, a pop
or ding starts there). Levels are normalized to a -1 dBFS peak; music_mix.py sets the final level.

    python3 sfx.py preview <outdir>      # writes one wav per kind for a quick listen
"""
import sys, os
import numpy as np
from scipy import signal

SR = 48000
_rng = np.random.default_rng(7)


def _norm(x):
    p = np.max(np.abs(x)) or 1.0
    return (x / p * 10 ** (-1 / 20)).astype(np.float32)


def _stereo(m, pan_from=0.0, pan_to=0.0):
    """Mono -> stereo with a linear equal-power pan sweep (-1 left .. +1 right)."""
    p = np.linspace(pan_from, pan_to, len(m))
    a = (p + 1) * np.pi / 4
    return np.stack([m * np.cos(a), m * np.sin(a)], 1)


def _shaped_noise(dur, fc_curve, bw_oct, env):
    """Noise whose spectrum is a moving log-gaussian band centred on fc_curve(u), u in 0..1."""
    n = int(dur * SR)
    x = _rng.standard_normal(n)
    f, t, Z = signal.stft(x, SR, nperseg=1024, noverlap=768)
    u = np.clip(t / dur, 0, 1)
    fc = fc_curve(u)                          # Hz per frame
    lf = np.log2(np.maximum(f, 20))[:, None]
    mask = np.exp(-0.5 * ((lf - np.log2(fc)[None, :]) / bw_oct) ** 2)
    _, y = signal.istft(Z * mask, SR, nperseg=1024, noverlap=768)
    y = y[:n]
    if len(y) < n:
        y = np.pad(y, (0, n - len(y)))
    return y * env(np.linspace(0, 1, n))


def whoosh(dur=0.42, peak=0.62, lo=350, hi=2400, pan=(-0.5, 0.5)):
    """Air whoosh: band sweeps up to `hi` at the peak then back down, panned across."""
    def fc(u):
        a = np.where(u < peak, u / peak, 1 - (u - peak) / (1 - peak))
        return lo * (hi / lo) ** np.clip(a, 0, 1)

    def env(u):
        a = np.where(u < peak, (u / peak) ** 2.2, np.exp(-6 * (u - peak) / (1 - peak)))
        return a

    m = _shaped_noise(dur, fc, 0.55, env)
    return _norm(_stereo(m, *pan)), int(peak * dur * SR)


def pop(f0=820, f1=260, dur=0.09):
    """Soft bubble pop: fast downward sine chirp with a 1 ms attack."""
    n = int(dur * SR); t = np.arange(n) / SR
    f = f1 + (f0 - f1) * np.exp(-t / 0.012)
    ph = 2 * np.pi * np.cumsum(f) / SR
    env = np.minimum(1, t / 0.001) * np.exp(-t / 0.022)
    m = np.sin(ph) * env
    return _norm(_stereo(m)), 0


def tick(dur=0.012):
    """Tiny UI click (high-passed noise burst)."""
    n = int(dur * SR)
    x = _rng.standard_normal(n) * np.exp(-np.arange(n) / (0.0025 * SR))
    b, a = signal.butter(2, 2500 / (SR / 2), 'high')
    return _norm(_stereo(signal.lfilter(b, a, x))), 0


def ding(freq=880.0, dur=0.55):
    """Soft bell / marimba-ish ding plus a tick on the attack. Keep freq below ~1.2 kHz (voice band)."""
    n = int(dur * SR); t = np.arange(n) / SR
    parts = [(1.0, 1.0, 0.30), (2.0, 0.22, 0.16), (3.0, 0.07, 0.09), (4.07, 0.05, 0.05)]
    m = sum(a * np.sin(2 * np.pi * freq * r * t) * np.exp(-t / d) for r, a, d in parts)
    m *= np.minimum(1, t / 0.002)
    tk, _ = tick()
    m[:len(tk)] += 0.25 * tk[:, 0]
    return _norm(_stereo(m)), 0


def riser(dur=1.5, lo=250, hi=5000):
    """Noise riser that ends sharply at its hit point, with a quiet rising sine under it."""
    def fc(u):
        return lo * (hi / lo) ** (u ** 1.6)

    def env(u):
        return (u ** 2.5) * np.minimum(1, (1 - u) / 0.015 + 0.0)

    m = _shaped_noise(dur, fc, 0.7, env)
    n = len(m); t = np.arange(n) / SR; u = t / dur
    f = 140 * (4.0 ** (u ** 1.4))
    m = m / (np.max(np.abs(m)) or 1) + 0.18 * np.sin(2 * np.pi * np.cumsum(f) / SR) * u ** 2
    m[-int(0.012 * SR):] *= np.linspace(1, 0, int(0.012 * SR))
    return _norm(_stereo(m, -0.2, 0.2)), n


def impact(dur=0.8, f0=95, f1=48):
    """Low cinematic hit: pitch-dropping sub sine plus a short low-passed noise thump."""
    n = int(dur * SR); t = np.arange(n) / SR
    f = f1 + (f0 - f1) * np.exp(-t / 0.06)
    sub = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.28)
    nz = _rng.standard_normal(n) * np.exp(-t / 0.035)
    b, a = signal.butter(2, 900 / (SR / 2))
    m = sub + 0.35 * signal.lfilter(b, a, nz)
    m *= np.minimum(1, t / 0.0015)
    return _norm(_stereo(m)), 0


def thump(dur=0.35):
    """Shorter, tighter impact for a button ending."""
    return impact(dur=dur, f0=120, f1=55)


KINDS = {"whoosh": whoosh, "pop": pop, "tick": tick, "ding": ding, "riser": riser,
         "impact": impact, "thump": thump}


def make(kind, **kw):
    return KINDS[kind](**kw)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "preview":
        from scipy.io import wavfile
        os.makedirs(sys.argv[2], exist_ok=True)
        for k, fn in KINDS.items():
            y, _ = fn()
            wavfile.write(os.path.join(sys.argv[2], f"{k}.wav"), SR, y)
        print("wrote", ", ".join(KINDS))
    else:
        print(__doc__)
