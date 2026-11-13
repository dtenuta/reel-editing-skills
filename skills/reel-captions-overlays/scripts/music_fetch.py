#!/usr/bin/env python3
"""Browse, fetch and analyze licensed Mixkit music for the reel music stage.

LICENSED SOURCES ONLY. Music comes from Mixkit (Mixkit Stock Music Free License: free commercial use,
including online ads, no attribution; https://mixkit.co/license/#musicFree) or from your own licensed
library (a local file in music_plan.json). Never use a track whose licence you cannot name.

    music_fetch.py list <tag>                 # list a Mixkit tag catalog: id, title, genre, length
    music_fetch.py get <tag> <id> [--dir D]   # download one track (default dir ~/.cache/reel-music/mixkit)
    music_fetch.py analyze <file.mp3>         # tempo candidates, drop points, energy per bar, key

    music_fetch.py sfx-list <category>        # Mixkit SFX category (whoosh, swoosh, transition, pop, click, ...)
    music_fetch.py sfx-get <id> [--dir D]     # download one Mixkit SFX wav (cache ~/.cache/reel-music/mixkit-sfx)

Files are cached as <dir>/<id>.mp3 with a <id>.json licence sidecar, so music_plan.json can refer to a
track by Mixkit ID and any machine can re-fetch it. Never commit the mp3s to a repo.
A "tag" is either a Mixkit tag (hip-hop, chill, lo-fi, funk, electronic, happy, corporate, cinematic; old tag URLs
redirect to category pages) or a listing path under /free-stock-music/ such as mood/calm, mood/relaxed, mood/dreamy,
chillout, lo-fi-beats, downtempo, ambient, instrument/rhodes. Listings are paginated (?page=N); `catalog` follows them.
Not tags: lofi, beats, upbeat, groovy, beat.
SFX: Mixkit Sound Effects Free License (https://mixkit.co/license/#sfxFree), same terms: free commercial use, no
attribution. They are fetched as WAV by ID, like music.
"""
import argparse, json, os, re, sys, urllib.error, urllib.request

CACHE = os.path.expanduser("~/.cache/reel-music/mixkit")
LICENSE = {"provider": "Mixkit", "name": "Mixkit Stock Music Free License",
           "url": "https://mixkit.co/license/#musicFree", "commercialUse": True,
           "attributionRequired": False}


SFX_CACHE = os.path.expanduser("~/.cache/reel-music/mixkit-sfx")
SFX_LICENSE = {"provider": "Mixkit", "name": "Mixkit Sound Effects Free License",
               "url": "https://mixkit.co/license/#sfxFree", "commercialUse": True, "attributionRequired": False}


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    r = urllib.request.urlopen(req, timeout=30)            # follows the 301s from old tag URLs
    return r.geturl(), r.read().decode()


def _ld_recordings(html):
    out, blocks = [], re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    for b in blocks:
        try:
            stack = [json.loads(b)]
        except ValueError:
            continue
        while stack:
            o = stack.pop()
            if isinstance(o, list):
                stack.extend(o); continue
            if not isinstance(o, dict):
                continue
            if o.get("@type") == "MusicRecording" and str(o.get("url", "")).startswith("https://assets.mixkit.co/music/"):
                out.append(o)
            stack.extend(v for v in o.values() if isinstance(v, (dict, list)))
    return out


def catalog(tag, pages=6):
    """Every track on a Mixkit listing (tag or /free-stock-music/<path>/), following ?page=N."""
    base = f"https://mixkit.co/free-stock-music/{tag.strip('/')}/" if "/" in tag.strip("/") or tag in (
        "chillout", "lo-fi-beats", "downtempo", "ambient", "hip-hop", "jazz", "funk", "soul") else \
        f"https://mixkit.co/free-stock-music/tag/{tag}/"
    seen, out = set(), []
    for pg in range(1, pages + 1):
        try:
            final, html = _get(base + (f"?page={pg}" if pg > 1 else ""))
        except Exception:
            break
        if pg > 1 and "page=" not in final:
            break
        new = 0
        for it in _ld_recordings(html):
            mid = it["url"].rstrip("/").split("/")[-1].replace(".mp3", "")
            if mid in seen:
                continue
            seen.add(mid); new += 1
            out.append({"id": mid, "title": it.get("name", "?"), "genre": it.get("genre", "?"),
                        "artist": it.get("byArtist"), "duration": it.get("duration", "?"), "url": it["url"],
                        "license_url": it.get("license"), "license_name": it.get("copyrightNotice")})
        if not new:
            break
    if not out:
        raise SystemExit(f"no catalog for {tag!r} (try hip-hop, chill, lo-fi, funk, electronic, mood/calm, chillout)")
    return out


def sfx_catalog(category, pages=3):
    out, seen = [], set()
    for pg in range(1, pages + 1):
        try:
            final, html = _get(f"https://mixkit.co/free-sound-effects/{category}/" + (f"?page={pg}" if pg > 1 else ""))
        except Exception:
            break
        if pg > 1 and "page=" not in final:
            break
        lic = re.search(r'data-license="(\w+)"', html)
        new = 0
        for card in re.split(r'class="item-grid-card ', html)[1:]:
            m = re.search(r'data-audio-player-item-id-value="(\d+)"', card)
            t = re.search(r'item-grid-card__title">\s*(.*?)\s*<', card, re.S)
            if m and m.group(1) not in seen:
                seen.add(m.group(1)); new += 1
                out.append({"id": m.group(1), "title": t.group(1) if t else "?", "category": category,
                            "page_license": lic.group(1) if lic else None})
        if not new:
            break
    return out


