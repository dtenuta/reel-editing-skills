// Generate the HyperFrames composition (index.html + compositions/captions.html + compositions/overlays.html)
// from the shared edit.json, so the HyperFrames and Remotion builds play the exact same edit.
// Usage: node scripts/gen.mjs [edit.json]   (run from the project root)
import fs from "node:fs";

const edit = JSON.parse(fs.readFileSync(process.argv[2] || "edit.json", "utf8"));
const css = fs.readFileSync("assets/style.css", "utf8");
const FPS = edit.fps;
const W = edit.width, H = edit.height;
const DUR = edit.durationInFrames / FPS;
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
// promptDemo emotion-marker tones (v6): sad slate, hopeful ochre, excited brand red. Keep in sync with style.css .sg/.sb.
const PD_TONE = { cool: "#56697A", mid: "#C28A2C", hot: "#B23A2E" };
// Clip windows: frame f is visible when from <= f < to. Times are nudged 1 ms early so the
// frame-start sampling (t = f / fps) lands inside [start, start + duration) exactly like a Remotion <Sequence>.
const t0 = (f) => Math.max(0, +(f / FPS - 0.001).toFixed(4));
const sec = (f) => +(f / FPS).toFixed(4);
const clipAttrs = (from, to, track, cls = "") =>
  `class="clip${cls ? " " + cls : ""}" data-start="${t0(from)}" data-duration="${+((to - from) / FPS).toFixed(4)}" data-track-index="${track}"`;

const FONTS = `
@font-face { font-family: 'ReelCapSerif'; src: url('assets/fonts/dm-serif-display-latin-400-normal.woff2') format('woff2'); font-style: normal; }
@font-face { font-family: 'ReelCapSerif'; src: url('assets/fonts/dm-serif-display-latin-400-italic.woff2') format('woff2'); font-style: italic; }
@font-face { font-family: 'ReelSerif'; src: url('assets/fonts/instrument-serif-latin-400-normal.woff2') format('woff2'); font-style: normal; }
@font-face { font-family: 'ReelSerif'; src: url('assets/fonts/instrument-serif-latin-400-italic.woff2') format('woff2'); font-style: italic; }
@font-face { font-family: 'ReelSans'; src: url('assets/fonts/inter-latin-900-normal.woff2') format('woff2'); font-weight: 900; }
@font-face { font-family: 'ReelSans'; src: url('assets/fonts/inter-latin-800-normal.woff2') format('woff2'); font-weight: 800; }
@font-face { font-family: 'ReelCond'; src: url('assets/fonts/Anton.ttf') format('truetype'); }`;

// Shared waveform (same formula as the Remotion build)
const WAVE = Array.from({ length: 36 }, (_, i) =>
  Math.round(26 + 160 * Math.abs(0.6 * Math.sin(i * 0.9) + 0.4 * Math.sin(i * 0.37 + 1))));

// ---------------- captions scene ----------------
const capHtml = [], capJs = [];
for (const c of edit.captions) {
  const id = `cap-${c.id}`;
  // data-t feeds the stroked ::before copy (dark outline behind the fill); --fs is the per-page fitted size
  capHtml.push(`    <div id="${id}" ${clipAttrs(c.from, c.to, 1, c.style === "sans" ? "cap sans" : "cap")} style="top:${c.y - 90}px"><span class="w" data-t="${esc(c.text)}"${c.fs ? ` style="--fs:${c.fs}px"` : ""}>${esc(c.text)}</span></div>`);
  // 4-frame pop starting one frame before the clip shows (matches tw(f,-1,4,...) in Remotion)
  const pos = (c.from - 1) / FPS;
  if (pos >= 0) {
    capJs.push(`tl.fromTo("#${id} .w",{opacity:0,y:14,scale:.86},{opacity:1,y:0,scale:1,duration:${4 / FPS},ease:"power2.out"},${pos.toFixed(4)});`);
  } else { // first word at frame 0: begin a quarter of the way in (power2.out(0.25) = 0.4375)
    const k = 0.4375;
    capJs.push(`tl.fromTo("#${id} .w",{opacity:${k},y:${14 * (1 - k)},scale:${0.86 + 0.14 * k}},{opacity:1,y:0,scale:1,duration:${3 / FPS},ease:"power2.out"},0);`);
  }
}
const captionsScene = `<template>
  <style>${FONTS}
${css}
    #captions { position: absolute; inset: 0; }
  </style>
  <div id="captions" data-composition-id="captions" data-width="${W}" data-height="${H}">
${capHtml.join("\n")}
  </div>
  <script>
    const tl = gsap.timeline({ paused: true });
    ${capJs.join("\n    ")}
    window.__timelines["captions"] = tl;
  </script>
</template>
`;

