---
name: reel-captions-overlays
description: Add a reference-reel caption and overlay style to a finished vertical talking-head cut. The style is heavy outlined one-word serif captions below the hands, sans captions on B-roll, brick-red title punch cards, red-grid "search pill" screen takeovers, floating tilted cards, step lists, old-vs-new splits, tool-chip cards, audible product demo cards (promptDemo: typed prompt, real waveform, BEFORE/AFTER) for sponsored reels and a "Comment / WORD" CTA, interview reels (a logo-framed hook on the host's question, a question card answered at the end, per-reel custom looks), with an editorial graphic beat every 6-10s so the reel never reads as an ad. Renders the same edit in HyperFrames (default) and Remotion from one transcript plus a small overlays JSON, then (by default) scores it with licensed music and SFX in a reference creator's measured audio style (default preset: steady-funk-bed, one steady funk/soul bed under the voice plus a few sub booms on the big graphics). Use when the user says "add captions", "caption this reel", "add overlays / text pops / a CTA card", "make the reel look better", "style it like the reference reel", "add music / background music / sound effects to the reel", "make the music sound like <creator>'s reels", or hands over an already-cut 9:16 reel (for example from talking-head-edit) that needs captions and graphics.
---

# Reel captions + overlays

Takes an already-cut 9:16 reel (usually 2160x3840 at 30fps, for example the output of `talking-head-edit`) and adds
captions and overlays. Everything is driven by one `edit.json` (caption pages and overlays, in frames) that both
renderers read, so the HyperFrames and Remotion versions match frame for frame.

```
SK=~/.claude/skills/reel-captions-overlays
PY=$($SK/scripts/find_python.sh)                          # faster-whisper large-v3 + opencv ($REELKIT_PYTHON, the repo .venv, or python3)
AUDIO_PY=~/.cache/reel-audio/venv/bin/python               # music stage (scripts/setup_audio.sh creates it)
ANA_PY=~/.cache/reel-audio/analysis-venv/bin/python        # reference-style analysis (scripts/setup_audio.sh --analysis)
```

**Default tool: HyperFrames** (Apache-2.0, pinned `hyperframes@0.8.214`). Remotion (`4.0.539`) is kept as a parity
build. Remotion is free for individuals and companies of up to 3 people; a larger company needs a paid Company License
for commercial use (remotion.pro/license). HyperFrames has no such licence condition.

## Workflow

1. **Set up (first pass).** `$SK/scripts/new_reel.sh <reel.mp4> <workdir>`
   This transcribes with word timestamps (large-v3), detects face boxes every 0.5s (`faces_src.json`) and shot cuts
   (`cuts.txt`), copies `overlays.example.json` to `<workdir>/overlays.json`, then stops.
2. **Fill in `overlays.json`.** Read `words_raw.json` first.
   - `corrections`: fix brand names by token sequence, for example `["Ackme"]→"Acme"`, `["re","-motion,"]→"Remotion,"`,
     `["Acme","Voice."]→"Acme Voice."`. Gluing brand pairs into one token keeps them on one caption page.
     Keep foreign-language lines exactly as spoken (for example "Même voix, autre langue.").
   - `sansWindows`: `[start,end]` seconds of B-roll cutaways, takeovers and promptDemo spans. Captions there switch to the heavy sans (same 75% lane).
   - `overlays`: see the style spec below and **Graphics plan**. Times are in seconds, taken from the word timings.
   - `sponsored: true` for a paid or brand reel. It switches on the demo-beat rule (**Sponsored reels** below) and the verify warning.
3. **Build both projects.** `$SK/scripts/new_reel.sh <reel.mp4> <workdir> <workdir>/overlays.json`
4. **Look before rendering.** `cd <workdir>/hyperframes && npx hyperframes@0.8.214 snapshot --at 2.5,20,30 --no-end`
   (or `npx remotion still Reel x.png --frame=N --scale=0.5` in `remotion/`).
5. **Render.** These need Chrome, so run them outside the Bash sandbox (`dangerouslyDisableSandbox`). Inside it, Chrome dies on Mach-port lookups.
   - `<workdir>/hyperframes/render.sh <out>_captions_hyperframes.mp4` renders a 1080x1920 layout supersampled to 2160x3840 at CRF 16 with faststart.
   - `<workdir>/remotion/render.sh <out>_captions_remotion.mp4` does the same with `--scale=2`, CRF 16, AAC 192k and faststart, then runs `scripts/repair_remotion.py` (see Gotchas).
6. **Verify.** `$PY -I $SK/scripts/verify.py <reel.mp4> <out.mp4> <workdir>/edit.json <workdir>/faces_src.json <workdir>/qc <label>`
   (exits 1 and prints FAIL if frames, corrupt frames, legibility, card edges or the CTA offer fail; about 9 minutes on a 60s 4K reel)
   - It checks that the frame count and duration match the source.
   - It scans every frame for corruption (a wrong video frame or black tiles). `corrupt_frames` must be 0.
   - It spot-checks 5 caption pages: the band must be empty 1 frame before a page starts and filled 2 frames after.
   - It measures face-box pixel change under every overlay. This should be about 0, apart from the full-frame takeover and promptDemo cards, which cover the face on purpose (they are left out of the face, corrupt-frame and legibility checks).
   - It lists `demo_beats` (promptDemo overlays, or any overlay with `"demo": true`). When `edit.json` has `sponsored: true` and there are fewer than 2, it prints a WARN and adds it to `warnings` (not a hard fail; fix the reel or say why in the report).
   - **BEFORE/AFTER separator (v7, warning).** For every promptDemo before -> after row pair it reports `before_after`: the
     words both clips share, the gap in the edit and the silence measured in the render, and whether the edit has the
     switch band and the switch SFX (build_edit `ba`). A pair that repeats words without all of it (gap >= 400 ms, band,
     SFX) prints a WARN: a repeated line with no separator reads as an editing glitch (see promptDemo).
   - **CTA offer (v7, hard check).** A `cta` with an `offer` must be fully visible >= 2.5 s, at >= 40px (13+ px on a
     360px phone), and actually drawn (pixel change in its band). `<label>_offer_mobile.jpg` shows it at phone size;
     look at it. A sponsored reel whose CTA has no `offer` gets a WARN.
   - **Legibility at phone size:** it downscales 8 caption pages to 360px wide and measures stem thickness, text height and
     the outline halo (glyph luma minus the ring around it). Floors: stem >= 4.0px, height >= 28px, halo >= 130.
     `legibility_ok` must be true. `<label>_mobile.jpg` shows the crops; look at it.
   - It writes a contact sheet with one still in the middle of every overlay, and a sync crop sheet. Open both before reporting.
7. **Music + SFX (default stage; skip with `--no-music` or `MUSIC=0`).** Fill in `<workdir>/music_plan.json` (copied from
   `music_plan.example.json` on the first pass; it uses the default style preset `steady-funk-bed`; see **Music and
   sound design** below), then
   `$SK/scripts/music_stage.sh <out_hyperframes.mp4> <workdir> <out>_music.mp4`
   This mixes the bed and SFX under the speech, muxes it onto the picture with a **video stream copy** (no re-encode) and the
   audio trimmed to the video's duration, makes the `<out>_music_mobile.mp4` phone copy (`MOBILE_OUT=` to put it elsewhere)
   and runs `music_verify.py`, including the style check against the plan's preset. It must print PASS. It refuses to
   overwrite an existing output (`FORCE=1` to allow). It also fails any sentence whose speech is less than 6 dB over the
   music under it (`voice_over_music` in the report, `--min-voice-over-music`): what matters is whether every line can be
   heard well. The bed is a steady un-ducked groove about 13 dB under the voice, so one quiet sentence can sink into it
   even when the section average passes. Fix a failure upstream, with talking-head-edit speech leveling or per-segment
   gain, not by pulling the whole bed down. A typical passing interview reel measured 11.2 dB min and 14.1 dB median.
   Open `<workdir>/qc/music_sections.png`. The first run creates `~/.cache/reel-audio/venv` (`scripts/setup_audio.sh`).
8. **Phone copy** (for sending, when the music stage was skipped): `ffmpeg -i <out.mp4> -vf scale=720:1280:flags=lanczos -c:v libx264 -preset slow -crf 22 -maxrate 2800k -bufsize 5600k -pix_fmt yuv420p -c:a copy -movflags +faststart <out>_mobile.mp4` (about 14-19 MB for 60s, under the 25 MB limit). Copy the
   mastered audio, never re-encode it: a second AAC encode at 128k lifted one reel's true peak from -1.3 to -0.7 dBTP.

## Style spec (from the reference reel)

All sizes below are in the 1080x1920 design space (rendered at 2x). The shared CSS is `templates/style.css`.

- **Captions** (v2: the v1 captions were thin white lines, hard to read on a phone. Heavier, larger, outlined is the default.)
  - One word per page, occasionally two: a short function word joins its neighbour, and brand pairs are glued.
  - Text is case and punctuation as spoken ("content.", "Then"). Never uppercase it. No per-word highlight: with 1-2 words per page it only adds noise.
  - Font: **DM Serif Display** (bundled, pinned `@fontsource/dm-serif-display@5.2.8`, OFL) at **136px**, white. Never go back to a
    thin serif such as Instrument Serif regular for captions. Instrument Serif stays for card titles on paper or red.
  - **Outline:** a stroked copy of the word (`::before` with `data-t`, `-webkit-text-stroke: 6px`, so 3px outside the glyph) in
    near-black at 86%, painted behind the fill so it never thins the letters. **Shadow:** 0 0 3px, 0 5px 12px and 0 14px 34px, all dark.
  - **Fit:** `build_edit.py` sets `fs` per page from `templates/fonts/caption_metrics.json` so a page never exceeds 820px
    (for example "automatically." at 127px). Regenerate the metrics with `scripts/caption_metrics.py` if the font changes.
  - **Placement:** one lane centered at **75%** of the height (y=1440), below the speaker's hands and the mic and above the
    bottom-20% UI. It drops to chin + 6% (capped at 76%) only on close-ups. On a typical desk setup the hands sit at
    55-72%, so an "under the chin" lane (60%) sits on the hands.
  - Animation: a 4-frame pop (scale 0.86→1, y 14→0, fade) that starts one frame early, so the word is visible on its first frame.
  - Pages start exactly on the word's start frame and hold until the next page, or until word end + 0.2s if there is a pause.
- **B-roll and takeover captions:** Inter 900 at 120px with tracking -0.03em, same outline and the same 75% lane.
- **Title punch card** (`titleCard`, for the hook phrase)
  - A brick-red `#B23A2E` box, 810px wide (75%), with its top at y=190.
  - Top line: Anton ALL CAPS at 118px. Below it, a white Instrument Serif subtitle at 62px.
  - Its bottom sits at about 23% of the frame height. **This is deliberately higher than the reference (top edge 22-28%)**,
    because the face starts at about 26%.
- **CTA** (`cta`)
  - A white italic serif "Comment" (82px) above a red box with the Anton keyword (196px, e.g. "GUIDE"). Same upper band as the title card.
  - The box pops in 3 frames after the kicker.
  - **Offer line** (`offer`, sponsored reels, v7): the brand's offer under the chin on a paper card, e.g.
    "Use my link for a\nFREE 14-day trial." Use the brand's own wording. `\n` sets the line
    break, and ALL-CAPS words ("FREE") get a white-on-red box. "Comment / WORD" stays the main element; the offer is the
    supporting line. `build_edit.py` derives `offerF` (from `offerAt`, default the CTA start + 10 frames), `offerY`
    (chin + 3% from `--faces` over the CTA window, kept at 55-60% of the height, above the caption ink at about 1385px)
    and `offerFs` (Inter fitted to 720px, max 46px; it warns under 40px). The card is 780px wide (x 150-930, clear of
    the right-side UI) and tilted -1.5°.
  - **The offer must be fully on screen >= 2.5 s.** If the reel ends too soon after the speaker says the CTA, hold the
    last frame: `$SK/scripts/hold_end.sh <reel.mp4> <frames> <reel_hold.mp4>` stream-copies the cut, appends N encoded
    copies of its last frame with digital silence, and checks that the speech decodes sample-identical. Pick N so the
    reel ends just before a music beat (beat k = k * 60 / bpm with `cue_at` 0), set the CTA `end` and the music plan
    section `end` to the new length, and run build_edit with the new `--frames`. The bed plays under the hold and cuts
    with the last frame (example: 32 frames, ending at 68.2 s, just before beat 125 of a 110 BPM bed at 68.207 s; offer
    fully up 3.2 s).
- **Screen takeover** (`takeover`, for a tool or product mention, held 1.5-3s)
  - A full-frame deep red `#8E1F17` with a subtle 8x14 lighter-red grid.
  - A white rounded search pill (magnifier left; mic and lens right) whose bold italic query types on over 14 frames.
  - Below it, a white rounded card with the tool name (serif 128px), a one-line description, a red waveform and 2-3 chips.
  - **Never fake a real product UI.** Use the neutral card with text only. The voice continues underneath.
- **Floating card** (`floatCard`, for a stat, a tip or a label; optional `kicker`, `width`)
  - A paper `#FBF7F1` card, 470px wide, with its top at y=200, on the left or right side. Rotated ±3-4° with a deep shadow.
  - Contents are either an optional kicker + big Anton number + serif label + small caps line, or a flag variant:
    `flag: "es"` (kicker + CSS flag + word) or `flag: "us-es"` (kicker "Same voice, different language" + US flag → ES flag + word).
  - Emoji flags are drawn in CSS, so they never depend on an emoji font.
- **Step list** (`steps`): a paper card at the top (y 150) with a red kicker and numbered rows (DM Serif 54px). Each item has
  its own `at` time and pops in when the speaker says it; the current number is red, earlier rows dim. Use when the
  speaker says "three steps", "first... then".
- **Old vs new split** (`split`): two tilted panes side by side at the top, dark "Old way" (struck through) and red "New way".
  The new pane pops at `newAt`. Use `\n` in `old`/`new` to control line breaks. Use when the speaker contrasts how it used to be.
- **Chip card** (`chipCard`): a paper card with a kicker, tool names as outlined text chips (no logos) and a small line. For "tools I use".
- **Prompt demo** (`promptDemo`, an audible product demo beat, 3-7 s; HyperFrames only, Remotion skips it). Added after a sponsor asked to hear the product, not just hear about it.
  - A full-frame paper card with a faint grid over the demo audio. From the top:
    - a red label pill (y 168) that says how the clip was really made, e.g. "Acme Voice · v2 model"; no logos, no "MCP" unless the MCP made it;
    - a DM Serif title naming the *task* (y 252, e.g. "Fix one word", "Sad to excited, one take");
    - a "Prompt" card (y 400, 940px) whose prompt types on between `typeAt` and `typeEnd`, followed by the model script in small type with `[direction]` tags in red (24px once the script passes 150 characters);
    - one waveform row per clip, flowing under the prompt card (`.stack` grid: y 770 for a short prompt, pushed down by a long prompt + script so the script's last line is never hidden; v6 bug). The typed prompt sits over an invisible full copy, so the card has its final height from the first frame and the rows never jump while the prompt wraps (v6 caught a 53px jump at the third line).
  - Each row has:
    - a tag (`kind`: `before` grey, `after` red, `plain` red);
    - 44 bars measured from the reel's own audio (`build_edit.py --video`; one 36 dB range across the whole demo, so BEFORE and AFTER compare honestly);
    - a playhead that runs while the row plays and colours the played bars;
    - an optional `mark` box over a word (`strike: true` crosses it out, e.g. BEFORE "voice");
    - optional `segments`: any number of emotion markers under the wave, each `{start, end, label, tone}` with `tone`
      `cool` (sad / calm, slate #56697A), `mid` (hopeful, ochre #C28A2C) or `hot` (excited, brand red). Each draws a
      bracket that fills left to right with the playhead and pops its label as the segment starts; the label shrinks
      (46 → 30px) to fit a narrow segment. With 2+ segments the played bars take each segment's colour, so the arc is
      visible as well as audible (e.g. sad / excited! / sad / excited!). Time them from the reel transcript's word times.
  - Later rows wait at 45% opacity until they play.
  - `video` (optional) is a screen recording in `hyperframes/assets/`. It plays muted over the same span and replaces the prompt card and rows, keeping the label and title, so nothing needs re-timing.
  - Put the span in `sansWindows`: captions on the demo audio stay on, in sans, in the 75% lane.
  - The music stage ducks the bed a further 8 dB under the rows (**Music**). The preset auto map gives demos no SFX,
    except the BEFORE -> AFTER switch whoosh below.
  - The card should run to the end of the B-roll shot under it (check `cuts.txt`) and start 1-2 frames before the insert,
    so the insert's picture never shows through the 2-frame fade in or the 4-frame fade out.
  - Schema and a worked example: `overlays.example.json` (`demoLang` single row, `demoEmotion` four-segment emotion arc).
    A BEFORE/AFTER row pair (`kind` before / after + `mark`) is described in its `_doc` block.
  - **BEFORE -> AFTER needs an unmistakable separator** (hard rule, v7). On an earlier version a sponsor's reviewer
    reported a "glitch" where the audio seemed to repeat: the BEFORE and AFTER clips both said "you can also change
    your ...", 0.40 s apart, with small tags and no sound, so it sounded like a bad edit. Every before -> after pair gets
    all of:
    - a clear gap of **at least 400 ms** between the two clips in the cut (**talking-head-edit** "BEFORE/AFTER demo inserts");
    - a **transition SFX** in the gap: `music_mix.py` adds a whoosh peaking mid-gap for each pair (on top of the style's
      auto map; opt out with `"switchSfx": false` or skip `<id>:switch`);
    - a **big full-width BEFORE/AFTER band** under the rows (Anton 92px, dark BEFORE, red AFTER) whose AFTER panel wipes
      in over 4 frames and lands exactly on the AFTER clip's first frame; the finished BEFORE row dims as the gap starts
      (build_edit writes the pair as `ba` with `switchF` = the AFTER row's first frame; opt out with `"switchLabel": false`,
      labels via `beforeLabel` / `afterLabel`);
    - ideally a **spoken or visual cue**: the speaker says "before" / "here's the fix", or the card's tags say it.
    `build_edit.py`, `verify.py` (`before_after`) and `music_verify.py` (`ba_gaps`) warn when a pair that repeats words
    is missing any of it.
- **Motion:** overlays pop in over 6 frames (`back.out(1.7)`, scale 0.7→1) and out over 5 frames (`power2.in`). Easing is identical in GSAP and Remotion `Easing`.
- **Do not change** the cuts. Pass the source audio through untouched (HyperFrames re-encodes AAC; Remotion uses AAC 192k).
  The music stage adds music and SFX *under* that audio and never edits the speech (see **Music and sound design**).

## Graphics plan (hard rules, so the reel does not read as an ad)

- Read the whole transcript first and map the story beats: hook, steps, problem, old vs new, numbers, tips, payoff, CTA.
- **A graphic beat every ~6-10s.** No stretch longer than about 6s without an overlay starting, apart from the open.
- **At least 3 editorial graphics that are not about a product:** step lists, old vs new, a stat the speaker says, a tip, a payoff title card.
- **Never let one brand dominate.** A product gets at most one mention-level moment (a chip, a small line, or a takeover whose
  title is the *task*, e.g. "Line fixer", not the brand). The CTA may name it because the speaker says it. If the brand is
  the only big graphic, the reel reads as a sponsored ad.
- Use only claims the speaker actually makes. No invented numbers, no fake or redrawn logos and no product UIs; tools are
  text chips. The one exception is a guest's real company logo as an interview hook's framing device (see **Interview reels**).
- **Sponsored reels: SHOW the product, don't only talk about it** (hard rule: sponsors want to *hear* the features). Set `sponsored: true`.
  - Map the brand's selling points to demo beats. For an AI voice product, for example:
    - mid-sentence emotion direction → one line with an emotion **arc** (see below);
    - fix one word or line → the speaker's real flub, then the regenerated word;
    - languages → the same voice in French.
  - Use at least 2-3 audible demos of 3-7 s each, as `promptDemo` cards over the real product output. Use before/after wherever the feature fixes something.
  - **An emotion demo shows 2+ audible shifts across a clear arc, not a single switch** (a single switch was judged too
    subtle; change the mood a couple of times mid-sentence). One generation, e.g. sad → excited → sad → excited, where
    each mood fits its words ("The concert sold out before I got a ticket, wait, my friend has a spare! Oh, it's for the wrong night, no, it's
    for tonight!"). Audition several scripts x 3 takes and keep the one where every shift is unmistakable (measure
    each segment: level, median f0, words/s; a good pick had f0 103 → 231 → 55 → 256 Hz). Label every segment on the
    waveform, and write the card prompt to describe the arc ("Start sad, then shift to excited halfway through. Change
    it up a couple of times.").
  - **Every segment must be audible on a phone.** Expressive voice models can render sad / deflated parts 13-20 dB under
    excited ones, and a "[normal speaking volume]" direction flattens the emotion instead of fixing the level. Ride each
    quiet segment up with one static gain in the clip (e.g. sad parts placed 4.5 dB under the excited parts, 40 ms ramps
    in the gaps, no compression, no pitch or speed change) and keep the un-ridden file. `music_verify.py` fails any
    labelled demo segment more than 6 LU under the speaker's normal speech (a calm half 6.7 dB under was hard to hear).
  - **Never cut the creator's own tool recommendations to make room for sponsor demos** (hard rule: the creator's own
    tools line and its chips card stay). Trim elsewhere (drop a weaker demo) or let the reel run longer.
  - The demo audio must be real product output in the creator's voice. Never fake, speed up, pitch or re-voice it.
  - Use a real mistake from the raw footage for the BEFORE when one exists, and label it honestly.
  - The demos are inserted in the talking-head edit (**talking-head-edit** "BEFORE/AFTER demo inserts"). The music ducks under them, and they sit at voice level.
  - Demo cards are titled by the *task*, not the brand, and the label pill is the only product name on them, so the reel still reads as editorial.
  - `verify.py` warns when a sponsored reel has fewer than 2 demo beats.
  - **The end card carries the brand's offer line when the brief or the brand gives one** (v7, e.g. "Use my link
    for a FREE 14-day trial."). Put it in the CTA `offer`, in their words, with the key word emphasized,
    legible on a phone and fully on screen >= 2.5 s (hold the last frame if needed, see **CTA**).
    `verify.py` fails a short or tiny offer and warns when a sponsored CTA has none.
  - Every BEFORE/AFTER demo has the separator (gap >= 400 ms, switch SFX, big BEFORE/AFTER band; see **Prompt demo**).
- Worked example (`overlays.example.json`, a sponsored ~67 s template with the placeholder sponsor "Acme Voice"):
  1. hook card 0.9s
  2. 3 steps list 8.3s
  3. old/new split 24.0s
  4. "Line fixer" takeover 28.5s
  5. "30+ languages" 36.0s
  6. **demo "Same voice, French"** 39.5s
  7. "Also in my kit" chips (Remotion, HyperFrames) 41.4s
  8. "Prompt tip" 46.6s
  9. **demo "Sad to excited, one take"** (sad / excited! / sad / excited!) 50.0s
  10. "TRY IT ONCE" card 60.0s
  11. Comment GUIDE 64.3s (plus the sponsor's offer line from 64.64 s, last frame held to 68.2 s)

  A third demo type, "Fix one word" (the speaker's real flub as BEFORE, the regenerated word spliced in as AFTER, rows
  `kind` before / after with a `mark` box, `strike: true` on BEFORE), fits when the product fixes mistakes.

## Interview reels (host + guest)
The cut comes from **talking-head-edit**'s **Interview / podcast (two-camera)** section: hook on the host's question,
the cliffhanger question by ~8-10 s, the guest's story, the answer in the last third, the payoff line.
- **Hook = the host's question naming the guest's company, with the company's mark as the framing device.**
  For example "You were at Acme?" with Acme's logo, a red "?" popping on "Acme" (0.48 s), then the guest's name and
  credential when they confirm (1.14 s). Logo rules: download ONE file, from Wikimedia Commons or the company's own
  site, and nothing else, or skip it and let the generator draw a neutral placeholder ring next to the company name.
  The generator validates the file before use (a `viewBox` and `<path d>` elements only; no script, event handlers,
  `href`, `url()`, `<style>`, `<image>` or `<use>`) and inlines it in the composition. It is editorial use only: no
  implied sponsorship (no "presented by" or "partner" copy, no brand colors taking over the reel, no CTA naming the
  brand). Report the source URL.
- **Plant the cliffhanger with the question itself**: the speaker's words plus a question card while it is asked
  (e.g. a "HIRING?" card from 7.0 to 10.4 s while the host asks whether the company cut the team). **Never** label the
  device. No "open question", "answer at the end", "back to the question" or "answered" labels, and no reminder chip
  that stays on screen through the middle (hard rule: labels that narrate the device read as clutter; the question
  carries itself). When a panel looks empty without a label, tighten its layout (e.g. the word centered, 135 px, in
  the same 226-470 px slot as the other panels) instead of adding copy.
- **The middle shows the guest's story, not the question**: the example has a THEN/NOW ledger, a "one tool" hub and a
  focus-pull "blurred" line, with nothing that hints at the answer.
- **The answer card** comes in on the host's bridge line (e.g. "Did that change anything?"): the same question card
  returns, fact pills land on the guest's words, a red strike crosses the question as the answer word is said, then a
  stamp in the guest's own words. Every pill and stamp is something they said.
- **Payoff**: the last line becomes the end slate (two big words plus name and credential), on the held last frame
  (about 1.6 s).
- **Per-reel branding may depart from the default style** when the story calls for it: the colors and graphics do not
  have to be rigid. The "briefing" look uses dark glass panels in the top band (y 150-470, clear of faces and the
  platform's top UI), Inter 900 heads, Space Mono labels, one signal red (#FF4D2E), and sans captions (Inter 900,
  white, dark stroke) with a red box on keyword pages. Its generator is `examples/interview-briefing/gen.mjs`, with
  placeholder texts and illustrative beat times in `examples/interview-briefing/overlays.json`. After `new_reel.sh`
  builds the projects with that overlays file (its last step, the template `gen.mjs`, stops with an error on these
  custom overlay types, for example a slate `takeover` without `chips`; everything before it is already built, so that
  error is expected), regenerate the HyperFrames scene from `<workdir>/hyperframes`:
  `node $SK/examples/interview-briefing/gen.mjs ../edit.json ../overlays.json [--logo /path/to/logo.svg]`
  then `npx hyperframes@0.8.214 lint`. It writes the same index / captions / overlays files as the template and passes
  lint. Only the look changes: the safe zones, `build_edit.py`, `verify.py` and the music stage stay the same.
- **Report the land times**: talking-head-edit's `verify.py` prints `== story beats` (question, bridge, answer, payoff
  times in the output). Run it with `--quick` on the final file.

## Music and sound design (default)

Voice is king. Everything is driven by `<workdir>/music_plan.json` (`music_plan.example.json` is the template) plus a
**style preset** (`music_presets/<name>.json`, default `steady-funk-bed`) that holds a reference creator's measured house
style. History: an earlier version followed a tone map (3 tracks, tape stop, filter-out, button ending, 7 dB duck, 16
light UI SFX) and it was rejected because it did not fit the vibe. The fix is to **measure the reference reels first and
match the numbers**, not to interpret the tone with taste words.

- **Licensed sources only (hard rule).** No music or SFX audio ships with this skill. Music: Mixkit (Mixkit Stock Music
  Free License: free commercial use incl. ads, no attribution, https://mixkit.co/license/#musicFree), fetched by id, or a
  track from your own licensed library (a local `file` in the plan). SFX: Mixkit Sound Effects Free License
  (https://mixkit.co/license/#sfxFree) by id, or synthesized in `scripts/sfx.py`. Never use a track whose licence you
  cannot name, never rip, sample or "sound-alike" copyrighted music (including the reference reels).
- **Step 1: profile the reference creator** (once per creator; skip when a preset exists).
  `$ANA_PY -I $SK/scripts/ref_audio_profile.py profile <dir>/*.mp4 --out <dir>/profile --name <creator> --primary <reel id>`
  Demucs htdemucs splits voice from bed+SFX; PANNs CNN14 tags genre/mood/instrument and SFX classes (`--tagger none` = DSP
  only); DSP measures BPM, beat strength, key, centroid, stereo width, bed level under the voice (at -14 LUFS), ducking, the
  bed contour (entry, stops, switches, ending) and SFX events (kind, level vs voice, placement). The `--primary` reel (the
  one closest to the sound you want) counts 3x. Output: `profile.md` (per-reel table + house style), `profile.json`,
  `preset.json`. Copy the preset to `music_presets/<creator>.json` and curate its `mix` block (eq, `sfx.auto_map`,
  `sfx.samples`). `--notes notes.md` appends reading notes (where reels disagree); `--from-json` re-derives everything
  from a saved profile without audio.
  **Reference audio is analysis only**: keep downloads in their own dir, run Python with `-I`, never commit or reuse the audio
  or stems (stems go to `_work/` and are deleted), keep only the numeric profile, delete the downloads when done.
  Calibration (separating our own mix with known stems): Demucs reads the bed about 0.5 dB low (`--bed-bias-db 0.5` is
  added back in the preset's `section_level`) and the centroid at about 0.82x; it misses quiet SFX but sees sub booms. A sub
  thump that starts on a p/b word is dropped as a likely mic pop.
- **Step 2: pick the track against the preset.** `music_fetch.py list <tag>` (tags: hip-hop, chill, lo-fi, funk, electronic,
  happy, and category paths like `mood/relaxed`, `mood/positive`), `get <tag> <id>`, `analyze <file>`. Then audition 5-10
  candidates on the exact reel-length segment you would use:
  `$ANA_PY -I $SK/scripts/ref_audio_profile.py tracks ~/.cache/reel-music/mixkit/<id>.mp3@<start>-<end> ... --preset <preset> --out cand.json`
  It scores BPM, centroid, width, short-term spread and the PANNs tag cosine vs the house style and the primary reel
  (`cos house/primary`). Prefer a steady arrangement (no choppy stops). Do not trust a beat tracker's BPM: check kick
  spacing. Never commit music files; `music_mix.py` fetches Mixkit tracks by id or reads your local licensed file.
- **steady-funk-bed house style (the default preset, measured from 7 reference reels with one primary).** One steady
  funk / soul / swing groove (guitar, bass, drum kit; about 100-110 BPM; bass-heavy) under the whole reel: it starts on the
  first frame at full level and hard-cuts with the last frame. Bed about -27 LUFS while the creator talks (13 dB under the
  voice), **no ducking** (bed in pauses within 1 dB of under speech), **no track switches, no stops, no button ending**. SFX
  are sparse: about 3 events per minute, almost all **sub booms** (30-70 Hz, 0.2-0.8 s) at roughly the voice's level
  (median +1.8 dB on the loudest-100-ms scale), on a phrase start; two thirds of the reference reels open with a boom in the
  first 0.5-1.5 s (+2.7 dB); the section turn gets an airy whoosh that blooms into a boom. No dings, no UI pops, no risers.
  The preset's mix block turns this into defaults: section level -27.2, duck 0.1 dB, bus EQ hp 35 Hz / -5 dB at 3 kHz / lp
  9.5 kHz (lands a Mixkit funk track near the reference centroid), boom on every `titleCard` (Mixkit SFX 2299 "Short bass
  hit", +2.7 dB in the first 1.5 s), whoosh (1489 "Air woosh", -10 dB) into a big boom (2303 "Futuristic bass hit") on a
  `takeover`, nothing on steps, splits, float/chip cards or the CTA. The SFX are fetched from Mixkit on first use.
- **Plan in this style:** one track, one section `start 0 -> end`, `in cut`, `out cut`, cue a full-groove bar at `cue_at 0`,
  `"style": "steady-funk-bed"` (the default; `"style": "none"` restores the old built-in defaults: 7 dB duck, light synth
  SFX on every graphic). The `tone_map` stays as documentation. Plan keys (duck, eq, sfx.levels, sfx.samples, sfx.auto_map,
  section `level`) override the preset.
- **Demo ducks (sponsored reels).** Under each audible product demo the bed drops a further `demo_duck_db` (default 8 dB;
  attack `demo_duck_attack` 0.12 s, fully down by the first demo sample, release `demo_duck_release` 0.25 s) so the demo is
  heard clearly. The spans come from every `promptDemo` overlay's rows in `edit.json` (`duck_demos`, default true) plus manual
  `duck_windows: [[start, end, extra_db], ...]`. These spans are left out of the section calibration and the style bed checks.
  `music_verify.py` reports `demo_ducks` and fails if a drop is more than 2 dB short of `extra_db`. The speech is untouched as always.
- **Demo segment audibility.** The resolved demo windows carry each row's labelled `segments`, and `music_verify.py`
  (`demo_segments`) fails any segment more than 6 LU under the speaker's normal speech (the median sentence outside the
  demos, measured on the speech stem). Ducking cannot fix a quiet segment: ride it up in the clip (talking-head-edit
  `emotion_ride.py`). A calm half 6.7 dB under the speech fails this check, which matches what listeners heard.
- **Other styles (tone-mapped scoring) still work:** grids fit one tempo to the story anchors so beats land on bar lines,
  tracks are atempo-stretched (within ~8%), sections join/xfade/sweep/filter_out/tape_stop/button, `dropouts` mute between
  grid-snapped times, SFX `extra` hits snap to a grid. Use them only when the reference creator's profile shows switches and
  stops; the verify style check will flag them otherwise.
- **Mix rules.** Section `level` is calibrated after ducking and EQ on the music while the speaker talks. Master -14 LUFS
  integrated, TP <= -1 dBTP. **The speech is never processed**: one constant master gain (fails if |gain| > 0.5 dB) and a
  true-peak guard that trims only music+SFX. Loud sub booms over the loudest words get trimmed by the guard (one reel:
  0.4-1.5 dB under plan); `music_verify.py` reports `sfx_effective` and warns past 2 dB.
- **Verify (`music_verify.py`, hard checks):** -14 +-0.5 LUFS and TP <= -1.0 on the final file; no clipped samples; speech lag
  0 samples; per-sentence speech level (final - music - sfx vs reference) and speech-band level with music within 0.5 dB;
  each demo duck within 2 dB of its `extra_db`; music under speech per section inside the preset's `section_level_range` (-28.7..-25.7 for steady-funk-bed; -30..-26
  without a preset); Whisper WER of the final within 2 points of the reference. **Style check** (preset recorded in
  `plan_resolved.json`, or `--preset`): bed level and dB below the voice, tempo (half/double allowed), track switches and
  mid-reel stops vs the preset max, bed present from <= 0.5 s to the last 0.5 s when the style runs to the last frame, SFX
  events per minute (hits within 0.6 s count as one event), each event's level in range, **palette** (>= 60% of events from
  the reference's common families, e.g. impact/boom), **duck depth** (fail when more than 1 dB over the reference range);
  warnings for no hook SFX, guard-trimmed SFX and bed centroid. The rejected tone-mapped mix fails it on 7 counts (80.7
  BPM, 2 switches, 2 stops, 12.9 SFX/min, quiet UI SFX, 46% palette match, 7 dB duck); the preset-style mix passes. Then
  `$ANA_PY -I $SK/scripts/ref_audio_profile.py mix --speech <reel> --stems <workdir>/music_out --words <words_raw.json> --out v.json`
  and `... plot --profile <profile.json> --mix v.json --out qc.png` draw the mix's bed and SFX contour over the reference reels.
- Worked example (a ~60 s reel): a licensed funk track (110 BPM, D minor) from its groove bar at 7.63 s, one section
  0-60.53, -27.6 LUFS under speech (13.3 dB under the voice); boom 2299 on the hook card (0.9 s) and the closing title
  card (53.37 s); whoosh 1489 into boom 2303 on the takeover (28.53 s). 3 SFX events in 60 s.
- Worked example (a ~67 s sponsored reel with two demos): the same bed, 0-67.13 s, plus demo ducks under the two demos
  (39.50-41.13 language, 50.03-56.63 emotion arc). music_verify:
  - -14.0 LUFS, TP -1.3;
  - speech moved at most 0.2 dB;
  - bed -27.4 LUFS under speech (13.1 dB under the voice);
  - drops of 7.4 and 7.6 dB under the demos, putting each demo 21-23 dB over the music;
  - `demo_segments` sad / excited! / sad / excited! at -2.8 / +0.8 / -3.1 / +2.7 dB against the speaker's normal speech
    (after riding; un-ridden, the sad parts would sit about 11 and 18 dB under).

## Safe zones (hard rules)

- **Face:** measured per reel by `faces.py` (Haar boxes, generous). On a typical desk setup it sits at y 26-58% and x 25-90%.
  talking-head-edit frames every cut so the body Haar box top is at about 29%, the chin median at 54% and the chin max
  at 59%. So the title card bottom (23%), the 75% caption lane and the CTA offer card (capped at 60%) clear the face and
  mouth with no layout change. A smaller face framing (chin about 48-50%) leaves even more room.
  - Overlays live in y < 25% or are full-frame takeovers or promptDemo cards.
  - Captions sit in the 75% lane, below the hands.
  - `verify.py` must report `face_coverage_max` ≈ 0 for non-takeover overlays.
- **Platform UI:** keep text out of the bottom 20% and the right 150px (1080 space).
- **Title-safe:** text stays inside the 80% box. Cards stay inside the 90% box.

## Files

- `scripts/find_python.sh`: prints the Python to use (`$REELKIT_PYTHON`, else `$REELKIT_HOME/.venv`, else the repo's `.venv`, else `python3`)
- `scripts/transcribe.py`: faster-whisper large-v3 word timestamps → `words_raw.json`
- `scripts/faces.py`: face boxes every 0.5s → `faces_src.json`
- `scripts/build_edit.py`: words + overlays.json + faces + cuts → `edit.json` (caption pages, per-shot caption y, overlay frames)
- `scripts/new_reel.sh`: the two-pass setup above
- `scripts/verify.py`: frame count, caption sync, face coverage, corrupt-frame scan, phone-size legibility, captions popping before a full-frame card ends, sponsored demo count, BEFORE/AFTER separator, CTA offer, contact sheet
- `scripts/hold_end.sh`: holds a cut's last frame for N frames (stream copy + an encoded tail, speech checked sample-identical) so an end card can stay up
- `scripts/caption_metrics.py`: regenerates `templates/fonts/caption_metrics.json` (glyph advances used to fit long pages)
- `scripts/repair_remotion.py`: finds corrupt frames in a Remotion render, re-renders each as a verified still and splices it in (one extra CRF 16 encode)
- `templates/hyperframes/scripts/gen.mjs`: edit.json → `index.html` + `compositions/captions.html` + `compositions/overlays.html`. Plain clips + GSAP; passes `hyperframes lint`.
- `templates/remotion/src/Reel.tsx`: the same edit as React `<Sequence>`s over `<OffthreadVideo>`.
  - `remotion.config.ts` sets `chrome-mode=chrome-for-testing`, because Remotion's own headless-shell 157 build aborts on macOS 26 ("icudtl.dat not found in bundle").
- `templates/fonts/`: the bundled OFL fonts (DM Serif Display, Instrument Serif, Inter, Anton, Space Mono) and `caption_metrics.json`.
- `overlays.example.json`: a neutral sponsored template (placeholder sponsor "Acme Voice") that doubles as the worked example. It has a `_doc` block, two promptDemo beats (a language demo and a four-segment emotion arc), the tools chipCard and `sponsored: true`. `new_reel.sh` rewrites its `source` to your input.
- `examples/interview-briefing/`: the custom "briefing" generator for interview reels (`gen.mjs`: `node gen.mjs
  ../edit.json ../overlays.json [--logo logo.svg] [--metrics caption_metrics.json]`, run from the HyperFrames project
  dir; hook-with-logo, question/answer card, ledger, tool hub, blur, meter, payoff, slate) and its template
  `overlays.json`. See **Interview reels**.
- `music_plan.example.json`: a template plan in the default style (one licensed track, one section, preset SFX, demo ducks), with a `_doc` block.
- `music_presets/steady-funk-bed.json`: the default style preset (measured ranges for verify + the music_mix defaults: level, duck, eq, SFX auto map, Mixkit SFX samples)
- `scripts/music_stage.sh`: the default music stage (mix, stream-copy mux, phone copy with the audio copied, true-peak gate on both files, verify); `--no-music` skips it, `--no-whisper` skips the WER check.
- `scripts/music_mix.py`: plan -> grids, beat-aware sections and transitions, ducking, EQ, SFX, master -> `mix.wav`, stems, `plan_resolved.json`
- `scripts/music_fetch.py`: list/get/analyze Mixkit tracks, `sfx-list`/`sfx-get` Mixkit SFX (licensed-source rule, cache + licence sidecar)
- `scripts/ref_audio_profile.py`: `profile` reference reels (Demucs + PANNs + DSP) into a numeric house style + preset; `tracks` scores candidates against a preset; `mix` measures our mix the same way; `plot` draws the mix vs the reference contour
- `scripts/sfx.py`: procedural whoosh, pop, ding, tick, riser, impact, thump (`sfx.py preview <dir>` writes wavs to listen to)
- `scripts/music_verify.py`: loudness/TP, clipping, speech-unchanged, music level per section, demo ducks, demo segment audibility, WER, style check vs the preset, sections image, BEFORE -> AFTER switch SFX warning (`ba_gaps`)
- `scripts/setup_audio.sh`: one-time venv (`~/.cache/reel-audio/venv`: numpy, scipy, librosa, matplotlib); `--analysis` makes `~/.cache/reel-audio/analysis-venv` (+ torch, demucs, panns_inference) and fetches the PANNs checkpoints to `~/panns_data`

## Gotchas

- **A takeover's auto whoosh can bury the last word.** The style puts a whoosh into the boom of every screen takeover,
  starting ~0.7 s before the hit. On one interview reel the end slate followed the payoff word by 0.3 s, so the whoosh
  sat on it and the WER gate failed. When a takeover starts within ~0.8 s of the last spoken word, skip its whoosh
  (`"skip": ["<overlay id>:whoosh"]` in the plan's `sfx` block) and keep the boom.
- **Legibility samples only full-height words.** `height_px` is a glyph-row span, so an x-height-only word ("use", "now")
  measures ~25% shorter at the same font size and failed falsely on one reel. `verify.py` now samples words with a
  capital, digit or ascender when there are 8 or more.
- **The phone copy copies the mastered AAC** (`music_stage.sh`, and Workflow step 8 when the music stage is skipped).
  Re-encoding it pushed the true peak over -1 dBTP; `music_stage.sh` now fails if either file is over -1 dBTP.
- HyperFrames `data-start` must not be negative. `gen.mjs` nudges clip starts 1 ms early so `t = f/fps` lands inside the clip exactly like a Remotion `<Sequence>`.
- HyperFrames lint rejects font families it cannot resolve. Only list the `Reel*` @font-face families (plus bundled Inter/Oswald) in the font stacks.
- Whisper splits "Remotion" into "re -motion" and mishears brand names in general. Always run the corrections.
- Whisper sometimes hallucinates a run of near-zero-length words (e.g. a five-word phrase crammed into 0.08 s that the
  speaker never says). `build_edit.py` prints `WARN n words in x s` when 4+ words fall inside 0.25 s.
  Listen, keep the untouched transcript as `words_raw.whisper.json` and delete the phantom words from `words_raw.json`
  (corrections can only merge tokens, not drop them).
- **Captions vs full-frame cards.** Whisper starts the first word after an insert a few frames early, so its caption
  popped over the fading demo card (4-6 frames early in one reel). `build_edit.py` snaps a page that starts
  in the last 10 frames of a takeover / promptDemo and is spoken at or after the card's end to the card's last frame
  (keeping 4 frames, pushing later pages back), and `verify.py` fails (`caption_card_edge`) if one is left. Also end a
  card's `sansWindows` span before the next spoken word's midpoint, or the snapped caption stays sans.
- Measured on a 60.5s 2160x3840 reel (Mac with 10 cores and 32 GB):
  - **HyperFrames:** 315s (v1), 564s (v2 with 10 overlays), 0 corrupt frames.
  - **Remotion:** 431-487s, plus about 3 minutes of repair.
- **Slow machine = hours, not minutes.** One reel rendered in 360 s; the next version on the same Mac ran on battery in
  Low Power Mode (`pmset -g` shows `powermode 1`) with swap nearly full, and capture took 3 s a frame instead of 0.2.
  Before a 4K render, check `pmset -g batt` and `sysctl vm.swapusage`; plug in and close heavy apps if you can. On that
  run HyperFrames killed FFmpeg after 600 s with no new frame (`ffmpegEncodeTimeout`) after capturing every frame, and
  segmented capture (`HF_SEGMENTED_CAPTURE=true`) failed its 45 s player-ready wait; a third run died at frame 899 when
  one capture made no progress for 60 s (`capture_disk stalled`, after dropping from 5 workers to 1). `render.sh`
  therefore raises `FFMPEG_ENCODE_TIMEOUT_MS`, `HF_DE_STALL_MS` (capture stall), `--player-ready-timeout` and
  `--protocol-timeout` (they only matter when something stalls). Extracted source frames are cached in
  `$TMPDIR/hyperframes-extract-cache-<uid>`, so a retry skips the extraction (18 min on that run, 0.04 s cached).
- Remotion's Chrome capture on macOS can return smeared frames (one pixel row stretched over the frame) or black tiles, at random.
  - The rate was 43/1816 at the default concurrency 5x, 12 at `--concurrency=2`, 10 with `--gl=angle`, 13 with one OffthreadVideo thread and a 4 GB cache, and 12 from a no-B-frame proxy source.
  - None of those fixed it, so `render.sh` uses concurrency 2 plus `repair_remotion.py`.
  - HyperFrames had none.
- A clean contact sheet is not enough on its own, because the corruption is sporadic. Always check `corrupt_frames` = 0 in `verify.py`.
- For iteration, use HyperFrames `--quality draft` or a Remotion `--scale=0.5` still.
- `promptDemo` exists only in the HyperFrames template. Remotion's `Reel.tsx` skips overlay types it does not know, so render demo reels with HyperFrames.
- HyperFrames lint rejects GSAP tweens of `left`/`top` (`gsap_non_transform_motion`). Move things with transforms: the promptDemo playhead is a full-width track tweened by `xPercent` with the visible line inside it.
- Quick stills of a demo span before a full render: `cd <workdir>/hyperframes && npx hyperframes@0.8.214 snapshot --at 35,37.5,51 --no-end --timeout 180000 -o ../qc/snap` (outside the sandbox). It writes `contact-sheet.jpg`.
  A talking-head export has sparse keyframes (7.7 s GOP), so seeking is slow: snapshots can time out (the default 45 s
  and even 180 s on a second run). If they do, render and pull frames from the render instead. Always look at the last
  frame of every promptDemo: the script's last line must sit above the rows card (an earlier version hid the last word).