def fetch_sfx(mid, d=SFX_CACHE, meta=None):
    """Local path of Mixkit SFX `mid` (WAV), downloading it with a licence sidecar if needed."""
    mid = str(int(mid))
    dst = os.path.join(d, f"{mid}.wav")
    if os.path.exists(dst):
        return dst
    os.makedirs(d, exist_ok=True)
    url = f"https://assets.mixkit.co/active_storage/sfx/{mid}/{mid}.wav"
    try:                                       # some items have no free WAV (403): skip them rather than use a preview
        data = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Mixkit SFX {mid}: no free WAV download (HTTP {e.code}); pick another")
    with open(dst + ".part", "wb") as f:
        f.write(data)
    os.replace(dst + ".part", dst)
    with open(os.path.join(d, f"{mid}.json"), "w") as f:
        json.dump({"id": mid, "url": url, **(meta or {}), "license": SFX_LICENSE}, f, indent=1)
    return dst


def fetch(tag, mid, d=CACHE):
    """Return the local path of Mixkit track `mid`, downloading it (from tag `tag`'s catalog) if needed."""
    mid = str(mid)
    dst = os.path.join(d, f"{mid}.mp3")
    if os.path.exists(dst):
        return dst
    os.makedirs(d, exist_ok=True)
    hit = [x for x in catalog(tag) if x["id"] == mid]
    if not hit:
        raise SystemExit(f"Mixkit id {mid} is not in tag {tag!r}; run `music_fetch.py list {tag}`")
    it = hit[0]
    if not it["url"].startswith("https://assets.mixkit.co/"):
        raise SystemExit(f"unexpected host for {it['url']}; refusing")
    if it.get("license_name") and it["license_name"] != LICENSE["name"]:
        raise SystemExit(f"Mixkit id {mid} is under {it['license_name']!r}, not {LICENSE['name']!r}; refusing")
    urllib.request.urlretrieve(it["url"], dst)
    with open(os.path.join(d, f"{mid}.json"), "w") as f:
        json.dump({**it, "tag": tag, "license": LICENSE}, f, indent=1)
    return dst


def analyze(path):
    import numpy as np, librosa
    y, sr = librosa.load(path, sr=22050, mono=True)
    dur = len(y) / sr
    oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=256)
    # tempo candidates from the onset autocorrelation (beat trackers often lock to 2/3 or 3/2)
    ac = librosa.autocorrelate(oenv, max_size=int(2.5 * sr / 256))
    lags = np.arange(len(ac)) * 256 / sr
    cands = []
    for lo_bpm, hi_bpm in ((60, 90), (90, 130), (130, 180)):
        sel = (lags > 60 / hi_bpm) & (lags < 60 / lo_bpm)
        i = np.argmax(np.where(sel, ac, -1))
        cands.append((round(60 / lags[i], 2), round(float(ac[i] / ac[0]), 3)))
    print(f"{os.path.basename(path)}  {dur:.1f}s")
    print("  tempo candidates (bpm, autocorr):", cands)
    # drops: biggest positive jumps of 1 s RMS
    hop = sr // 4
    rms = np.array([np.sqrt(np.mean(y[i:i + hop] ** 2) + 1e-12) for i in range(0, len(y) - hop, hop)])
    db = 20 * np.log10(rms)
    jump = db[4:] - db[:-4]
    idx = np.argsort(jump)[::-1]
    drops = []
    for i in idx:
        t = (i + 4) * 0.25
        if all(abs(t - d) > 4 for d in drops):
            drops.append(t)
        if len(drops) == 5:
            break
    on = librosa.onset.onset_detect(y=y, sr=sr, units="time", hop_length=128, backtrack=True)
    snapped = [round(float(on[np.argmin(abs(on - d + 0.25))]), 3) for d in sorted(drops)]
    print("  drops (energy jumps, snapped to the nearest onset; good section cues):", snapped)
    print("  energy per 4 s:", " ".join(f"{int(i*4)}:{np.mean(db[i*16:(i+1)*16]):.0f}" for i in range(int(dur // 4))))
    print("  key:", key_of(y, sr))


_MAJ = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_MIN = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def key_of(y, sr):
    """Krumhansl-Schmuckler key estimate -> (tonic pitch class, 'major'|'minor', name)."""
    import numpy as np, librosa
    ch = librosa.feature.chroma_cqt(y=y, sr=sr).mean(1)
    best = None
    for mode, prof in (("major", _MAJ), ("minor", _MIN)):
        for k in range(12):
            r = np.corrcoef(ch, np.roll(prof, k))[0, 1]
            if best is None or r > best[0]:
                best = (r, k, mode)
    return best[1], best[2], f"{NOTES[best[1]]} {best[2]}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("list"); a.add_argument("tag")
    b = sub.add_parser("get"); b.add_argument("tag"); b.add_argument("id"); b.add_argument("--dir", default=CACHE)
    c = sub.add_parser("analyze"); c.add_argument("file")
    e = sub.add_parser("sfx-list"); e.add_argument("category")
    f = sub.add_parser("sfx-get"); f.add_argument("id"); f.add_argument("--dir", default=SFX_CACHE)
    x = ap.parse_args()
    if x.cmd == "list":
        for it in catalog(x.tag):
            print(f"  {it['id']:>5}  {it['title'][:32]:32s} {str(it['genre'])[:18]:18s} {it['duration']}  {it['license_name']}")
        print("License:", LICENSE["name"], LICENSE["url"])
    elif x.cmd == "get":
        print(fetch(x.tag, x.id, x.dir))
    elif x.cmd == "sfx-list":
        for it in sfx_catalog(x.category):
            print(f"  {it['id']:>5}  {it['title']}")
        print("License:", SFX_LICENSE["name"], SFX_LICENSE["url"])
    elif x.cmd == "sfx-get":
        print(fetch_sfx(x.id, x.dir))
    else:
        analyze(x.file)


if __name__ == "__main__":
    main()
