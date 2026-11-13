"""Regenerate templates/fonts/caption_metrics.json (per-character advance widths, in em) for the caption fonts.
build_edit.py uses it to shrink a caption page's font size (--fs) so long pages fit the 820px caption width.
Only needed when the caption font changes. Needs fonttools + brotli (pip install fonttools brotli, in a scratch venv).
Usage: python3 caption_metrics.py <serif caption font.woff2> <sans caption font.woff2> <out.json>
"""
import json, sys
if len(sys.argv) < 4 or sys.argv[1] in ("-h", "--help"):
    print(__doc__.strip()); sys.exit(0 if sys.argv[1:2] in (["-h"], ["--help"]) else 2)
from fontTools.ttLib import TTFont
out = {}
for name, path in [("serif", sys.argv[1]), ("sans", sys.argv[2])]:
    f = TTFont(path); upm = f["head"].unitsPerEm; cmap = f.getBestCmap(); hm = f["hmtx"].metrics
    adv = {chr(c): round(hm[cmap[c]][0] / upm, 4) for c in range(0x20, 0x100) if c in cmap}
    out[name] = {"default": round(sum(adv.values()) / len(adv), 4), "adv": adv}
json.dump(out, open(sys.argv[3], "w"), ensure_ascii=False, separators=(",", ":"))
for k in out: print(k, out[k]["default"], out[k]["adv"].get("a"), out[k]["adv"].get("m"))
