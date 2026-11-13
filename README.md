# Reel editing skills for Claude Code

Two Claude Code skills for vertical (9:16) talking-head videos.
You hand Claude your raw clips, it runs the scripts in these folders, checks its own work and gives you a finished
reel.

## What they do

**`talking-head-edit`** turns raw phone clips of someone talking to camera into one tight take.

- Transcribes every clip with word timestamps.
- Finds the retakes and keeps the last clean attempt of each line.
- Cuts out stumbles, repeated words, a leading "So" and anything said after the call to action.
- Joins the pieces right on the words, so there are no dead pauses and no clipped word endings.
- Keeps the speaker's face in the same place on screen (alternating wide and closer framing).
- Levels every piece so the whole reel sounds equally loud, then normalizes to -14 LUFS.
- Exports a 2160x3840 (4K portrait) video.
- Re-transcribes and measures the result before calling it done.
- Also cuts two-camera interview or podcast clips: a hook on the host's question, a cliffhanger question early on that
  gets answered at the end, and a speaker check at every join.

**`reel-captions-overlays`** takes a finished vertical cut (for example the output of `talking-head-edit`) and adds the
graphics.

- Bold one-word captions synced to the speech.
- Title cards, step lists, old-vs-new splits, tool cards, full-screen "takeovers" and a "Comment WORD" call to action.
- Optional product demo cards for sponsored reels.
- An interview look with a logo hook and a question card.
- One small `overlays.json` drives everything. The same edit renders in HyperFrames (default) and in Remotion, frame for
  frame.