// ---------------- overlays scene ----------------
const ovHtml = [], ovJs = [];
// pop in over 6 frames (back.out(1.7)), out over the last 5 (power2.in), same as pop() in Remotion
const pop = (sel, from, to, delay = 0, fromScale = 0.7, extraFrom = "", extraTo = "") => {
  ovJs.push(`tl.fromTo("${sel}",{scale:${fromScale}${extraFrom}},{scale:1${extraTo},duration:${6 / FPS},ease:"back.out(1.7)"},${sec(from + delay)});`);
  ovJs.push(`tl.fromTo("${sel}",{opacity:0},{opacity:1,duration:${6 / FPS},ease:"power2.out"},${sec(from + delay)});`);
  ovJs.push(`tl.fromTo("${sel}",{scale:1,opacity:1},{scale:.85,opacity:0,duration:${5 / FPS},ease:"power2.in",immediateRender:false},${sec(to - 5)});`);
};
const MAG = `<svg width="52" height="52" viewBox="0 0 24 24"><circle cx="10" cy="10" r="6.5" fill="none" stroke="#5f6368" stroke-width="2.4"/><path d="M15 15l5.5 5.5" stroke="#5f6368" stroke-width="2.6" stroke-linecap="round"/></svg>`;
const MIC = `<svg width="44" height="52" viewBox="0 0 20 24"><rect x="6" y="2" width="8" height="13" rx="4" fill="#4285F4"/><path d="M3 11a7 7 0 0 0 14 0" fill="none" stroke="#34A853" stroke-width="2.2"/><path d="M10 18v4" stroke="#FBBC05" stroke-width="2.2"/></svg>`;
// CTA offer text: one .ln per "\n" line; ALL-CAPS words (2+ letters, e.g. "FREE") become .em (white on red).
// Keep in sync with offerParts() in Reel.tsx.
const EMW = /^([^A-Za-z]*)([A-Z]{2,})([^A-Za-z]*)$/;   // "FREE", "(FREE)", "FREE!" -> the letters get the box
const offerHtml = (s) => String(s).split("\n").map((ln) =>
  `<div class="ln">${ln.split(/(\s+)/).map((w) => {
    const m = w.match(EMW);
    return m ? `${esc(m[1])}<span class="em">${esc(m[2])}</span>${esc(m[3])}` : esc(w);
  }).join("")}</div>`).join("");
const LENS = `<svg width="50" height="50" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="5" fill="none" stroke="#4285F4" stroke-width="2.2"/><circle cx="12" cy="12" r="3.6" fill="#EA4335"/></svg>`;

