// Interview "briefing" look for HyperFrames: a custom per-reel style (not the default template) for a two-person
// interview reel. Every on-screen word comes from overlays.json (see the example next to this file), so swap the
// texts and the beat times for your own reel. No label narrates the cliffhanger device ("open question", "answer at
// the end", reminder chips): the question plants itself through the host's words and the question card while it is asked.
// Dark monochrome glass panels in the top band (y 150-470, clear of the face and the platform's top UI), Inter 900
// heads, Space Mono labels, one signal red (#FF4D2E). The hook is framed by the guest's company mark: pass your own
// logo with --logo (a plain SVG made of <path d=...> elements only; anything else is rejected), or leave it out and a
// neutral placeholder ring plus the company name from hook.company is used.
// Usage (run from the HyperFrames project dir):
//   node <skill>/examples/interview-briefing/gen.mjs ../edit.json ../overlays.json [--logo logo.svg] [--metrics caption_metrics.json]
import fs from "node:fs";

const USAGE = "usage: node gen.mjs <edit.json> <overlays.json> [--logo logo.svg] [--metrics caption_metrics.json]";
const argv = process.argv.slice(2), pos = [], opt = {};
for (let i = 0; i < argv.length; i++) {
  if (argv[i] === "--logo" || argv[i] === "--metrics") opt[argv[i].slice(2)] = argv[++i];
  else if (argv[i] === "-h" || argv[i] === "--help") { console.log(USAGE); process.exit(0); }
  else pos.push(argv[i]);
}
const [editP, ovP] = pos;
if (!editP || !ovP) { console.error(USAGE); process.exit(2); }
const metP = opt.metrics || new URL("../../templates/fonts/caption_metrics.json", import.meta.url);
const edit = JSON.parse(fs.readFileSync(editP, "utf8"));
const cfg = JSON.parse(fs.readFileSync(ovP, "utf8"));
const MET = JSON.parse(fs.readFileSync(metP, "utf8")).sans;
const FPS = edit.fps, W = edit.width, H = edit.height, NF = edit.durationInFrames, DUR = NF / FPS;
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const t0 = (f) => Math.max(0, +(f / FPS - 0.001).toFixed(4));
const clipAttrs = (from, to, track, cls = "") =>
  `class="clip${cls ? " " + cls : ""}" data-start="${t0(from)}" data-duration="${+((to - from) / FPS).toFixed(4)}" data-track-index="${track}"`;
const ov = Object.fromEntries(edit.overlays.map((o) => [o.id, o]));

