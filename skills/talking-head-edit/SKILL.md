---
name: talking-head-edit
description: Edit raw talking-head clips into a tight vertical reel - cut out retakes, keep the last clean attempt of every line, butt-cut every join on the word (~0-40ms of room tone, tight hand-cut style), drop piece-initial "So" and post-CTA sign-offs, remove repeated words, frame the speaker's face to measured targets (wide/punch targets for face height and eye line), level every piece so all of the speech is equally audible, normalize to -14 LUFS and export 4K portrait (no captions by default). Also cuts two-camera interview / podcast reels: hook on the host's question naming the guest's company, cliffhanger question by ~10 s answered at the end, speaker check at every join. Use when the user says "cut out retakes", "edit my talking head video", "edit this reel the same way", "remove the retakes", "tighten this talking head", or hands over phone clips of someone talking to camera with multiple attempts per line.
---

# Talking-head edit

Turn one or more raw talking-head clips (phone, desk, selfie) into one cohesive take:
every line once, the best (usually last) clean attempt, no stumbles or repeated words, word endings
never lost, joins butt-cut on the word (~0-40ms of room tone, out-points on the falling tail, in-points
on the rising attack), framing that puts the face on measured targets (alternating wide/punch), every
piece equally audible (speech leveling), broadcast loudness, 2160x3840 export.

All scripts live in `scripts/` next to this file (transcribe, plan, probe_regions,
assemble_audio, reframe, build_video, verify, common, emotion_ride, mouth). Scratch folders get wiped between
sessions, so never rely on ad hoc scripts in a scratchpad: copy nothing, call these directly.

```
SK=~/.claude/skills/talking-head-edit/scripts
PY=$($SK/find_python.sh)   # $REELKIT_PYTHON, else <repo>/.venv/bin/python3, else python3 (needs faster-whisper + opencv)
FFMPEG: $FFMPEG, else ffmpeg on PATH (common.py); the drawtext filter is not needed
YuNet face model: $YUNET_MODEL, else <repo>/assets/models/yunet.onnx (<repo> = $REELKIT_HOME or the repo this skill lives in)
```

Work in a fresh scratch dir (`$W`). Never overwrite the user's previous export; write `_v2`, `_v3`.

## Pipeline

### 1. Probe every source
`ffprobe -show_entries stream=width,height:stream_side_data=rotation`. iPhone portrait clips
are often stored 3840x2160 with `rotation=-90`; ffmpeg autorotates on decode, so the cut
sources are portrait. `build_video.py` refuses to run if a source's rotated orientation does
not match the output. A clip with no rotation tag that looks sideways needs a `transpose`.

### 2. Transcribe the full sources (medium.en or larger, word timestamps)
```
$PY $SK/transcribe.py SRC1.MOV SRC2.MOV --model large-v3 --out-dir $W --words > $W/transcript.txt
```
Chunked by default (30s windows, 6s overlap). A single Whisper pass over a long raw take
silently drops whole minutes of retakes; chunking keeps every attempt visible. large-v3 on
CPU takes roughly 1.5-2x realtime; medium.en is the minimum.

### 3. Retake audit (MANDATORY, this is where a first version failed)
Read the whole transcript top to bottom and build a per-line table BEFORE choosing cuts:

| line | attempts (start-end, words) | chosen | why |

Rules:
- Group attempts by line (same intent, even if the wording changes). Creators usually
  retake until it is right, so the **last complete clean attempt wins** unless it is
  clearly worse (stumble, cut off, laugh). Wording changes in a later take are deliberate.