for (const o of edit.overlays) {
  const id = `ov-${o.id}`;
  if (o.type === "titleCard") {
    ovHtml.push(`    <div id="${id}" ${clipAttrs(o.from, o.to, 2, "tcard")}><div class="box"><div class="t">${esc(o.title)}</div><div class="s">${esc(o.subtitle)}</div></div></div>`);
    pop(`#${id} .box`, o.from, o.to);
  } else if (o.type === "cta") {
    ovHtml.push(`    <div id="${id}" ${clipAttrs(o.from, o.to, 2, "cta")}><div class="k">${esc(o.kicker)}</div><div class="box"><div class="word">${esc(o.word)}</div></div></div>`);
    pop(`#${id} .k`, o.from, o.to, 0, 0.6);
    pop(`#${id} .box`, o.from, o.to, 3, 0.6);
    if (o.offer) {
      // brand offer line (sponsored reels): a paper card under the chin, own clip on track 3 (same window
      // as the CTA), pops at offerF. ALL-CAPS words get the red box ("FREE"); "\n" in the text forces a line break.
      ovHtml.push(`    <div id="${id}-offer" ${clipAttrs(o.from, o.to, 3, "ctaoffer")} style="top:${o.offerY ?? 1092}px"><div class="card" style="font-size:${o.offerFs ?? 44}px">${offerHtml(o.offer)}</div></div>`);
      pop(`#${id}-offer .card`, o.from, o.to, Math.max(0, (o.offerF ?? o.from + 10) - o.from), 0.8, `,rotation:-6`, `,rotation:-1.5`);
    }
  } else if (o.type === "floatCard") {
    const flags = o.flag === "us-es" ? `<div class="flag-us"></div><div class="arrow">→</div><div class="flag-es"></div>` : `<div class="flag-es"></div>`;
    const inner = o.flag
      ? `<div class="kick">${esc(o.label)}</div><div class="row">${flags}<div class="lang">${esc(o.small)}</div></div>`
      : `${o.kicker ? `<div class="kick">${esc(o.kicker)}</div>` : ""}${o.big ? `<div class="big">${esc(o.big)}</div>` : ""}<div class="label">${esc(o.label)}</div>${o.small ? `<div class="small">${esc(o.small)}</div>` : ""}`;
    ovHtml.push(`    <div id="${id}" ${clipAttrs(o.from, o.to, 2, "fcard " + o.side)}${o.width ? ` style="width:${o.width}px"` : ""}><div class="card">${inner}</div></div>`);
    pop(`#${id} .card`, o.from, o.to, 0, 0.7, `,rotation:${o.rotate - 8}`, `,rotation:${o.rotate}`);
  } else if (o.type === "steps") {
    // numbered list that builds as the speaker says each step; the current step's number is red, earlier ones dim
    const rows = o.items.map((it, i) => `<div class="it" id="${id}-i${i}"><div class="n">${i + 1}</div><div class="tx">${esc(it.text)}</div></div>`).join("");
    ovHtml.push(`    <div id="${id}" ${clipAttrs(o.from, o.to, 2, "steps")}><div class="card"><div class="kick">${esc(o.kicker)}</div>${rows}</div></div>`);
    pop(`#${id} .card`, o.from, o.to, 0, 0.7, `,rotation:${(o.rotate ?? -1.5) - 6}`, `,rotation:${o.rotate ?? -1.5}`);
    o.items.forEach((it, i) => {
      ovJs.push(`tl.fromTo("#${id}-i${i}",{opacity:0,x:-40},{opacity:1,x:0,duration:${6 / FPS},ease:"back.out(1.7)"},${sec(Math.max(it.f, o.from))});`);
      ovJs.push(`tl.fromTo("#${id}-i${i} .n",{backgroundColor:"#d9d3cb"},{backgroundColor:"#B23A2E",duration:${3 / FPS},ease:"none"},${sec(Math.max(it.f, o.from))});`);
      const nx = o.items[i + 1];
      if (nx) {
        ovJs.push(`tl.fromTo("#${id}-i${i} .n",{backgroundColor:"#B23A2E"},{backgroundColor:"#d9d3cb",duration:${3 / FPS},ease:"none",immediateRender:false},${sec(nx.f)});`);
        ovJs.push(`tl.fromTo("#${id}-i${i} .tx",{opacity:1},{opacity:.5,duration:${3 / FPS},ease:"none",immediateRender:false},${sec(nx.f)});`);
      }
    });
  } else if (o.type === "split") {
    // old way vs new way: the old pane pops first, the new pane at newF
    ovHtml.push(`    <div id="${id}" ${clipAttrs(o.from, o.to, 2, "split")}><div class="pane old"><div class="lab">${esc(o.oldLabel || "Old way")}</div><div class="tx">${esc(o.old)}</div></div><div class="pane new"><div class="lab">${esc(o.newLabel || "New way")}</div><div class="tx">${esc(o.new)}</div></div></div>`);
    pop(`#${id} .old`, o.from, o.to, 0, 0.7, `,rotation:-8`, `,rotation:-2`);
    pop(`#${id} .new`, o.from, o.to, o.newF - o.from, 0.7, `,rotation:8`, `,rotation:2`);
  } else if (o.type === "chipCard") {
    // generic text chips (tool names as words, never logos)
    const chips = o.chips.map((c, i) => `<div class="chip" id="${id}-c${i}">${esc(c)}</div>`).join("");
    ovHtml.push(`    <div id="${id}" ${clipAttrs(o.from, o.to, 2, "chipcard " + (o.side || "center"))}><div class="card"><div class="kick">${esc(o.kicker)}</div><div class="chips">${chips}</div>${o.small ? `<div class="small">${esc(o.small)}</div>` : ""}</div></div>`);
    pop(`#${id} .card`, o.from, o.to, 0, 0.7, `,rotation:${(o.rotate ?? 2) + 6}`, `,rotation:${o.rotate ?? 2}`);
    o.chips.forEach((_, i) => {
      ovJs.push(`tl.fromTo("#${id}-c${i}",{opacity:0,scale:.6},{opacity:1,scale:1,duration:${6 / FPS},ease:"back.out(1.7)"},${sec(o.from + 4 + 4 * i)});`);
    });
  } else if (o.type === "promptDemo") {
    // Audible product demo beat (sponsored reels): full-frame editorial card over the demo audio.
    // Label pill (no logos), DM Serif title, a typed prompt, the model script, and one waveform row per clip
    // (BEFORE / AFTER / single) whose bars are the reel's own audio (build_edit --video). A playhead runs over
    // each row while its audio plays and colours the played bars. Optional o.video = a screen recording that
    // replaces the card visual (root-level muted clip, same timing); the label and title stay.
    const f = o.from, len = o.to - o.from;
    const tagCls = (r) => (r.kind === "before" ? "before" : r.kind === "after" ? "after" : "plain");
    const chars = [...String(o.prompt || "")];
    // the typed prompt sits over an invisible full copy (.ghost) that fixes the card height from the first frame, so the
    // rows card under it never jumps down when the prompt wraps to another line while typing (it once jumped 53px mid-demo)
    const typed = chars.map((ch, i) => `<span class="c" id="${id}-k${i}">${esc(ch)}</span>`).join("");
    const script = esc(o.script || "").replace(/\[([^\]]+)\]/g, '<b>[$1]</b>');
    const rowsHtml = (o.rows || []).map((r, ri) => {
      const span = Math.max(1, r.rt - r.rf);
      const pct = (fr) => Math.max(0, Math.min(100, ((fr - r.rf) / span) * 100)).toFixed(2);
      const bars = (r.bars || WAVE.slice(0, 36).map((h) => h / 186)).map((h, i) =>
        `<i id="${id}-r${ri}b${i}" style="height:${Math.round(8 + 104 * h)}px"></i>`).join("");
      const mark = r.mark ? `<div class="mk ${r.mark.strike ? "strike" : ""}" id="${id}-r${ri}m" style="left:${pct(r.mark.mf)}%;width:${(pct(r.mark.mt) - pct(r.mark.mf)).toFixed(2)}%"><b>${esc(r.mark.label || "")}</b></div>` : "";
      // emotion markers (v6): one bracket + label per segment, any number of segments (sad -> excited -> sad -> excited).
      // tone cool (sad, slate) / mid (hopeful, ochre) / hot (excited, red); the label shrinks (46 -> 30px) to fit its span.
      const segs = (r.segments || []).map((g, gi) => {
        const tn = PD_TONE[g.tone] ? g.tone : gi ? "hot" : "cool";
        const l = +pct(g.sf), w = Math.max(0, +pct(g.st) - l);
        const fs = Math.max(30, Math.min(46, Math.floor(((w / 100) * 872) / (0.5 * Math.max(1, String(g.label).length)))));
        return `<div class="sb ${tn}" id="${id}-r${ri}k${gi}" style="left:calc(${l.toFixed(2)}% + 3px);width:calc(${w.toFixed(2)}% - 6px)"></div>` +
          `<div class="sg ${tn}" id="${id}-r${ri}s${gi}" style="left:${l.toFixed(2)}%;font-size:${fs}px">${esc(g.label)}</div>`;
      }).join("");
      return `<div class="row ${tagCls(r)}" id="${id}-r${ri}"><div class="top"><div class="tag">${esc(r.tag || "")}</div></div><div class="wv">${mark}${bars}<div class="ph" id="${id}-r${ri}p"><em></em></div></div>${segs ? `<div class="bot">${segs}</div>` : ""}</div>`;
    }).join("");
    // .stack (v6): the rows card flows under the prompt card (grid, first track >= 348px so short prompts keep the
    // v5 layout with rows at y 770); a long prompt + script pushes the rows down instead of hiding the script's end.
    // BEFORE -> AFTER switch band (v7, after a sponsor's reviewer heard a repeated "change your ..." line as an audio
    // glitch): a big full-width band under the rows reads BEFORE while the before clip plays and wipes to AFTER
    // ending exactly on the AFTER clip's first frame (build_edit "ba": switchF = the after row's rf). music_mix puts a
    // whoosh in the gap. Opt out per demo with "switchLabel": false (then the clip must say "before"/"after" itself).
    const ba = o.video ? null : (o.ba || []).find((p) => p.label);
    const baHtml = ba ? `<div class="ba" id="${id}-ba"><div class="bf"><span>${esc(o.beforeLabel || "Before")}</span></div><div class="af" id="${id}-baf"><span>${esc(o.afterLabel || "After")}</span></div></div>` : "";
    const card = o.video ? "" : `<div class="stack${ba ? " hasba" : ""}"><div class="pcard"><div class="pk">Prompt</div><div class="pt"><span class="ghost">${esc(o.prompt || "")}<span class="caret"></span></span><div class="ptt">${typed}<span class="caret"></span></div></div>${o.script ? `<div class="ps"${String(o.script).length > 150 ? ' style="font-size:24px;line-height:1.3"' : ""}>${script}</div>` : ""}</div>
      <div class="rows">${rowsHtml}</div>${baHtml}</div>`;
    ovHtml.push(`    <div id="${id}" ${clipAttrs(o.from, o.to, 2, "pdemo" + (o.video ? " vid" : ""))}><div class="fade">${o.video ? "" : `<div class="bgk"></div>`}
      <div class="lab">${esc(o.label || "Product demo")}</div><div class="ttl" style="font-size:${Math.min(100, Math.floor(940 / (0.5 * Math.max(1, String(o.title || "").length))))}px">${esc(o.title || "")}</div>
      ${card}</div></div>`);
    if (!o.video) ovJs.push(`tl.fromTo("#${id} .bgk",{opacity:0},{opacity:1,duration:${2 / FPS},ease:"power2.out"},${sec(f)});`);
    ovJs.push(`tl.fromTo("#${id} .fade",{opacity:1},{opacity:0,duration:${4 / FPS},ease:"power2.in",immediateRender:false},${sec(f + len - 4)});`);
    ovJs.push(`tl.fromTo("#${id} .lab",{opacity:0,y:-30},{opacity:1,y:0,duration:${6 / FPS},ease:"back.out(1.7)"},${sec(f + 1)});`);
    ovJs.push(`tl.fromTo("#${id} .ttl",{opacity:0,scale:.8},{opacity:1,scale:1,duration:${6 / FPS},ease:"back.out(1.7)"},${sec(f + 2)});`);
    if (!o.video) {
      ovJs.push(`tl.fromTo("#${id} .pcard",{opacity:0,y:40,scale:.94},{opacity:1,y:0,scale:1,duration:${6 / FPS},ease:"back.out(1.7)"},${sec(f + 2)});`);
      ovJs.push(`tl.fromTo("#${id} .rows",{opacity:0,y:50},{opacity:1,y:0,duration:${6 / FPS},ease:"back.out(1.7)"},${sec(f + 3)});`);
      // typewriter: chars appear one by one between typeF and typeEndF (default ~1.4 chars/frame)
      const tf = Math.max(f + 3, o.typeF), te = o.typeEndF ?? tf + Math.ceil(chars.length / 1.4);
      chars.forEach((_, i) => ovJs.push(`tl.fromTo("#${id}-k${i}",{display:"none"},{display:"inline",duration:0.001},${((tf + (te - tf) * (i / Math.max(1, chars.length))) / FPS).toFixed(4)});`));
      if (o.script) ovJs.push(`tl.fromTo("#${id} .ps",{opacity:0},{opacity:1,duration:${5 / FPS},ease:"power2.out"},${sec(te)});`);
      if (ba) {
        const sw = ba.switchF, bRow = o.rows[ba.b];
        ovJs.push(`tl.fromTo("#${id}-ba",{opacity:0,y:50},{opacity:1,y:0,duration:${6 / FPS},ease:"back.out(1.7)"},${sec(f + 3)});`);
        // the wipe lands on the AFTER audio's first frame; it starts inside the gap (>= 0.4 s, 12 frames)
        ovJs.push(`tl.fromTo("#${id}-baf",{clipPath:"inset(0% 100% 0% 0%)"},{clipPath:"inset(0% 0% 0% 0%)",duration:${4 / FPS},ease:"power2.inOut"},${sec(sw - 4)});`);
        ovJs.push(`tl.fromTo("#${id}-baf span",{scale:1.3},{scale:1,duration:${6 / FPS},ease:"back.out(2)"},${sec(sw - 1)});`);
        // the finished BEFORE row dims as the gap starts, so the eye goes to the AFTER row
        ovJs.push(`tl.fromTo("#${id}-r${ba.b}",{opacity:1},{opacity:.45,duration:${4 / FPS},ease:"power2.out",immediateRender:false},${sec(bRow.rt)});`);
      }
      (o.rows || []).forEach((r, ri) => {
        const n = (r.bars || WAVE.slice(0, 36)).length, rs = r.rf / FPS, rd = Math.max(1, r.rt - r.rf) / FPS;
        const segsR = r.segments || [];
        // played bars take the colour of the emotion segment they fall in (only when a row has 2+ segments)
        const played = (i) => {
          if (r.kind === "before") return "#2A2623";
          if (segsR.length < 2) return "#B23A2E";
          const fr = r.rf + (r.rt - r.rf) * ((i + 0.5) / n);
          let k = segsR.findIndex((g) => fr >= g.sf && fr < g.st);
          if (k < 0) k = fr < segsR[0].sf ? 0 : segsR.length - 1;
          return PD_TONE[PD_TONE[segsR[k].tone] ? segsR[k].tone : k ? "hot" : "cool"];
        };
        if (ri > 0) ovJs.push(`tl.fromTo("#${id}-r${ri}",{opacity:.45},{opacity:1,duration:${4 / FPS},ease:"power2.out",immediateRender:false},${(Math.max(f, r.rf - 3) / FPS).toFixed(4)});`);
        if (ri > 0) ovJs.push(`tl.set("#${id}-r${ri}",{opacity:.45},${sec(f)});`);
        ovJs.push(`tl.fromTo("#${id}-r${ri}p",{xPercent:0},{xPercent:100,duration:${rd.toFixed(4)},ease:"none"},${rs.toFixed(4)});`);
        ovJs.push(`tl.set("#${id}-r${ri}p",{opacity:0},${sec(f)});`);
        ovJs.push(`tl.set("#${id}-r${ri}p",{opacity:1},${rs.toFixed(4)});`);
        ovJs.push(`tl.to("#${id}-r${ri}p",{opacity:0,duration:${3 / FPS}},${(rs + rd).toFixed(4)});`);
        for (let i = 0; i < n; i++)
          ovJs.push(`tl.fromTo("#${id}-r${ri}b${i}",{backgroundColor:"#D9D3CB"},{backgroundColor:"${played(i)}",duration:${2 / FPS},ease:"none"},${(rs + rd * (i / n)).toFixed(4)});`);
        if (r.mark) ovJs.push(`tl.fromTo("#${id}-r${ri}m b",{opacity:0,y:16,scale:.7},{opacity:1,y:0,scale:1,duration:${5 / FPS},ease:"back.out(2)"},${sec(Math.max(f + 3, r.mark.mf))});`);
        segsR.forEach((g, gi) => {
          const at = Math.max(f + 3, g.sf);
          // the bracket draws left to right with the playhead across its segment; the label pops as the segment starts
          ovJs.push(`tl.fromTo("#${id}-r${ri}k${gi}",{scaleX:0},{scaleX:1,duration:${(Math.max(2, g.st - at) / FPS).toFixed(4)},ease:"none"},${sec(at)});`);
          ovJs.push(`tl.fromTo("#${id}-r${ri}s${gi}",{opacity:0,scale:.6},{opacity:1,scale:1,duration:${5 / FPS},ease:"back.out(2)"},${sec(at)});`);
        });
      });
    }
  } else if (o.type === "takeover") {
    const bars = WAVE.map((h, i) => `<i id="${id}-b${i}" style="height:${h}px"></i>`).join("");
    const chips = o.chips.map((c) => `<div class="chip">${esc(c)}</div>`).join("");
    ovHtml.push(`    <div id="${id}" ${clipAttrs(o.from, o.to, 2, "tk")}><div class="fade"><div class="bgk"></div>
      <div class="pill">${MAG}<div class="q"><span>${esc(o.query)}</span></div>${MIC}${LENS}</div>
      <div class="sheet"><div class="ct">${esc(o.cardTitle)}</div><div class="cs">${esc(o.cardSub)}</div><div class="wave">${bars}</div><div class="chips">${chips}</div></div></div></div>`);
    const f = o.from, len = o.to - o.from;
    ovJs.push(`tl.fromTo("#${id} .bgk",{opacity:0},{opacity:1,duration:${3 / FPS},ease:"power2.out"},${sec(f)});`);
    ovJs.push(`tl.fromTo("#${id} .fade",{opacity:1},{opacity:0,duration:${4 / FPS},ease:"power2.in",immediateRender:false},${sec(f + len - 4)});`);
    ovJs.push(`tl.fromTo("#${id} .pill",{y:-60},{y:0,duration:${6 / FPS},ease:"back.out(1.7)"},${sec(f + 2)});`);
    ovJs.push(`tl.fromTo("#${id} .pill",{opacity:0},{opacity:1,duration:${6 / FPS},ease:"power2.out"},${sec(f + 2)});`);
    ovJs.push(`tl.fromTo("#${id} .q span",{clipPath:"inset(0% 100% 0% 0%)"},{clipPath:"inset(0% 0% 0% 0%)",duration:${14 / FPS},ease:"none"},${sec(f + 6)});`);
    ovJs.push(`tl.fromTo("#${id} .sheet",{y:60,scale:.88},{y:0,scale:1,duration:${6 / FPS},ease:"back.out(1.7)"},${sec(f + 8)});`);
    ovJs.push(`tl.fromTo("#${id} .sheet",{opacity:0},{opacity:1,duration:${6 / FPS},ease:"power2.out"},${sec(f + 8)});`);
    WAVE.forEach((_, i) =>
      ovJs.push(`tl.fromTo("#${id}-b${i}",{scaleY:.08},{scaleY:1,duration:${8 / FPS},ease:"power2.out"},${((f + 12 + i * 0.5) / FPS).toFixed(4)});`));
  }
}
const overlaysScene = `<template>
  <style>${FONTS}
${css}
    #overlays { position: absolute; inset: 0; }
    .tk .fade, .pdemo .fade { position: absolute; inset: 0; }
  </style>
  <div id="overlays" data-composition-id="overlays" data-width="${W}" data-height="${H}" data-layout-allow-overlap>
${ovHtml.join("\n")}
  </div>
  <script>
    const tl = gsap.timeline({ paused: true });
    ${ovJs.join("\n    ")}
    window.__timelines["overlays"] = tl;
  </script>
</template>
`;

