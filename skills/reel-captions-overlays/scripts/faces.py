# Detect face boxes every 0.5s in a video; prints per-sample boxes as % of frame and writes JSON.
import json, sys
if len(sys.argv) < 3 or sys.argv[1] in ("-h", "--help"):
    print("usage: faces.py <video> <out.json>   face boxes every 0.5 s (OpenCV Haar cascade), as fractions of the frame"); sys.exit(0 if sys.argv[1:2] in (["-h"], ["--help"]) else 2)
import cv2
cap = cv2.VideoCapture(sys.argv[1]); fps = cap.get(5); n = int(cap.get(7))
W, H = cap.get(3), cap.get(4)
casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
out = []
for f in range(0, n, 15):
    cap.set(1, f); ok, img = cap.read()
    if not ok: break
    s = 0.25; g = cv2.cvtColor(cv2.resize(img, None, fx=s, fy=s), cv2.COLOR_BGR2GRAY)
    faces = casc.detectMultiScale(g, 1.1, 6, minSize=(80, 80))
    if len(faces):
        x, y, w, h = max(faces, key=lambda b: b[2]*b[3])
        b = [x/s/W, y/s/H, (x+w)/s/W, (y+h)/s/H]
    else: b = None
    out.append({"t": round(f/fps, 2), "box": b})
json.dump(out, open(sys.argv[2], "w"))
bs = [o["box"] for o in out if o["box"]]
print("samples", len(out), "with face", len(bs))
print("x0 min %.3f x1 max %.3f y0 min %.3f y1 max %.3f" % (min(b[0] for b in bs), max(b[2] for b in bs), min(b[1] for b in bs), max(b[3] for b in bs)))
print("none at", [o["t"] for o in out if not o["box"]])