// ---------- logo: optional; validate, then inline every path in currentColor ----------
const PLACEHOLDER_D = "M12 0a12 12 0 1 0 0 24a12 12 0 1 0 0-24zm0 6a6 6 0 1 1 0 12a6 6 0 1 1 0-12z";
let LOGO = null;
if (opt.logo) {
  const svg = fs.readFileSync(opt.logo, "utf8");
  if (/<script|<foreignObject|<image|<use\b|<style|\son[a-z]+\s*=|href\s*=|url\(|@import|javascript:/i.test(svg)) throw new Error("logo: active content");
  const vbm = svg.match(/<svg\b[^>]*\bviewBox="\s*([-\d.]+)[\s,]+([-\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)\s*"/);
  if (!vbm) throw new Error("logo: the <svg> needs a viewBox");
  const tags = [...svg.matchAll(/<path\b[^>]*>/g)].map((m) => m[0]);
  if (!tags.length) throw new Error("logo: expected one or more <path d=...>");
  const paths = tags.map((t) => {
    const d = (t.match(/\sd="([^"]+)"/) || [])[1];
    if (!d) throw new Error("logo: every <path> needs a d attribute");
    if (!/^[MmLlHhVvCcSsQqTtAaZz0-9.,eE\-\s]+$/.test(d)) throw new Error("logo: path data has non-path characters");
    const rule = (t.match(/\sfill-rule="(nonzero|evenodd)"/) || [])[1];
    return { d, rule };
  });
  LOGO = { vb: vbm.slice(1, 5).map(Number), paths };
}
const company = String((ov.hook && ov.hook.company) || "COMPANY");
const logoSvg = (maxW, maxH) => {
  const [x, y, vw, vh] = LOGO.vb;
  const w = Math.min(maxW, maxH * vw / vh);
  return `<svg class="logo" viewBox="${x} ${y} ${vw} ${vh}" width="${+w.toFixed(1)}" height="${+(w * vh / vw).toFixed(1)}" aria-label="${esc(company)}">` +
    LOGO.paths.map((p) => `<path fill="currentColor"${p.rule ? ` fill-rule="${p.rule}"` : ""} d="${p.d}"/>`).join("") + `</svg>`;
};

// ---------- text fitting (Inter 900 advances from caption_metrics.json, em units) ----------
const emW = (s, ls = -0.025) => [...s].reduce((a, ch) => a + (MET.adv[ch] ?? MET.default), 0) + ls * Math.max(0, [...s].length - 1);
const fit = (s, maxW, maxPx, ls = -0.025) => Math.min(maxPx, Math.floor(maxW / emW(s, ls)));

// ---------- tween helpers: every tween is a fromTo with immediateRender:false; CSS holds the initial state ----------
const J = [];
const tw = (sel, from, to, at, durF, ease = "power2.out", more = {}) =>
  J.push(`tl.fromTo(${JSON.stringify(sel)},${JSON.stringify(from)},${JSON.stringify({ ...to, duration: +(durF / FPS).toFixed(4), ease, immediateRender: false, ...more })},${(+at).toFixed(4)});`);
const popIn = (sel, at, d = 6, s0 = 0.7, ease = "back.out(1.7)") => { tw(sel, { opacity: 0 }, { opacity: 1 }, at, d, "power2.out"); tw(sel, { scale: s0 }, { scale: 1 }, at, d, ease); };
const fadeOut = (sel, at, d = 5) => tw(sel, { opacity: 1 }, { opacity: 0 }, at, d, "power2.in");
const slam = (sel, at, rot, d = 5) => { tw(sel, { opacity: 0 }, { opacity: 1 }, at, 2, "none"); tw(sel, { xPercent: -50, yPercent: -50, scale: 1.6, rotation: rot }, { xPercent: -50, yPercent: -50, scale: 1, rotation: rot }, at, d, "power4.out"); };
const html = [];
const ARROW = (dir, c, w = 54) => `<svg width="${w}" height="${w}" viewBox="0 0 24 24"><path d="${dir === "down" ? "M12 3v15M5 12l7 7 7-7" : "M12 21V6M5 12l7-7 7 7"}" fill="none" stroke="${c}" stroke-width="3.2" stroke-linecap="square"/></svg>`;
const XMARK = (c, w = 58) => `<svg width="${w}" height="${w}" viewBox="0 0 24 24"><path d="M4 4l16 16M20 4L4 20" fill="none" stroke="${c}" stroke-width="3.6" stroke-linecap="square"/></svg>`;
const lines = (s) => String(s).split("\n").map(esc).join("<br>");

// ===== 1. hook: the company mark with a red question mark on the host's question, then the guest line =====
if (ov.hook) {
  const o = ov.hook, end = o.to / FPS;
  const mark = LOGO ? logoSvg(520, 200)
    : `<svg class="logo" viewBox="0 0 24 24" width="128" height="128" aria-hidden="true"><path fill="currentColor" fill-rule="evenodd" d="${PLACEHOLDER_D}"/></svg>` +
      `<span class="cname hd" style="font-size:${fit(company, 380, 150)}px">${esc(company)}</span>`;
  html.push(`<div id="hook" ${clipAttrs(o.from, o.to, 2, "pn")} style="top:150px;height:300px">
  <div class="lockup"><div class="lk">${mark}<span class="qm">?</span></div></div>
  <div class="gname hd" style="font-size:${fit(o.name, 760, 66)}px">${esc(o.name)}</div>
  <div class="grole mono">${esc(o.role)}</div>
</div>`);
  tw("#hook .lk", { scale: 1.06 }, { scale: 1 }, 0, 12, "power2.out");
  popIn("#hook .qm", o.qAt, 6, 0.3, "back.out(2.2)");
  tw("#hook .lockup", { y: 0, scale: 1 }, { y: -92, scale: 0.46 }, o.guestAt, 10, "power3.inOut");
  tw("#hook .gname", { opacity: 0, y: 24 }, { opacity: 1, y: 0 }, o.guestAt + 3 / FPS, 7, "power3.out");
  tw("#hook .grole", { opacity: 0, y: 14 }, { opacity: 1, y: 0 }, o.guestAt + 7 / FPS, 7, "power3.out");
  tw("#hook", { opacity: 1, y: 0 }, { opacity: 0, y: -24 }, end - 6 / FPS, 6, "power2.in");
}

// ===== 2. the question: one-word card while the host asks it, then (after the bridge) the answer card =====
// Both cards sit in the same 226-470 slot as the ledger/tool/blur panels. No meta labels: the words carry the hook.
if (ov.question) {
  const o = ov.question, end = o.to / FPS, word = String(o.word || "QUESTION");
  const bigFs = fit(word + "?", 700, 170);
  const stampFs = fit(o.stamp, 640, 60);
  html.push(`<div id="question" ${clipAttrs(o.from, o.to, 3)}>
  <div class="pn qcard" style="top:226px;height:244px">
    <div class="qbig hd" style="font-size:${bigFs}px;top:${Math.round((244 - 0.95 * bigFs) / 2)}px">${esc(word)}<span class="sig">?</span></div>
  </div>
  <div class="pn acard" style="top:226px;height:244px">
    <div class="qbig hd" style="font-size:${bigFs}px"><span class="tx">${esc(word)}<span class="sig">?</span><i class="st"></i></span></div>
    <div class="pills">${(o.pills || []).map((p, i) => `<span class="pill mono p${i}">${esc(p.text)}</span>`).join("")}</div>
    <div class="stamp hd" style="font-size:${stampFs}px">${esc(o.stamp)}</div>
  </div>
</div>`);
  const a = o.from / FPS;
  tw("#question .qcard", { opacity: 0, scale: 1.14 }, { opacity: 1, scale: 1 }, a, 5, "power3.out");
  tw("#question .qcard", { opacity: 1, scale: 1 }, { opacity: 0, scale: 0.3 }, o.collapseAt, 8, "power3.in");
  tw("#question .acard", { opacity: 0, scale: 0.3 }, { opacity: 1, scale: 1 }, o.bridgeAt + 2 / FPS, 8, "power3.out");
  (o.pills || []).forEach((p, i) => tw(`#question .p${i}`, { opacity: 0, y: 16 }, { opacity: 1, y: 0 }, p.at, 6, "back.out(1.7)"));
  tw("#question .acard .st", { scaleX: 0 }, { scaleX: 1 }, o.answeredAt, 7, "power2.out");
  tw("#question .acard .qbig .tx", { opacity: 1 }, { opacity: 0.55 }, o.answeredAt, 7, "none");
  slam("#question .stamp", o.stampAt, -5);
  tw("#question .acard", { opacity: 1, y: 0 }, { opacity: 0, y: -24 }, end - 6 / FPS, 6, "power2.in");
}

// ===== 3. then / now ledger: old habits listed, struck through, replaced by one line =====
if (ov.ledger) {
  const o = ov.ledger, end = o.to / FPS, a = o.from / FPS;
  const aiFs = fit(o.ai, 770, 84);
  const hl = o.aiHighlight ? esc(o.aiHighlight) : "";
  const aiHtml = hl ? esc(o.ai).replace(hl, `<span class="sig">${hl}</span>`) : esc(o.ai);
  html.push(`<div id="ledger" ${clipAttrs(o.from, o.to, 4, "pn")} style="top:226px;height:244px">
  <div class="lhead"><span class="tag t1 mono">${esc(o.thenTag || "THEN")}</span><span class="tag t2 mono">${esc(o.nowTag || "NOW")}</span><span class="ll l1 mono">${esc(o.thenLabel || "")}</span><span class="ll l2 mono">${esc(o.nowLabel || "")}</span></div>
  <div class="rows">${o.rows.map((r, i) => `<div class="row r${i}" style="top:${76 + 54 * i}px"><span class="n mono">0${i + 1}</span><span class="tx">${esc(r.text)}<i class="st"></i></span></div>`).join("")}</div>
  <div class="ai hd" style="font-size:${aiFs}px">${aiHtml}</div>
</div>`);
  tw("#ledger", { opacity: 0, y: 20 }, { opacity: 1, y: 0 }, a, 7, "power3.out");
  o.rows.forEach((r, i) => tw(`#ledger .r${i}`, { opacity: 0, x: -36 }, { opacity: 1, x: 0 }, r.at, 6, "back.out(1.7)"));
  fadeOut("#ledger .t1", o.nowAt, 3); fadeOut("#ledger .l1", o.nowAt, 3);
  tw("#ledger .t2", { opacity: 0, scale: 1.5 }, { opacity: 1, scale: 1 }, o.nowAt + 2 / FPS, 5, "power3.out");
  tw("#ledger .l2", { opacity: 0, x: -16 }, { opacity: 1, x: 0 }, o.nowAt + 3 / FPS, 5, "power3.out");
  (o.strikes || []).forEach((t, i) => {
    tw(`#ledger .r${i} .st`, { scaleX: 0 }, { scaleX: 1 }, t, 7, "power2.out");
    tw(`#ledger .r${i} .tx`, { opacity: 1 }, { opacity: 0.45 }, t, 7, "none");
  });
  fadeOut("#ledger .rows", o.aiAt, 4);
  popIn("#ledger .ai", o.aiAt + 2 / FPS, 6, 0.75);
  tw("#ledger", { opacity: 1, y: 0 }, { opacity: 0, y: -20 }, end - 5 / FPS, 5, "power2.in");
}

// ===== 4. the tool: one hub wired to four tiles, a spoken command, then every tile lights up =====
if (ov.tool) {
  const o = ov.tool, end = o.to / FPS, a = o.from / FPS;
  const hub = { x: 170, y: 132, r: 60 }, tiles = [42, 96, 150, 204];
  const labels = tiles.map((_, i) => (o.tiles || [])[i] ?? "APP");
  const curves = tiles.map((cy) => `M${hub.x + hub.r} ${hub.y} C ${hub.x + 220} ${hub.y}, ${hub.x + 230} ${cy}, 600 ${cy}`);
  const stampFs = fit(o.stamp, 640, 50);
  html.push(`<div id="tool" ${clipAttrs(o.from, o.to, 5, "pn")} style="top:226px;height:244px">
  <svg class="wires" width="846" height="244" viewBox="0 0 846 244">
    ${[0, 1, 2].map((i) => `<circle class="rip rp${i}" cx="${hub.x}" cy="${hub.y}" r="${hub.r}" fill="none" stroke="#FF4D2E" stroke-width="3"/>`).join("")}
    ${curves.map((d, i) => `<path class="wire w${i}" d="${d}" pathLength="1" fill="none" stroke-width="3"/>`).join("")}
  </svg>
  <div class="hub"><span class="hd">${lines(o.hub || "ONE\nTOOL")}</span></div>
  <div class="talk mono">&ldquo;${esc(o.talk || "TALK TO IT")}&rdquo;</div>
  ${tiles.map((cy, i) => `<div class="tile mono t${i}" style="top:${cy - 20}px">${esc(labels[i])}</div>`).join("")}
  <div class="stamp hd" style="font-size:${stampFs}px">${esc(o.stamp)}</div>
</div>`);
  tw("#tool", { opacity: 0, y: 20 }, { opacity: 1, y: 0 }, a, 6, "power3.out");
  popIn("#tool .hub", o.hubAt, 7, 0.4);
  tiles.forEach((_, i) => {
    tw(`#tool .t${i}`, { opacity: 0, x: 30 }, { opacity: 1, x: 0 }, o.linkAt + (3 * i) / FPS, 6, "power3.out");
    tw(`#tool .w${i}`, { strokeDashoffset: 1 }, { strokeDashoffset: 0 }, o.linkAt + (2 + 3 * i) / FPS, 10, "power2.inOut");
  });
  [0, 1, 2].forEach((i) => tw(`#tool .rp${i}`, { opacity: 0.95, scale: 1, transformOrigin: "50% 50%" }, { opacity: 0, scale: 2.1, transformOrigin: "50% 50%" }, o.talkAt + (6 * i) / FPS, 16, "power1.out"));
  tw("#tool .talk", { opacity: 0, y: 10 }, { opacity: 1, y: 0 }, o.talkAt, 6, "back.out(1.7)");
  tiles.forEach((_, i) => {
    tw(`#tool .t${i}`, { backgroundColor: "rgba(255,77,46,0)", borderColor: "rgba(245,245,242,.55)" }, { backgroundColor: "rgba(255,77,46,1)", borderColor: "rgba(255,77,46,1)" }, o.buildAt + (4 * i) / FPS, 4, "none");
    tw(`#tool .w${i}`, { stroke: "rgba(245,245,242,0.7)" }, { stroke: "rgba(255,77,46,1)" }, o.buildAt + (4 * i) / FPS, 4, "none");
  });
  slam("#tool .stamp", o.stampAt, -6);
  tw("#tool", { opacity: 1, y: 0 }, { opacity: 0, y: -20 }, end - 5 / FPS, 5, "power2.in");
}

// ===== 5. blur: line 1 comes into focus (a focus-pull, best on a "blurred" line), then two short follow-ups =====
if (ov.blur) {
  const o = ov.blur, end = o.to / FPS, a = o.from / FPS;
  const L1 = String(o.line1 || "ROLES BLURRED."), L2 = String(o.line2 || ""), L3 = String(o.line3 || "");
  const f1 = fit(L1, 780, 74), f23 = Math.min(fit(L2 || "X", 780, 46), fit(L3 || "X", 780, 46));
  html.push(`<div id="blur" ${clipAttrs(o.from, o.to, 2, "pn")} style="top:226px;height:244px">
  <div class="kick mono bk">${esc(o.kicker || "")}</div>
  <div class="bl1 ghost hd" style="font-size:${f1}px">${esc(L1)}</div>
  <div class="bl1 main hd" style="font-size:${f1}px">${esc(L1)}</div>
  <div class="bl2 hd" style="font-size:${f23}px">${esc(L2)}</div>
  <div class="bl3 hd sig" style="font-size:${f23}px">${esc(L3)}</div>
</div>`);
  tw("#blur", { opacity: 0, y: 20 }, { opacity: 1, y: 0 }, a, 6, "power3.out");
  tw("#blur .bk", { opacity: 0, x: -16 }, { opacity: 1, x: 0 }, o.kickerAt, 6, "power3.out");
  tw("#blur .main", { opacity: 0, filter: "blur(18px)", scale: 1.08 }, { opacity: 1, filter: "blur(0px)", scale: 1 }, o.blurAt, 9, "power2.out");
  tw("#blur .ghost", { opacity: 0, x: 0 }, { opacity: 0.68, x: 16 }, o.blurAt + 4 / FPS, 10, "power2.out");
  tw("#blur .bl2", { opacity: 0, y: 14 }, { opacity: 1, y: 0 }, o.line2At, 6, "back.out(1.7)");
  tw("#blur .bl3", { opacity: 0, y: 14 }, { opacity: 1, y: 0 }, o.line3At, 6, "back.out(1.7)");
  tw("#blur", { opacity: 1, y: 0 }, { opacity: 0, y: -20 }, end - 5 / FPS, 5, "power2.in");
}

// ===== 6. meter: the thing you expected to go down did not; the other thing went up =====
if (ov.meter) {
  const o = ov.meter, end = o.to / FPS, a = o.from / FPS;
  const down = String(o.down || "HOURS"), up = String(o.up || "OUTPUT");
  const fA = Math.min(fit(down, 640, 80), fit(up, 640, 80));
  html.push(`<div id="meter" ${clipAttrs(o.from, o.to, 2, "pn")} style="top:150px;height:320px">
  <div class="kick mono mk">${esc(o.kicker || "INSTEAD OF…")}</div>
  <div class="mrow ma" style="top:66px"><span class="tx hd" style="font-size:${fA}px">${esc(down)}<i class="st"></i></span><span class="icw"><span class="ic down">${ARROW("down", "rgba(245,245,242,.7)")}</span><span class="ic xx">${XMARK("#FF4D2E")}</span></span></div>
  <div class="mrow mb" style="top:158px"><span class="tx hd" style="font-size:${fA}px">${esc(up)}</span><span class="icw"><span class="ic up">${ARROW("up", "#FF4D2E")}</span></span></div>
  <div class="bar"><i class="fill"></i></div>
  <div class="mono hi">${esc(o.label || "HIGHER")}</div>
</div>`);
  tw("#meter", { opacity: 0, y: 20 }, { opacity: 1, y: 0 }, a, 6, "power3.out");
  tw("#meter .mk", { opacity: 0, x: -16 }, { opacity: 1, x: 0 }, o.insteadAt, 6, "power3.out");
  tw("#meter .ma", { opacity: 0, x: -30 }, { opacity: 1, x: 0 }, o.downAt, 6, "back.out(1.7)");
  tw("#meter .down", { y: -18 }, { y: 0 }, o.downAt + 3 / FPS, 8, "bounce.out");
  tw("#meter .ma .st", { scaleX: 0 }, { scaleX: 1 }, o.xAt, 7, "power2.out");
  tw("#meter .ma .tx", { opacity: 1 }, { opacity: 0.45 }, o.xAt, 7, "none");
  fadeOut("#meter .down", o.xAt, 3);
  popIn("#meter .xx", o.xAt + 2 / FPS, 6, 0.3, "back.out(2.4)");
  tw("#meter .mb", { opacity: 0, x: -30 }, { opacity: 1, x: 0 }, o.upAt, 6, "back.out(1.7)");
  tw("#meter .up", { y: 18 }, { y: 0 }, o.upAt + 3 / FPS, 8, "power3.out");
  tw("#meter .bar", { opacity: 0 }, { opacity: 1 }, o.upAt, 5, "none");
  tw("#meter .fill", { scaleX: 0 }, { scaleX: 1 }, o.upAt + 2 / FPS, Math.max(1, Math.round((o.labelEnd - o.upAt) * FPS)), "power1.in");
  tw("#meter .hi", { opacity: 0, y: 10 }, { opacity: 1, y: 0 }, o.labelAt, 5, "back.out(1.7)");
  tw("#meter", { opacity: 1, y: 0 }, { opacity: 0, y: -20 }, end - 5 / FPS, 5, "power2.in");
}

// ===== 7. payoff: a dim setup line, a mono bridge, then two big words =====
if (ov.payoff) {
  const o = ov.payoff, a = o.from / FPS;
  const fB = Math.min(fit(o.big1, 780, 80), fit(o.big2, 780, 80));
  html.push(`<div id="payoff" ${clipAttrs(o.from, o.to, 3, "pn")} style="top:150px;height:320px">
  <div class="ptop">${esc(o.top || "")}</div>
  <div class="pmid mono">${esc(o.mid || "")}</div>
  <div class="pm hd" style="font-size:${fB}px;top:132px">${esc(o.big1)}</div>
  <div class="pf hd sig" style="font-size:${fB}px;top:${132 + fB + 6}px">${esc(o.big2)}</div>
</div>`);
  tw("#payoff", { opacity: 0, y: 20 }, { opacity: 1, y: 0 }, a, 6, "power3.out");
  tw("#payoff .ptop", { opacity: 0, y: 12 }, { opacity: 1, y: 0 }, o.topAt, 6, "power3.out");
  tw("#payoff .pmid", { opacity: 0, x: -16 }, { opacity: 1, x: 0 }, o.midAt, 6, "power3.out");
  popIn("#payoff .pm", o.big1At, 6, 0.7);
  tw("#payoff .pf", { opacity: 0 }, { opacity: 1 }, o.big2At, 3, "none");
  tw("#payoff .pf", { x: 90, skewX: -14 }, { x: 0, skewX: 0 }, o.big2At, 6, "power4.out");
}

// ===== 8. end slate (full frame, over the held last frame) =====
if (ov.slate) {
  const o = ov.slate, a = o.from / FPS, dur = (o.to - o.from) / FPS;
  const fS = Math.min(fit(o.big1, 860, 150), fit(o.big2, 860, 150));
  html.push(`<div id="slate" ${clipAttrs(o.from, o.to, 4)}>
  <div class="sbg"></div>
  <div class="sin">
    <div class="s1 hd" style="font-size:${fS}px">${esc(o.big1)}</div>
    <div class="s2 hd sig" style="font-size:${fS}px">${esc(o.big2)}</div>
    <i class="srule"></i>
    <div class="s3 mono">${esc(o.small)}</div>
  </div>
</div>`);
  tw("#slate .sbg", { opacity: 0 }, { opacity: 1 }, a, 3, "none");
  tw("#slate .s1", { opacity: 0, y: 40 }, { opacity: 1, y: 0 }, a, 5, "power3.out");
  tw("#slate .s2", { opacity: 0, y: 40 }, { opacity: 1, y: 0 }, a + 3 / FPS, 5, "power3.out");
  tw("#slate .srule", { scaleX: 0 }, { scaleX: 1 }, a + 6 / FPS, 8, "power2.out");
  tw("#slate .s3", { opacity: 0 }, { opacity: 1 }, a + 8 / FPS, 6, "none");
  tw("#slate .sin", { scale: 1 }, { scale: 1.035 }, a, Math.round(dur * FPS), "none");
}

const FONTS = `
@font-face { font-family: 'RkSans'; src: url('assets/fonts/inter-latin-900-normal.woff2') format('woff2'); font-weight: 900; }
@font-face { font-family: 'RkSans'; src: url('assets/fonts/inter-latin-800-normal.woff2') format('woff2'); font-weight: 800; }
@font-face { font-family: 'RkMono'; src: url('assets/fonts/SpaceMono-Regular.ttf') format('truetype'); }`;
const OV_CSS = `
#overlays { position: absolute; inset: 0; --ink: rgba(11,11,12,.86); --paper: #F5F5F2; --dim: rgba(245,245,242,.62); --sig: #FF4D2E; }
.pn { position: absolute; left: 72px; width: 846px; background: var(--ink); border: 1px solid rgba(255,255,255,.2); border-radius: 6px;
  overflow: hidden; color: var(--paper); box-shadow: 0 18px 44px rgba(0,0,0,.35); }
.hd { font-family: 'RkSans'; font-weight: 900; text-transform: uppercase; letter-spacing: -0.025em; line-height: .95; color: var(--paper); white-space: nowrap; }
.mono { font-family: 'RkMono'; text-transform: uppercase; letter-spacing: .08em; font-size: 26px; line-height: 1; color: var(--dim); white-space: nowrap; }
.sig { color: var(--sig) !important; }
.kick { position: absolute; left: 30px; top: 26px; display: flex; align-items: center; gap: 12px; }
.st { position: absolute; left: -6px; right: -6px; top: 50%; height: 7px; margin-top: -4px; background: var(--sig); transform: scaleX(0); transform-origin: 0 50%; }
.tx { position: relative; display: inline-block; }
.stamp { position: absolute; left: 50%; top: 50%; padding: 12px 24px 14px; opacity: 0; }

/* hook */
#hook { opacity: 1; }
#hook .lockup { position: absolute; left: 0; top: 0; width: 846px; height: 300px; display: flex; align-items: center; justify-content: center; transform-origin: 50% 50%; }
#hook .lk { display: flex; align-items: flex-end; gap: 16px; color: var(--paper); transform-origin: 50% 50%; }
#hook .logo { display: block; }
#hook .cname { line-height: .78; }
#hook .qm { font-family: 'RkSans'; font-weight: 900; font-size: 150px; line-height: .78; color: var(--sig); opacity: 0; display: inline-block; transform-origin: 50% 80%; }
#hook .gname { position: absolute; left: 0; width: 846px; top: 128px; text-align: center; opacity: 0; }
#hook .grole { position: absolute; left: 0; width: 846px; top: 222px; text-align: center; opacity: 0; font-size: 25px; }

/* question */
#question { position: absolute; inset: 0; }
#question .qcard { opacity: 0; transform-origin: 50% 50%; }
#question .qbig { position: absolute; left: 30px; top: 26px; }
#question .qcard .qbig { left: 0; width: 846px; text-align: center; }
#question .acard { opacity: 0; transform-origin: 0 0; }
#question .acard .st { height: 14px; margin-top: -9px; }
#question .pills { position: absolute; left: 30px; bottom: 28px; display: flex; gap: 14px; }
#question .pill { padding: 9px 16px 8px; border: 1.5px solid rgba(245,245,242,.5); border-radius: 999px; font-size: 23px; color: var(--paper); opacity: 0; }
#question .stamp { background: var(--sig); color: #fff; top: 46%; box-shadow: 0 12px 30px rgba(0,0,0,.45); }

/* ledger */
#ledger { opacity: 0; }
#ledger .lhead { position: absolute; left: 30px; top: 22px; height: 36px; width: 780px; }
#ledger .tag { position: absolute; left: 0; top: 0; padding: 5px 12px 4px; border: 1.5px solid rgba(245,245,242,.6); color: var(--paper); font-size: 24px; }
#ledger .t2 { background: var(--sig); border-color: var(--sig); color: #fff; opacity: 0; }
#ledger .ll { position: absolute; left: 104px; top: 6px; font-size: 24px; }
#ledger .l2 { opacity: 0; color: var(--paper); }
#ledger .row { position: absolute; left: 30px; height: 50px; display: flex; align-items: center; gap: 18px; opacity: 0; }
#ledger .row .n { font-size: 22px; }
#ledger .row .tx { font-family: 'RkSans'; font-weight: 800; font-size: 40px; letter-spacing: -0.02em; color: var(--paper); white-space: nowrap; }
#ledger .ai { position: absolute; left: 0; width: 846px; top: 100px; text-align: center; opacity: 0; }

/* tool */
#tool { opacity: 0; }
#tool .wires { position: absolute; left: 0; top: 0; }
#tool .wire { stroke: rgba(245,245,242,.7); stroke-dasharray: 1; stroke-dashoffset: 1; }
#tool .rip { opacity: 0; transform-box: fill-box; transform-origin: 50% 50%; }
#tool .hub { position: absolute; left: 110px; top: 72px; width: 120px; height: 120px; border-radius: 50%; border: 3px solid var(--sig);
  background: rgba(255,77,46,.14); display: flex; align-items: center; justify-content: center; text-align: center; opacity: 0; }
#tool .hub .hd { font-size: 27px; line-height: 1; }
#tool .talk { position: absolute; left: 318px; top: 112px; padding: 6px 14px 5px; background: #141416; border: 1.5px solid rgba(245,245,242,.28); border-radius: 4px; color: var(--paper); font-size: 24px; opacity: 0; z-index: 2; }
#tool .tile { position: absolute; left: 600px; width: 200px; height: 40px; border: 1.5px solid rgba(245,245,242,.55); border-radius: 6px;
  display: flex; align-items: center; justify-content: center; color: var(--paper); font-size: 22px; opacity: 0; background: rgba(255,77,46,0); }
#tool .stamp { color: var(--sig); border: 4px solid var(--sig); background: rgba(11,11,12,.94); }

/* blur */
#blur { opacity: 0; }
#blur .bk { opacity: 0; }
#blur .bl1 { position: absolute; left: 30px; top: 60px; opacity: 0; }
#blur .ghost { color: var(--sig); filter: blur(5px); }
#blur .bl2 { position: absolute; left: 30px; top: 142px; opacity: 0; }
#blur .bl3 { position: absolute; left: 30px; top: 190px; opacity: 0; }

/* meter */
#meter { opacity: 0; }
#meter .mk { opacity: 0; }
#meter .mrow { position: absolute; left: 30px; height: 86px; display: flex; align-items: center; gap: 22px; opacity: 0; }
#meter .icw { position: relative; display: inline-block; width: 58px; height: 58px; }
#meter .ic { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; }
#meter .xx { opacity: 0; }
#meter .ma .st { height: 10px; margin-top: -6px; }
#meter .bar { position: absolute; left: 30px; top: 258px; width: 640px; height: 22px; background: rgba(245,245,242,.14); opacity: 0; }
#meter .fill { position: absolute; inset: 0; background: var(--sig); transform: scaleX(0); transform-origin: 0 50%; }
#meter .hi { position: absolute; left: 690px; top: 256px; color: var(--sig); font-size: 26px; opacity: 0; }

/* payoff */
#payoff { opacity: 0; }
#payoff .ptop { position: absolute; left: 30px; top: 24px; font-family: 'RkSans'; font-weight: 800; font-size: 46px; letter-spacing: -0.02em; color: var(--dim); opacity: 0; white-space: nowrap; }
#payoff .pmid { position: absolute; left: 30px; top: 88px; font-size: 25px; color: var(--paper); opacity: 0; }
#payoff .pm, #payoff .pf { position: absolute; left: 30px; opacity: 0; transform-origin: 0 50%; }

/* slate */
#slate { position: absolute; inset: 0; }
#slate .sbg { position: absolute; inset: 0; background: #0B0B0C; opacity: 0; }
#slate .sin { position: absolute; left: 0; width: ${W}px; top: 700px; display: flex; flex-direction: column; align-items: center; transform-origin: 50% 40%; }
#slate .s1, #slate .s2 { opacity: 0; line-height: 1; }
#slate .s2 { margin-top: 8px; }
#slate .srule { display: block; width: 120px; height: 6px; background: var(--sig); margin: 40px 0 30px; transform: scaleX(0); }
#slate .s3 { font-size: 28px; opacity: 0; }
`;

const overlaysScene = `<template>
  <style>${FONTS}
${OV_CSS}
  </style>
  <div id="overlays" data-composition-id="overlays" data-width="${W}" data-height="${H}" data-layout-allow-overlap>
${html.join("\n")}
  </div>
  <script>
    const tl = gsap.timeline({ paused: true });
    ${J.join("\n    ")}
    window.__timelines["overlays"] = tl;
  </script>
</template>
`;

// ---------- captions: one word (or two) per page, Inter 900, white with a dark stroke; keyword pages get a red box ----------
const KW = new Set((cfg.keywords || []).map((k) => k.toLowerCase()));
const slateFrom = ov.slate ? ov.slate.from : NF;
const capHtml = [], capJs = [];
for (const c of edit.captions) {
  const to = Math.min(c.to, slateFrom);
  if (to <= c.from) continue;
  const text = c.text.replace(/[,.]+$/g, "").replace(/,(\s)/g, "$1");
  const kw = c.words.some((w) => KW.has(w.w.toLowerCase().replace(/[^a-z']/g, "")));
  const id = `cap-${c.id}`;
  capHtml.push(`    <div id="${id}" ${clipAttrs(c.from, to, 1, kw ? "cap kw" : "cap")} style="top:${c.y - 90}px"><span class="w" data-t="${esc(text)}" style="--fs:${c.fs}px">${esc(text)}</span></div>`);
  const at = (c.from - 1) / FPS;
  capJs.push(at >= 0
    ? `tl.fromTo("#${id} .w",{opacity:0,y:14,scale:.86},{opacity:1,y:0,scale:1,duration:${(4 / FPS).toFixed(4)},ease:"power2.out"},${at.toFixed(4)});`
    : `tl.fromTo("#${id} .w",{opacity:.4375,y:7.875,scale:.92125},{opacity:1,y:0,scale:1,duration:${(3 / FPS).toFixed(4)},ease:"power2.out"},0);`);
}
const CAP_CSS = `
#captions { position: absolute; inset: 0; }
.cap { position: absolute; left: 100px; width: 820px; height: 180px; display: flex; align-items: center; justify-content: center; pointer-events: none; }
.cap .w { position: relative; display: inline-block; white-space: nowrap; color: #fff; transform-origin: 50% 60%; isolation: isolate;
  font-family: 'RkSans'; font-weight: 900; font-size: var(--fs, 120px); line-height: 1; letter-spacing: -0.03em; }
.cap .w::before { content: attr(data-t); position: absolute; left: 0; top: 0; z-index: -1; white-space: nowrap;
  color: rgba(12,10,9,.86); -webkit-text-stroke: 6px rgba(12,10,9,.86);
  text-shadow: 0 0 3px rgba(0,0,0,.55), 0 5px 12px rgba(0,0,0,.55), 0 14px 34px rgba(0,0,0,.45); }
.cap.kw .w::after { content: ""; position: absolute; left: -0.14em; right: -0.14em; top: 0.02em; bottom: -0.04em; z-index: -2;
  background: #FF4D2E; border-radius: 8px; transform: rotate(-1.5deg); box-shadow: 0 10px 26px rgba(0,0,0,.35); }
`;
const captionsScene = `<template>
  <style>${FONTS}
${CAP_CSS}
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
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="${DUR.toFixed(4)}" data-width="${W}" data-height="${H}">
      <video id="src" class="clip" src="assets/${edit.source}" data-start="0" data-duration="${DUR.toFixed(4)}" data-track-index="0" playsinline data-has-audio="true"></video>
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
fs.writeFileSync("compositions/overlays.html", overlaysScene);
fs.writeFileSync("compositions/captions.html", captionsScene);
fs.writeFileSync("index.html", index);
console.log(`captions ${capHtml.length}, overlay tweens ${J.length}, duration ${DUR.toFixed(4)}s, logo ${LOGO ? LOGO.paths.length + " path(s)" : "placeholder ring + company name"}`);