- A kept piece must contain **no internal restart, stumble, or repeated word**. Look for
  stretched words ("re... built"), doubled words ("I don't know about you, I don't
  know about you"), and long intra-line pauses where a restart happened. If the chosen
  attempt has one, find a clean attempt or cut the stumble out on the envelope.
- **No two kept pieces may cover overlapping content.** If piece N ends with phrase X and
  piece N+1 starts with phrase X, or they are two halves of different attempts of the same
  sentence, that is a stitched retake. Only stitch two attempts mid-sentence when the join is
  seamless in both sound (tone, pace) and picture.
- Picture check: adjacent pieces at the same zoom with a jump in posture or hand position
  reveal a retake. Alternating zoom hides small jumps, it does not excuse a content repeat.
- Also check lines that were never used: make sure each line of the final script appears
  exactly once.
- **Repeated-word scan (mandatory).** List every repeat in the chosen pieces: doubled words
  ("usually, usually", "like, like"), restarted phrases ("I've actually found one tool. But one of
  the tools..."), and the same CTA said twice in the video ("comment EDIT" mid-video and at the
  end). Cut each to a single clean instance at an envelope dip (split a piece into a/b around a
  doubled filler word if needed). For a duplicated CTA keep one, the strongest wording, at the end;
  drop the other and any line that refers back to it ("And again, comment ..."). verify.py runs the
  same scan on the output (step 8), but find them here first.

**Spoken editor instructions.** Creators often talk to the editor inside the raw take ("you can
cut all of that at the end", "use the second one", "skip this"). Before choosing cuts, list every
aside addressed to the editor with its timestamp, follow it, and make sure no instruction speech
lands in a kept piece. Ambiguous scope (e.g. does "all of that at the end" include the closing
CTA?): pick the reading the stated reason supports, and flag it in the report. Instructions you
cannot fulfil (b-roll you do not have, captions, music) are reported, never faked.

Tools for the audit:
- `plan.py PLAN --bursts SRC` lists every voiced burst in the source plus a ready-made range list;
  feed it to `probe_regions.py` so every attempt (including ones the long-form transcript merged
  or dropped) is transcribed in isolation. Long gaps that probe as "You" / "Thank you" are Whisper
  hallucinations on room tone; confirm with the envelope.
- `plan.py PLAN --peek SRC LO HI [LO HI ...] --step 0.02` prints the envelope; every voiced
  burst between attempts must be accounted for.
- `probe_regions.py SRC LO-HI ...` transcribes windows in isolation. Run it on each
  envelope burst inside a candidate piece. Whisper collapses an immediate repeat into one
  long word when both are in the same window: in one edit, "so then we re-" (aborted)
  + "so then we rebuilt it" (clean) was transcribed as a single
  "re- ... -built" lasting 2.2s. Any word longer than ~0.6s is a red flag; split the
  region at the envelope dips and transcribe each part alone.

Present the table to the user (or in the report) with the chosen attempt per line.
Judgment call: if the last attempt runs straight into the next phrase with no envelope dip
(nothing below threshold for >= 60ms), it cannot be cut cleanly on a frame boundary; an
earlier attempt with a natural ending wins. Note this in the table.

### 4. Write the plan
`$W/plan.json` (see `plan.example.json`): sources by key, then pieces in narrative order.
For each piece give `lo`/`hi` hard bounds placed in the silence before and after the chosen
attempt (never inside a neighbouring attempt), `ws`/`we` = Whisper start of first word / end
of last word, `text` = the expected words, optional `compress:false` (keep natural pauses),
optional `zoom`. Use `--peek SRC LO HI` to print the envelope and place bounds.
Interview or podcast footage (two cameras, host + guest): also read **Interview / podcast (two-camera)** below
before writing the plan (story order, speaker checks, `story` tags).

### 4b. Content trims (rule-based, apply while writing the plan)
Learned from comparing an export with a hand-made re-cut of it. Apply the rules; the judgment calls are
suggestions to raise in the report, not to apply silently.
- **Rule: drop a piece-initial "So".** The hand re-cut removed it on every piece that opened with it.
  Put `lo`/`ws` on the envelope dip between "So" and the next word (`--peek` at 5-10ms steps; the
  dip is usually -45..-53 dB, 10-20ms before the next word). Keep piece-initial "And"/"But".
- **Rule: end on the CTA.** Drop any sign-off after it ("Hopefully this helps.", "See you next time").
- **Rule: cut long in-line hesitations** (>= ~200ms dead air inside a sentence where no word is lost).
- Judgment (the hand re-cut did these; ask or flag): an editorializing aside that adds no information ("And that
  is basically the whole idea."), a vague trailing tag ("...the whole thing"), a filler
  intensifier before a claim ("It's really that simple" -> "It's that simple"). It KEPT "like",
  "actually", "Best part is", so there is no blanket filler-word rule.

### 5. Snap cuts to the envelope (tight edges by default)
```
$PY $SK/plan.py $W/plan.json --out $W/cuts.json
$PY $SK/plan.py $W/plan.json --peek B LO HI --step 0.01   # time:broadband/2-8kHz dB + local floor
```
**Default `tail_mode: "tight"` (v9).** Measured on a hand-made re-cut that butt-cut every join on the
word: out-points a median 58ms BEFORE the -47 dB crossing (on the falling tail, ~15 dB above the
room tone), in-points ~12ms into the rising attack, ~0-5ms of room tone at joins (the earlier export had 87ms).
No word was lost; the cut is deliberately tighter than the decay. Tight mode matches that style:
- Out-point = last envelope frame above `max(local floor, thresh-6) + tight_edge_db` (15 dB) within
  `tight_max_trim` (200ms) before the -47 crossing; in-point = first frame above that level within
  200ms after the onset. `tight_out_offset` / `tight_in_offset` (0) shift them; no safety tail, no
  handle, no join top-up. Target true gap `tight_join_gap` [0, 40ms].
- A `next-word` ending is still capped at the dip before the next word (+5ms), with its fade.
- Frame fitting = nearest whole frame (`fit_frames_tight`), shifted back if it would cross hi.
- plan.py prints `tight: out Xms vs -47 crossing, in Yms vs -47 onset` per piece; verify.py prints
  signed edges per join with the reference (out -58 / in -37 median). A whole word must still be
  heard in the last-word gate; if one fails, raise `tight_out_offset` for that piece, not globally.
- In-line pauses are unchanged (the reference kept the compressed pauses, longest 125ms): `pause_min` 100ms,
  `pause_keep_tail` 50ms, decay logic inside the line.
- **`tail_mode: "decay"` restores v8 ("never clipped", ~65-110ms joins)**, described below; use it
  for a slower, breathier delivery or when the user asks for air between lines.

`tail_mode: "decay"` (v8). The -47 dB crossing is NOT the end of a word. An early version cut on
it (25ms/30ms handles) and clipped "free" and "thing". Decay mode (`common.py`):
- Two envelopes, 20ms RMS / 5ms hop: broadband (90 Hz high-pass) and the 2-8 kHz band (`hf_band`),
  where f/s/th and breathy vowels ("free") carry their energy. Local noise floor = 10th percentile
  within +/-2.5s (`floor_win`), capped at the source's global floor + 3 dB.
- **Out-point**: from the last frame above -47 near Whisper's word end, follow the word until it has
  truly decayed: broadband within `decay_margin_db` (6) AND 2-8 kHz within `decay_margin_hf_db` (10)
  of the local floor for `decay_hold` (30ms), at most `decay_max` (450ms). Then keep a **safety
  tail** `post_offset` = 80ms (frame fitting may trim it to `post_min` 65ms, never lower).
- If the next word follows with no silence (a re-rise of `rise_db` 8 dB, or hi placed on the dip and
  the source is loud right after it) the out-point is the dip, marked `next-word`: a 25ms fade-out
  (`bounded_fade`) ending at the cut and no crossfade spill past it, so the ending decays instead of
  clicking and the next word never leaks in. Put `hi` ON that dip (`--peek` at 5-10ms steps).
- **In-point** stays tight: from the first frame above -47, follow a soft attack (fricative, breath)
  back up to `attack_max` 80ms, keep `pre_onset` 15ms. An in-point right after other speech gets a
  12ms fade-in.
- Frame fitting rounds to whole frames without eating into a word (`fit_frames_grow`).
- **Join top-up**: every join gets at least `join_gap[0]` (60ms) of true silence (room tone after the
  previous decay + before the next attack). A `next-word` ending has none of its own, so the neighbour
  supplies it from quiet room tone inside its lo/hi. Typical joins land at 65-110ms of room tone; plan.py
  prints the table and flags `<-- short` / `<-- long`.
- In-line pauses >= `pause_min` are compressed with the same decay logic (`pause_keep_tail` 50ms after the
  word, `pre_onset`+10ms before the next).
- Whisper word edges are 50-300ms off. Never cut on Whisper times; they only seed the search.
- `plan.py` prints per piece: thresh crossing, attack, decay (reason: decayed / next-word / bound),
  and how far the decay ran past the -47 crossing (often 50-170ms). `WARN tail still decaying at the hi
  bound` = move `hi` out (into silence or onto the dip before the next word). Never shorten a tail.
- `tail_mode: "thresh"` restores the v2-v7 behaviour (only for reproducing old exports).
- Pin framing across revisions: `crop`/`zoom`/`track`/`face` in a plan piece are passed through to
  cuts.json (copy them from the previous cuts.json) so a timing fix does not move the framing.

### 5b. Insert lines (separately recorded line + B-roll), optional
A line recorded elsewhere (e.g. a pickup line recorded later) can sit between two pieces with B-roll
picture over it, so the lips are never seen:
```
{"label": "INS_x", "insert": true, "audio": "/abs/line.wav", "video": "/abs/broll.MOV",
 "video_start": 12.3, "video_x": 0.5, "video_zoom": 1.0, "text": "Same voice, different language."}
```
- If `audio` does not exist yet, plan.py prints `PENDING` and skips the piece: the plan builds now and,
  once the file is dropped at that path, one rerun of steps 5-8 includes it. Never fake a missing line with TTS.
- The audio becomes source `INS_<label>` (padded 0.5s, `settings.source_pad`), gets the same edge logic,
  is level-matched to the main source (any cut allowed, since mastered lines often arrive near -14 LUFS;
  boost capped at `insert_match_db` 12 dB). The target is the loudness of the pieces right before and
  after the insert, not the whole source; per-piece `gain_db` trims it. Check it lands within 1 LU of
  its neighbours in the output. It is bedded on the main source's room tone (quietest 1s, looped;
  `insert_room_tone`) so it does not sound like a dropout.
- To bring a join into the 60-100ms range by one frame without clipping, a piece can carry its own
  `post_min` (e.g. 0.060) together with a tighter `hi`; the default stays 0.065.
- Picture: `video` from `video_start` for exactly the insert's duration, scaled to cover 2160x3840
  (landscape B-roll center-cropped at `video_x`). Inserts do not count in the wide/punch alternation.
- Video intermediates are content-addressed by source/start/frames/filter only (`c_<hash>.mp4`), so the
  rebuild with an insert re-renders only the insert.
- **Room-tone guard:** `assemble_audio.py` beds inserts on the quietest 1 s of the main source. If that second is louder
  than `room_tone_max_db` (-35 dBFS), it is speech, not room tone; this happens on an already-tight re-cut. In that case it
  prints `WARN ... speech, not room tone` and skips the bed. Pre-bed the insert audio on room tone taken from the raw
  camera file, and set `"insert_room_tone": false`.
- **Raw camera audio as an insert** (e.g. a BEFORE take from the .MOV at about -37 LUFS) needs more than the default 12 dB
  boost. Raise `insert_match_db` (one case needed 30, gain +24.4 dB) and check it lands within 1 LU of its neighbours.

### 5c. BEFORE/AFTER demo inserts (sponsored audio-product reels), optional
Used when a sponsor's product changes audio (for example an AI voice or audio-repair tool) and the brand wants to
*hear* it. Each demo is one insert piece of 3-7 s with real product output, played over B-roll (or raw footage where
the speaker is not talking) so no lips are seen. The captions stage puts a `promptDemo` card over it
(**reel-captions-overlays**). Only use the speaker's own voice with their consent.
- **Fix-one-word demo (before/after):**
  1. Find a real flub in the raw footage during the retake audit, e.g. "change your voice" where the retake says
     "change your emotion".
  2. BEFORE = the raw take of that line, untouched.
  3. Leave **at least 0.4 s** of raw room tone between BEFORE and AFTER (hard minimum; 0.5-0.7 s reads better). Both
     clips start with the same words, so the gap alone is not enough: the captions stage adds a whoosh in the gap and a
     big BEFORE/AFTER band that switches on the AFTER clip's first frame (step 7). With only a 0.40 s gap and small
     tags, a brand reviewer heard the repeated phrase as an editing glitch, not a demo.
  4. AFTER = the same raw take up to the wrong word, plus only that word regenerated by the product.
  5. Generate the *whole sentence* (so the word gets natural final intonation) with a direction that matches the
     delivery, e.g. `[low-key and relaxed, a little tired, speaking softly to a friend]`. Make several takes and cut at
     the energy dip before the word.
  6. Splice with an LTAS EQ match to the speaker's voice (1/3 octave, +-9 dB), an RMS match to the word it replaces,
     room tone under the generated part, and a 10 ms equal-power crossfade.
  7. Label it honestly on screen ("Before · original take" / "After · 1 word regenerated"). Never stage a fake flub.
     The separator is non-negotiable (reel-captions-overlays "BEFORE -> AFTER needs an unmistakable separator"): the
     >= 0.4 s gap, the switch SFX, the large full-width BEFORE/AFTER band timed to the audio, and ideally a spoken cue.
     If the speaker can say it, keep a line like "here's the original" / "and here's the fix" in the cut, or record one;
     it makes the repeat read as intentional on audio alone.
- **Emotion demo (one take, an arc):** 2+ audible shifts, not a single switch ("a couple of changes from sad to
  excited"). Write one line whose moods fit the words, e.g.
  `[sad, at a normal speaking volume] The concert sold out before I got a ticket, [suddenly very excited] wait, my friend has a spare!
  [sad again, at a normal speaking volume] Oh, it's for the wrong night, [super excited] no, it's for tonight!`
  1. Audition several scripts x 3 takes; transcribe each with word times and measure every segment (active level, median
     f0, words/s). Keep the take where every shift is unmistakable and the clip stays under ~7 s (one good pick: f0
     103 → 231 → 55 → 256 Hz, 6.6 s).
  2. Emotion voice models often render the sad parts 13-20 dB under the excited ones (in one test the calm half ended
     6.7 dB under the speech, hard to hear on a phone). Asking for "a normal speaking volume" flattens the emotion, so
     ride the level instead:
     `~/.cache/reel-audio/venv/bin/python $SK/emotion_ride.py take.wav demo.wav --labels "sad,excited!,sad,excited!"
     --gaps "1.55-2.15,3.2-3.85,4.5-4.95" --room room.wav --bedded demo_bedded.wav` (the reel-captions-overlays audio
     venv, which has soundfile + librosa). One static gain per quiet
     segment (to 4.5 dB under the excited mean), 40 ms ramps in the pauses, trim, fades, 48 kHz, and the copy pre-bedded
     on the speaker's room tone for the insert. It also keeps the un-ridden clip and writes the per-segment gains for
     `prompts.md`.
  3. Use the bedded file as the insert audio; the captions stage labels each segment on the card.
- Demo audio is real product output: nothing faked, sped up or pitched. Keep the exact prompts, settings
  and selected takes in a `prompts.md` next to the audio.
- **Inserting into an already-edited cut** (a hand-made re-cut as the source): set `"reframe": false`, `"match_sources": true`
  and `"compress": false` on every piece. **Pin** each piece to exact frame boundaries at the existing joins:
  `"pin": "whole"` (use ws..we as is) or `"pin": [[s, e], ...]`. Without `pin`, tight snapping re-cuts those edges
  (in one case it took 173 ms off the start). A pinned piece skips the envelope snap and the lo-bound warning.
- Verify as usual (`verify.py --model large-v3 --sheet --stills`, last-word gate). The transcript at a fix-one-word demo
  reads "...change your voice, ...change your emotion." Coarticulation such as "That you" is fixed in the caption
  corrections, not in the audio.

### 6. Trial-clip check (every piece, full-length transcription)
```
$PY $SK/assemble_audio.py $W/cuts.json --out-dir $W/trial --trial
```
Each piece is spliced exactly as it will be in the final and transcribed alone. Every
expected word must be present, first and last words intact, no extra words from a
neighbouring attempt. Fix bounds and rerun 5-6 until clean.

### 6b. Face framing (default on, `settings.reframe`, `frame_mode: "target"`)
```
$PY $SK/reframe.py $W/cuts.json --write          # stores crop/zoom/face per piece, prints the framing report
$PY $SK/reframe.py $W/cuts.json --sheet OUT.mp4 $W/stills.jpg   # after the render
```
Default since v10. The rule is where the face sits, not a zoom number. The default targets were measured per shot
with the same YuNet detector on a hand-made re-cut the creator approved:

| role  | `face_h` (face box / frame height) | `eye_y` (eye line from top) | shots |
|-------|------|------|------|
| wide  | 0.197 (0.193-0.202) | 0.444 | 8 |
| punch | 0.226 (0.214-0.234) | 0.399 | 10 |

- The chin lands at about 53-56% and the face is centered in x. Wide and punch alternate per talking piece, as
  in 18 of the reference's 19 transitions. Inserts do not count in the alternation and the hook counts as wide.
- YuNet runs on 7 frames per piece, and the **median** face (center x, eye line, height) drives one static
  crop per piece, never a per-frame track.
- **Zoom is derived, never hardcoded.** For each (source, role), zoom = target `face_h` / that source's median
  face height. It is then refined closed loop: frame up to 6 pieces, re-detect on the framed crop, rescale,
  2 passes. YuNet boxes are not scale-invariant, so the open-loop zoom came out about 9% too big. The crop puts
  the eyes on the role's `eye_y` and the face at x 0.50, clamped inside the frame (no black edges).
- **Upscale:** `zoom_max` 2.25 is a safety cap. Reaching the targets on a 4K phone raw with a small face (0.106 of
  the frame) needs 1.75x wide and 2.02x punch: 1232x2191 and 1072x1906 real source px scaled to 2160x3840. That
  sharpness was judged acceptable. If the cap is hit, or a crop keeps fewer than `upscale_warn_px` 1700 px of real
  height, the report prints a WARN with the face size actually reached. It never falls short of the target silently.
  Crops are applied in source pixels (`build_video.py`), so a 4K source is never pre-shrunk before the zoom.
- **Handheld pieces** (face x spread > 0.10, e.g. a selfie hook) are exempt from the targets. A selfie hook in the
  reference sat at face 0.36 and eyes 0.42 and was left untouched. Those pieces keep `handheld_zoom` 1.08 (up to 1.30
  to pan) on a smoothed track: 5-pt median then 5-pt mean, keys every 0.25s, turned into a piecewise-linear
  crop expression by build_video.
- The framing report (stdout and `settings.frame_report` in cuts.json) gives, per (source, role): source face,
  zoom, target face and eye line, kept source px and upscale factor.
- build_video.py auto-runs this if cuts.json has no crops. `frame_mode: "zoom"` restores the v2-v9 behavior:
  `reframe_zooms` [1.08, 1.25], `zoom_contrast` 1.15, eyes in `eye_band` 0.35-0.40, `zoom_max` 1.30.
  `reframe: false` gives the legacy fixed 1.0x/1.15x punch.
- Another person or setup: the default targets come from one creator's approved cut and are a good starting point.
  To match a different look, measure an approved cut the same way (YuNet per shot) and put the numbers in
  `settings.frame_targets`.

### 7. Assemble audio, build video
```
$PY $SK/assemble_audio.py $W/cuts.json --out-dir $W
$PY $SK/build_video.py $W/cuts.json --audio $W/audio_norm.wav --out OUT.mp4 --work $W/v
```
Sources are level-matched first (`settings.match_sources`, default true): each source's kept speech
is gained to the integrated loudness of the source with the most kept material (cap +/-6 dB), so a
hook shot on another day does not jump at the join (an early version shipped a 10 LU drop from hook to body).

**Speech leveling** (v10, `settings.level_pieces`, default true). The goal is that all of the speech can be
heard well, not a specific boost. Before the final loudnorm,
`assemble_audio.py` measures each talking piece's active speech loudness: K-weighted 100 ms blocks, gated
within 20 dB of the piece's loud blocks and at least 10 dB over its room floor. It then gives the piece ONE
constant gain so it sits at the median of the pieces.
- Pieces already within `level_tol` (1.0 LU) are left alone. Boosts are capped at `level_max_boost_db` (10)
  and cuts at `level_max_cut_db` (10).
- A boost is also capped by the room tone: the piece's boosted floor may rise at most `level_floor_rise_db`
  (6 dB) over the median piece floor, or `level_floor_rise_masked_db` (10 dB) when the piece has no exposed
  pause of 0.15 s or more. Background hiss must not become audible.
- One static gain per piece means no compression, no pumping and no new fades. Joins stay butt cuts and word
  tails keep their shape. Inserts are matched to their leveled neighbours afterwards.
- Opt out per piece with `"level": false`.
- It prints a per-piece table (offset, floor, longest pause, gain, result) plus the spread before and after,
  and writes `level_report.json`.
- Why not a block boost: a hand re-cut that boosted the second half as a block (+2..+8 dB) overshot. Its
  second half came out 1-3 LU louder than the first, and two pieces stayed over 2 LU under the median. What
  was wanted was evenness. On one re-run the piece spread went from 13.9 LU (raw) to 1.6 LU (the hand re-cut:
  6.2 LU).

10ms crossfade at every join; two-pass loudnorm to -14 LUFS, true peak <= -1 dBTP
(targets -1.5 before AAC for margin). Framing comes from step 6b (crop per piece, lanczos). libx264 crf 16, AAC 192k, +faststart, 2160x3840, no captions.

### 8. Verify
```
$PY $SK/verify.py OUT.mp4 $W/cuts.json --model large-v3 --sheet $W/joins.jpg --stills $W/stills.jpg
```
**Last-word gate (mandatory):** verify.py lists the last expected word of every piece and whether it
is heard in the FULL-output pass at the end of that piece. The full pass is what a viewer hears in
context; an isolated clip lets Whisper "hear" a clipped word from its first fricative (an early version:
"free" passed in isolation, was missing in the full pass, and was audibly cut off). Any FAIL: render a
spectrogram of the source edge (`ffmpeg -lavfi showspectrumpic`), `--peek` it, find the real decay,
move hi, rebuild. It also prints TRUE join gaps (room tone between real decay and real attack).
Also: residual silence at every join (from the output audio), a repeated-word scan of the full
output transcript and of each piece transcribed in isolation (adjacent repeats, words >0.6s,
near-repeat n-grams, content phrases used twice), and a stills sheet with one frame per piece
showing the face box, center line and eye band. Read both sheets. Legitimate repeats (a product
name said twice, "you can also" in two different sentences) are fine; say so in the report.
Checks specs, loudness, full re-transcription vs expected text (missing/extra words), every
join +/-2s transcribed, envelope level at each cut edge (flags cuts on sound), and a contact
sheet of before/after frames for each join. Read the sheet (Read tool) for posture jumps.
Report the joins with timestamps so the human can listen to them.

**Story gate (v11, interview reels):** `== story beats` prints, for every piece tagged `story`, where its beat lands
in the OUTPUT (the first heard word matching `story_key`), and gates the structure: the hook is the first piece,
the question lands by `settings.story_question_by` (10 s), the first answer piece starts after
`settings.story_answer_after` (60%) of the reel, and no `settings.spoilers` word is heard between the question
and the answer (exact or prefix match; fuzzy matching turned "laid" into "ai"). Report the land times.
`--quick` runs only specs, loudness, transcript, diff, last-word gate and story beats: use it on the final file
after captions and music, where joins and framing were already checked on the cut.

**Framing gate (v10):** `== framing vs target` re-detects the face in the OUTPUT for every talking piece,
using the median of 3 frames at 25/50/75% since speakers sway about 0.06 in x within a shot. It FLAGs a piece
whose face height is off its role target by more than `frame_tol` (10%), whose eye line is off by more than
`eye_tol` (0.03), or whose x is off center by more than 0.04. It also prints the framing report (zoom, kept
px, upscale, WARNs). Handheld pieces are listed with `--` and not judged.

**Audibility gate (v10):** `== audibility` measures active speech loudness per piece and per sentence. It
FLAGs any piece or sentence more than `audible_piece_lu` (2 LU) below the median, any word whose peak is less
than `audible_word_db` (8 dB) over the local room floor, and any sentence- or piece-final word whose last
80 ms is less than `audible_tail_db` (6 dB) over it. A tail that drops into the floor is heard as a clipped
ending. Words Whisper stacks on one timestamp are measured over 120 ms and their tail is skipped. Calibration:
an approved hand-made cut has 0 word and 0 tail flags, and its pieces span 6.2 LU, IQR 3.4. The v10 re-run spans
1.7 LU with 0 flags. Every FLAG: listen, then fix it in the plan (piece `level`, bounds) and rebuild.

## Interview / podcast (two-camera)
The brief behind this structure: lead with the guest's company (framed with its logo), plant the cliffhanger
question early, and do not answer it until the end.
- **Sources.** One file per camera on one shared timeline (the same source seconds), so a piece's `src` picks the
  camera of whoever is talking. Both files usually carry ONE shared audio track, so per-camera loudness says nothing
  about who is talking.
- **Speaker check at every piece and join.** Always show the speaker who is talking.
  `$PY -I $SK/mouth.py CAM1.mp4 CAM2.mp4 a-b [a-b ...] --names A,D --strip $W/qc/mouth` prints mouth articulation
  ("art") per camera plus the voice pitch of the span (YIN on the shared audio). Typical: host 80-120 Hz, guest
  170-240 Hz; YIN can halve a high voice, so read p25/p75 too. Short spans (< 0.6 s, e.g. "Did they") read
  "both/unclear": decide with the strip, the pitch, an isolated read (`probe_regions.py`) and the conversation.
- **Story order (the default for interview reels):**
  1. **Hook** on the host's question naming the guest's company or credential ("You were at Acme?") plus the
     guest's confirmation. The captions skill frames it with the company's logo (**Interview reels** there).
  2. **Plant the cliffhanger by ~8-10 s**: the host's sharpest question ("Did they cut the team after
     that?"). Plant it with the question itself: the speaker's words, plus a question card while it is asked.
     Never narrate the device. No "open question", "answer at the end", "answered" or "back to the question"
     labels, and no reminder chip that stays on screen: those read as clutter, and the question does not need
     a label.
  3. **Middle**: the guest's story (e.g. then vs now), with no word that gives the answer away.
  4. **Bridge + answer in the last third**, with an explicit answer, ending on the payoff line.
- **Integrity (hard rules).** Every line was really said, in context. Move a host question earlier only when the
  answer you use is the answer to that question in the source (e.g. a question at 451 s answered at 461 s). No
  splice that creates a new claim. Drop reactions that do not read out of context ("That's crazy"). Pin each host
  line from its own first word (start it at that word's onset, not earlier), or the tail of another line leaks in.
- **Tag the plan**: `story` = hook / question / bridge / answer / payoff and `story_key` = the word the beat lands on,
  per piece; `settings.spoilers` = words that would give the answer away (e.g. for a layoffs question: firing, fired,
  layoffs, laid); optional `settings.story_question_by` / `story_answer_after`. Then `verify.py` (step 8) reports the
  land times under `== story beats`. Example: question card on "cut" at 7.00 s, bridge at 35.63 s, answer from
  36.73 s (66%), payoff at 54.14 s.
- **Cut the guest's repeats** (lines that do not get the point across or just repeat it). One statement per idea.
  Drop: a restatement of the same idea in a second sentence (keep the clearer one), filler elaboration that adds
  nothing ("going through everything, working out what was what"), a framing phrase said again (then/now: "back
  when I started" twice; keep two of four), two clauses with one meaning ("super busy and always overloaded"), and
  the company or guest name re-said after the hook already set it ("at Acme" a fifth time). KEEP hedges and
  qualifiers ("I think", "generally"): cutting them makes the guest's claim about a real company stronger than what
  they said, never do that. If a repeat cannot be cut cleanly (a mid-word join, a speaker change, the answer needs
  it), keep it and flag it in the report. `verify.py` prints `== repeated lines` (WARN only): every 3+ word phrase
  said more than once, plus the count of `--names Acme,Guest` (or `settings.interview_names`). Run it on the plan's
  transcript before the final cut.
- **Stop + vowel words get clipped.** One cut ended "But I was still" at 414.83 s, on what the envelope showed
  as the dip after the word, but it was the closure inside "still": the isolated read of the span heard "but I was",
  and Whisper's word time (414.65 s) was early. Extending to 415.167 s (10 frames) kept "still" (the next span read
  starts with "looking"). Proof method: per 10 ms, compare energy above 3.5 kHz with 80-1000 Hz. A burst or
  fricative reads about -8 dB (high minus low), voicing about -35 dB, so a /t/ burst followed by a vowel is the same
  word continuing. The same check tells whether a short syllable after a name ending in a stop is part of the name
  (a /t/ burst, then the vowel) or the extra word Whisper heard there.
- **Whisper on this kind of material:** unusual company names get misheard (one came out as two unrelated common words);
  "didn't experience" becomes "did an experience"; "building" becomes "built" in long windows; it drops "still",
  "actually" and "like"; and it heard a phantom "And in <company>" at a host/guest join that neither side contains
  when read alone (source envelope: both cuts sit in -46..-49 dB dips). Decide with isolated reads of each side of
  the join plus the source envelope, never one pass, and list those spots for the ear check.
- **Caption words** (for the captions skill's `words_raw.json`): where reads disagree on a small word, use the
  majority of the reads (full pass, isolated piece, source) and say so in the report. E.g. "that," after
  "experience" (heard in 3 reads); "Acme is", not "Acme's".

## Report
Retake table, output path + duration, output transcript, what was removed for repeats,
join list with times and residual gap ms, stills path, loudness/TP, and:
- framing per role: face height % and eye line % measured in the output vs target, zoom, kept source px and
  upscale factor, and any WARN (cap hit, < 1700 px real height) or framing FLAG;
- audio: per-piece speech loudness spread before and after leveling (range and IQR in LU), the largest boost
  and cut, any piece whose boost hit the room-tone cap, and every audibility FLAG with its time.

## Learned pitfalls
- v9: a hand re-cut of a 70.1s export came out at 60.6s. Every lag was a whole number of
  frames, every cut a butt cut (no crossfade, no J/L cuts), room tone at joins 2-5ms vs our 87ms
  median, plus the content trims in step 4b. Our "never clipped" decay tails read as slack;
  hence tight mode. The re-cut also punched in on everything after the hook and boosted the second half by
  +2..+8 dB. Both are codified as of v10, in the form of what was wanted rather than the raw numbers:
  - Framing: the goal is where the face sits. This became the per-role face-height and eye-line targets in
    6b, with zoom derived per source. The re-cut's 1.53x zoom is not portable: the same face needs a
    different zoom on a source shot from another distance.
  - Audio: the goal is that everything can be heard well. This became speech leveling in step 7 plus the
    audibility gate in step 8. The block boost overshot (second half +1..+3 LU over the first), so it is not copied.
- Hook-to-body joins between different sources need a loudness check: measure ebur128 on the
  hook and on the first body piece of the output; they should be within ~1.5 LU (match_sources).
- Immediate word repeats inside one burst ("Usually, usually I'd have to") hide inside a
  "clean" trial transcript; the tell is one word lasting >0.6s ("I'd" spanning 0.8s). Probe each
  half at the envelope dip and start the piece at the dip (set lo/ws on it; plan.py warns that the
  onset hugs lo, which is expected there).
- A first version shipped stitched retakes because the cut list was chosen from a gappy transcript and
  never audited line by line. Step 3 is not optional.
- Whisper merges or hallucinates on repeated attempts ("I don't know about you, I don't
  know about you"); trust the per-piece trial transcription and the envelope, not one pass.
- Stretched or hesitant words ("re... built") read fine in text but sound like a stumble.
  Listen-check via the trial transcription timing: a word spanning >0.6s is suspect.
- Rotation metadata: check it, do not assume.
- Keep scripts in this skill; scratch folders are wiped.
- Frame-quantized cuts are clamped inside lo/hi (plan.py ceil/floors at the bounds), so put
  lo/hi in the silence, not on the word.
- Quiet sentence-final words (e.g. "free" with a breathy -45..-50dB tail) look like a stray
  word on the envelope; probe before deciding a tail is the next attempt starting.
- An early version (the v8 fix): "for free" ran straight into the next take's "There's" with no
  silence. The plan put CTA hi / L13a lo at 180.09, which was the f- of "free": the CTA ended on "for"
  and the next piece started with "-ree". The spectrogram showed f- 180.00-180.10, "ee" to 180.30, dip
  at 180.32, then "There's". Lesson: when two attempts touch, find the dip on a 5ms `--peek` plus a
  spectrogram, put hi and the next lo on it, and let the out-point be a `next-word` ending with a fade.
  Likewise "thing" (L9) ends in an -ng murmur at -41 dB until "but" at 98.405; hi on 98.40, not 98.365.
- Short isolated trial clips make Whisper invent leading words ("Now I don't know",
  "Also, if"); confirm with the full-output transcription and the join probes, and check the
  envelope at the cut edge (verify.py flags edges above threshold+3dB).
- Raw phone audio can sit near -31 LUFS with LRA 15; linear loudnorm cannot reach -14 with
  TP <= -1, so assemble_audio.py falls back to gain + oversampled limiter, iterated until
  within 0.2 LU. Always check the final mp4 numbers in verify.py, not the wav.
- build_video.py intermediates are content-addressed; rerunning after a plan tweak only
  re-renders changed cuts (a full 4K render is ~10-15 min on a loaded Mac).

- Isolated Whisper often invents a leading "So"/"But" on short clips (seen on a clip starting on
  the consonant burst itself); the full-output pass and the source envelope decide, not the
  isolated clip.
- Conversely Whisper in context can drop a faint final word that is audible alone (the breathy
  "free" in "try it for free" at -40..-49 dB vanished in the full pass, present in
  isolation). Boosting it does not help; check it in isolation and flag it as a join to listen to.
- A voiced plosive closure (e.g. the 'b' of the next "but") sits at -36..-41 dB right after the
  last word; end the piece at the dip before it, and expect Whisper to sometimes "hear" the
  dropped next word from context.
- When the same line exists in several takes, prefer the one whose onset is clean on the
  envelope (no 100ms voiced burst before the first word).
- v11 (interview reels): two-camera interviews need a speaker check (`mouth.py`: mouth art + pitch),
  story tags with the story gate, and the stop-plus-vowel clipping check; see **Interview / podcast (two-camera)**.
  Never narrate the cliffhanger device on screen.

## Example plan
`plan.example.json` is a template: placeholder source paths, a hook from a second clip, a line split into a/b, a
`compress: false` piece and a CTA, with the default settings. Replace the paths, times and text with your own
(step 4), then run steps 5-8.