- By default it then adds a music bed and a few sound effects in a measured house style (the `steady-funk-bed` preset).
  You supply the music; see [Music and sound effects](#music-and-sound-effects-are-not-included).

Each `SKILL.md` is the full instruction set Claude follows: the steps, the thresholds and the checks. Read them if you
want to know exactly what happens.

## What is in this folder

```
README.md
requirements.txt                 Python packages for the main venv
.gitignore
assets/models/yunet.onnx         YuNet face detector (OpenCV Zoo, MIT licence)
skills/
  talking-head-edit/
    SKILL.md                     instructions Claude follows
    plan.example.json            example cut plan
    scripts/                     transcribe, plan, assemble_audio, reframe, build_video, verify, ...
  reel-captions-overlays/
    SKILL.md
    overlays.example.json        example overlays (a placeholder sponsored reel)
    music_plan.example.json      example music plan (points at YOUR licensed track)
    music_presets/steady-funk-bed.json
    examples/interview-briefing/ custom interview look (gen.mjs + template overlays.json)
    scripts/                     new_reel, transcribe, faces, build_edit, verify, music_*, ...
    templates/                   HyperFrames and Remotion projects, style.css, fonts
```

## Requirements

Tested on macOS (Apple Silicon). The shell scripts use zsh and macOS `cp -c`; Linux should mostly work but is untested.

- **Claude Code**.
- **ffmpeg and ffprobe** on your PATH, built with libx264 (tested with ffmpeg 8.1). On a Mac: `brew install ffmpeg`.
  Set `FFMPEG` if you want a specific binary.
- **Node.js** 20.19+ or 22.13+ (tested with 22), with `npm` and `npx`.
  - **HyperFrames** (default renderer, Apache-2.0) runs through `npx hyperframes@0.8.214`. Nothing to install up front.
  - **Remotion** 4.0.539 (parity renderer) is installed per reel with `npm ci` from the template's lock file.
    Remotion is free for individuals and companies of up to 3 people. Larger companies need a paid Remotion Company
    License for commercial use (see remotion.pro/license).
  - **Google Chrome** for rendering. Run renders outside any sandbox.
- **Python 3** (tested with 3.13) in a virtual environment with the packages in `requirements.txt`:
  faster-whisper, opencv (headless), numpy, scipy, fonttools, brotli.
  The first transcription downloads the Whisper model (`large-v3` is about 3 GB) into `~/.cache/huggingface`.
- **YuNet face model**: included at `assets/models/yunet.onnx`. If you ever need it again, it is
  `face_detection_yunet_2023mar.onnx` from the OpenCV Zoo (`opencv/opencv_zoo`, folder `models/face_detection_yunet`).
- **Music stage only**: a second venv made by `scripts/setup_audio.sh` (numpy, scipy, librosa, matplotlib).
- **Optional, matching a new creator's audio style**: `scripts/setup_audio.sh --analysis` builds a larger venv
  (Python 3.11, torch, demucs, panns_inference, about 1.5 GB) and downloads the PANNs checkpoints (about 650 MB) into
  `~/panns_data`.

## Setup

Run these from this folder (the repo root).

1. Make the main Python venv. The scripts look for it at `.venv` in this folder.

   ```sh
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

2. Install the skills into Claude Code by symlinking them (recommended, so `git pull` updates them):

   ```sh
   mkdir -p ~/.claude/skills
   ln -s "$PWD/skills/talking-head-edit" "$PWD/skills/reel-captions-overlays" ~/.claude/skills/
   ```

   The skills expect to live at `~/.claude/skills/<name>`. With symlinks, the scripts follow the link back to this
   folder and find `.venv` and `assets/models/yunet.onnx` on their own.

3. If you copy the folders instead of symlinking, the scripts cannot see this folder any more. Tell them where it is
   (add to `~/.zshrc`):

   ```sh
   export REELKIT_HOME="/path/to/this/folder"   # finds .venv and assets/models/yunet.onnx
   ```

   Or point at each piece directly:

   ```sh
   export REELKIT_PYTHON="/path/to/a/python3"    # any Python with requirements.txt installed
   export YUNET_MODEL="/path/to/yunet.onnx"
   ```

4. How the Python is picked (`scripts/find_python.sh` in each skill): `$REELKIT_PYTHON`, then
   `$REELKIT_HOME/.venv/bin/python3`, then `.venv/bin/python3` in this folder, then `python3` on your PATH.

5. Music stage (only if you want music): `skills/reel-captions-overlays/scripts/setup_audio.sh`. Claude also runs it the
   first time it is needed. It makes `~/.cache/reel-audio/venv`.

6. Check it works:

   ```sh
   PY=$(~/.claude/skills/talking-head-edit/scripts/find_python.sh)
   "$PY" -c "import faster_whisper, cv2; print('ok', cv2.__version__)"
   ffmpeg -version | head -1; node --version
   ```

Restart Claude Code after installing so it picks up the skills.

## Using them with Claude Code

Start Claude Code in a folder where it can write its working files, then just ask. Some prompts that trigger the
skills:

- "Edit my talking head video: `~/Movies/take1.MOV` and `~/Movies/take2.MOV`. Cut out the retakes."
- "Tighten this talking head clip and export it 4K vertical: `~/Desktop/raw.mov`"
- "Cut this two-camera interview into a reel. Host camera `host.mp4`, guest camera `guest.mp4`."
- "Add captions and overlays to `~/Desktop/reel_v1.mp4` in the reference reel style."
- "Caption this reel, add a Comment GUIDE call to action at the end, and add background music using
  `~/Music/licensed/track.mp3`."
- "Add captions to this interview reel using the interview briefing look. The guest's company logo is `~/logo.svg`."

Typical flow: `talking-head-edit` produces a clean cut, then `reel-captions-overlays` captions it, renders it and (if you
gave it a track) scores it. Claude writes new versions as `_v2`, `_v3` and never overwrites your earlier export. Each
skill ends with a short report of what it changed and what it checked.

Good to know:

- Renders are 4K and slow. Plug in the laptop and close heavy apps.
- For the interview look, `--logo` takes a simple SVG made only of `<path>` elements (no scripts, images or links).
  Without a logo, a neutral placeholder ring plus the company name is shown.

## Music and sound effects are not included

No music or sound-effect audio ships with these skills. You must use audio you have the right to use, for example a
track you bought a licence for, or a free library whose licence covers your use.

Where the skill expects it (`music_plan.json`, which Claude creates in the reel's working folder from
`skills/reel-captions-overlays/music_plan.example.json`):

- `tracks.A.file`: the path to your licensed track (absolute, or relative to the plan), plus its `bpm`.
- `license`: who licensed it to you and under which terms, so the record travels with the reel.
- Or use a Mixkit track instead of a file (`mixkit_id` + `tag`). Mixkit's free licence allows commercial use without
  attribution; check the current terms at mixkit.co/license. Mixkit downloads are cached in `~/.cache/reel-music/`.
- Sound effects: the `steady-funk-bed` preset names three Mixkit sound effects by ID. They are fetched into
  `~/.cache/reel-music/mixkit-sfx` the first time they are used. To use your own licensed files instead, set
  `sfx.samples` in the plan (a `file` per sound). Any sound with no sample is synthesized by `scripts/sfx.py`, so
  removing `mix.sfx.samples` from the preset gives you synthesized sounds only.

Never commit audio to this repo (the `.gitignore` blocks common audio and video formats).

## Licences of included third-party files

- Fonts in `skills/reel-captions-overlays/templates/fonts/` (Anton, DM Serif Display, Instrument Serif, Inter,
  Space Mono) are under the SIL Open Font License 1.1. The licence text for DM Serif Display is included; the others
  carry the licence notice in their font metadata.
- `assets/models/yunet.onnx` is YuNet from the OpenCV Zoo (MIT licence).
- GSAP is loaded from the jsDelivr CDN at render time, not bundled.

This repo does not have a licence of its own yet.