// ---------------- root ----------------
// promptDemo with a screen recording: a full-frame bg + muted video clip under the overlays layer, same timing.
const demoVids = edit.overlays.filter((o) => o.type === "promptDemo" && o.video).map((o, i) =>
  `      <div class="clip pdemo-vbg" data-start="${t0(o.from)}" data-duration="${+((o.to - o.from) / FPS).toFixed(4)}" data-track-index="${3 + 2 * i}"></div>
      <video id="dv-${o.id}" class="clip pdemo-v" src="assets/${o.video}" muted playsinline data-start="${t0(o.from)}" data-duration="${+((o.to - o.from) / FPS).toFixed(4)}" data-track-index="${4 + 2 * i}"></video>`).join("\n");
const index = `<!doctype html>
<html lang="en" data-resolution="portrait">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=${W}, height=${H}" />
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      * { margin: 0; padding: 0; box-sizing: border-box; }
      html, body { margin: 0; width: ${W}px; height: ${H}px; overflow: hidden; background: #000; }
      #root { position: relative; width: 100%; height: 100%; }
      #src { position: absolute; left: 0; top: 0; width: ${W}px; height: ${H}px; object-fit: cover; }
      .pdemo-vbg { position: absolute; inset: 0; z-index: 5; background-color: #8E1F17; }
      .pdemo-v { position: absolute; left: ${Math.round(W * 0.065)}px; top: ${Math.round(H * 0.19)}px; width: ${Math.round(W * 0.87)}px;
        height: ${Math.round(H * 0.47)}px; object-fit: contain; z-index: 6; border-radius: ${Math.round(W * 0.026)}px; }
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="${DUR.toFixed(4)}" data-width="${W}" data-height="${H}">
      <video id="src" class="clip" src="assets/${edit.source}" data-start="0" data-duration="${DUR.toFixed(4)}" data-track-index="0" playsinline data-has-audio="true"></video>
${demoVids}
      <div id="overlays" data-composition-id="overlays" data-composition-src="compositions/overlays.html" data-start="0" data-duration="${DUR.toFixed(4)}" data-track-index="2" data-track-kind="graphics" data-width="${W}" data-height="${H}" style="position:absolute;inset:0;z-index:10"></div>
      <div id="captions" data-composition-id="captions" data-composition-src="compositions/captions.html" data-start="0" data-duration="${DUR.toFixed(4)}" data-track-index="1" data-track-kind="captions" data-width="${W}" data-height="${H}" style="position:absolute;inset:0;z-index:30"></div>
    </div>
    <script>
      const tl = gsap.timeline({ paused: true });
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
`;
fs.mkdirSync("compositions", { recursive: true });
fs.writeFileSync("compositions/captions.html", captionsScene);
fs.writeFileSync("compositions/overlays.html", overlaysScene);
fs.writeFileSync("index.html", index);
console.log(`captions: ${edit.captions.length}, overlays: ${edit.overlays.length}, duration ${DUR.toFixed(4)}s`);
