import React, { useEffect, useState } from "react";
import {
  AbsoluteFill,
  Easing,
  interpolateColors,
  OffthreadVideo,
  Sequence,
  continueRender,
  delayRender,
  interpolate,
  staticFile,
  useCurrentFrame,
} from "remotion";
import "./style.css";
import edit from "./edit.json";

// ---- easing shared with the HyperFrames build (GSAP names in comments) ----
const backOut = Easing.out(Easing.back(1.7)); // back.out(1.7)
const quadOut = Easing.out(Easing.quad); // power2.out
const quadIn = Easing.quad; // power2.in
const tw = (f: number, start: number, dur: number, a: number, b: number, ease: (t: number) => number) =>
  interpolate(f, [start, start + dur], [a, b], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: ease });

// pop in over 6 frames, out over the last 5 (frame is local to the Sequence)
const pop = (f: number, len: number, delay = 0, fromScale = 0.7) => {
  const inS = tw(f, delay, 6, fromScale, 1, backOut);
  const inO = tw(f, delay, 6, 0, 1, quadOut);
  const outS = tw(f, len - 5, 5, 1, 0.85, quadIn);
  const outO = tw(f, len - 5, 5, 1, 0, quadIn);
  return { scale: f >= len - 5 ? outS : inS, opacity: f >= len - 5 ? outO : inO };
};

const FONTS: [string, string, Record<string, string>][] = [
  ["ReelCapSerif", "fonts/dm-serif-display-latin-400-normal.woff2", {}],
  ["ReelCapSerif", "fonts/dm-serif-display-latin-400-italic.woff2", { style: "italic" }],
  ["ReelSerif", "fonts/instrument-serif-latin-400-normal.woff2", {}],
  ["ReelSerif", "fonts/instrument-serif-latin-400-italic.woff2", { style: "italic" }],
  ["ReelSans", "fonts/inter-latin-900-normal.woff2", { weight: "900" }],
  ["ReelSans", "fonts/inter-latin-800-normal.woff2", { weight: "800" }],
  ["ReelCond", "fonts/Anton.ttf", {}],
];
const useFonts = () => {
  const [handle] = useState(() => delayRender("fonts"));
  useEffect(() => {
    Promise.all(
      FONTS.map(([fam, file, desc]) => {
        const ff = new FontFace(fam, `url(${staticFile(file)})`, desc);
        return ff.load().then((l) => document.fonts.add(l));
      }),
    ).then(() => continueRender(handle));
  }, [handle]);
};

type Cap = { id: string; text: string; from: number; to: number; style: string; y: number; fs?: number };
const Caption: React.FC<{ c: Cap }> = ({ c }) => {
  const f = useCurrentFrame();
  // 4-frame pop that starts one frame early, so the word is already visible on its first frame
  const s = tw(f, -1, 4, 0.86, 1, quadOut);
  const y = tw(f, -1, 4, 14, 0, quadOut);
  const o = tw(f, -1, 4, 0, 1, quadOut);
  return (
    <div className={`cap ${c.style === "sans" ? "sans" : ""}`} style={{ top: c.y - 90 }}>
      {/* data-t feeds the stroked ::before copy (dark outline behind the fill); --fs is the per-page fitted size */}
      <span className="w" data-t={c.text}
        style={{ opacity: o, transform: `translateY(${y}px) scale(${s})`, ...(c.fs ? { ["--fs" as string]: `${c.fs}px` } : {}) }}>{c.text}</span>
    </div>
  );
};

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Ov = any;
const TitleCard: React.FC<{ o: Ov; len: number }> = ({ o, len }) => {
  const p = pop(useCurrentFrame(), len);
  return (
    <div className="tcard">
      <div className="box" style={{ opacity: p.opacity, transform: `scale(${p.scale})` }}>
        <div className="t">{o.title}</div>
        <div className="s">{o.subtitle}</div>
      </div>
    </div>
  );
};

// CTA offer text: one line per "\n"; ALL-CAPS words (2+ letters, e.g. "FREE") get the red box. Same as offerHtml in gen.mjs.
const EMW = /^([^A-Za-z]*)([A-Z]{2,})([^A-Za-z]*)$/;
const offerParts = (s: string) => String(s).split("\n").map((ln, li) => (
  <div className="ln" key={li}>
    {ln.split(/(\s+)/).map((w, wi) => {
      const m = w.match(EMW);
      return m ? <React.Fragment key={wi}>{m[1]}<span className="em">{m[2]}</span>{m[3]}</React.Fragment> : w;
    })}
  </div>
));

const Cta: React.FC<{ o: Ov; len: number }> = ({ o, len }) => {
  const f = useCurrentFrame();
  const k = pop(f, len, 0, 0.6);
  const b = pop(f, len, 3, 0.6);
  // brand offer line (sponsored reels): pops at offerF on a paper card under the chin (see .ctaoffer in style.css)
  const od = Math.max(0, (o.offerF ?? o.from + 10) - o.from);
  const p = pop(f, len, od, 0.8);
  const rot = tw(f, od, 6, -6, -1.5, backOut);
  return (
    <>
      <div className="cta">
        <div className="k" style={{ opacity: k.opacity, transform: `scale(${k.scale})` }}>{o.kicker}</div>
        <div className="box" style={{ opacity: b.opacity, transform: `scale(${b.scale})` }}>
          <div className="word">{o.word}</div>
        </div>
      </div>
      {o.offer ? (
        <div className="ctaoffer" style={{ top: o.offerY ?? 1092 }}>
          <div className="card" style={{ fontSize: o.offerFs ?? 44, opacity: p.opacity, transform: `rotate(${rot}deg) scale(${p.scale})` }}>
            {offerParts(o.offer)}
          </div>
        </div>
      ) : null}
    </>
  );
};

const FloatCard: React.FC<{ o: Ov; len: number }> = ({ o, len }) => {
  const f = useCurrentFrame();
  const p = pop(f, len);
  const rot = tw(f, 0, 6, o.rotate - 8, o.rotate, backOut);
  return (
    <div className={`fcard ${o.side}`} style={o.width ? { width: o.width } : undefined}>
      <div className="card" style={{ opacity: p.opacity, transform: `rotate(${rot}deg) scale(${p.scale})` }}>
        {o.flag ? (
          <>
            <div className="kick">{o.label}</div>
            <div className="row">
              {o.flag === "us-es" ? (<><div className="flag-us" /><div className="arrow">→</div></>) : null}
              <div className="flag-es" /><div className="lang">{o.small}</div>
            </div>
          </>
        ) : (
          <>
            {o.kicker ? <div className="kick">{o.kicker}</div> : null}
            {o.big ? <div className="big">{o.big}</div> : null}
            <div className="label">{o.label}</div>
            {o.small ? <div className="small">{o.small}</div> : null}
          </>
        )}
      </div>
    </div>
  );
};

// numbered list that builds as the speaker says each step; the current number is red, earlier ones dim (same as gen.mjs)
const Steps: React.FC<{ o: Ov; len: number }> = ({ o, len }) => {
  const f = useCurrentFrame();
  const p = pop(f, len);
  const r0 = o.rotate ?? -1.5;
  const rot = tw(f, 0, 6, r0 - 6, r0, backOut);
  return (
    <div className="steps">
      <div className="card" style={{ opacity: p.opacity, transform: `rotate(${rot}deg) scale(${p.scale})` }}>
        <div className="kick">{o.kicker}</div>
        {o.items.map((it: Ov, i: number) => {
          const at = Math.max(it.f, o.from) - o.from;
          const nx = o.items[i + 1] ? o.items[i + 1].f - o.from : Infinity;
          const on = interpolateColors(tw(f, at, 3, 0, 1, (t) => t), [0, 1], ["#d9d3cb", "#B23A2E"]);
          const bg = f >= nx ? interpolateColors(tw(f, nx, 3, 0, 1, (t) => t), [0, 1], ["#B23A2E", "#d9d3cb"]) : on;
          const txO = f >= nx ? tw(f, nx, 3, 1, 0.5, (t) => t) : 1;
          return (
            <div className="it" key={i} style={{ opacity: tw(f, at, 6, 0, 1, backOut), transform: `translateX(${tw(f, at, 6, -40, 0, backOut)}px)` }}>
              <div className="n" style={{ background: bg }}>{i + 1}</div>
              <div className="tx" style={{ opacity: txO }}>{it.text}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

// old way vs new way: the old pane pops first, the new pane at newF
const Split: React.FC<{ o: Ov; len: number }> = ({ o, len }) => {
  const f = useCurrentFrame();
  const d = o.newF - o.from;
  const a = pop(f, len), b = pop(f, len, d);
  const ra = tw(f, 0, 6, -8, -2, backOut), rb = tw(f, d, 6, 8, 2, backOut);
  return (
    <div className="split">
      <div className="pane old" style={{ opacity: a.opacity, transform: `rotate(${ra}deg) scale(${a.scale})` }}>
        <div className="lab">{o.oldLabel || "Old way"}</div><div className="tx">{o.old}</div>
      </div>
      <div className="pane new" style={{ opacity: b.opacity, transform: `rotate(${rb}deg) scale(${b.scale})` }}>
        <div className="lab">{o.newLabel || "New way"}</div><div className="tx">{o.new}</div>
      </div>
    </div>
  );
};

// generic text chips (tool names as words, never logos)
const ChipCard: React.FC<{ o: Ov; len: number }> = ({ o, len }) => {
  const f = useCurrentFrame();
  const p = pop(f, len);
  const r0 = o.rotate ?? 2;
  const rot = tw(f, 0, 6, r0 + 6, r0, backOut);
  return (
    <div className={`chipcard ${o.side || "center"}`}>
      <div className="card" style={{ opacity: p.opacity, transform: `rotate(${rot}deg) scale(${p.scale})` }}>
        <div className="kick">{o.kicker}</div>
        <div className="chips">
          {o.chips.map((c: string, i: number) => (
            <div className="chip" key={c} style={{ opacity: tw(f, 4 + 4 * i, 6, 0, 1, quadOut), transform: `scale(${tw(f, 4 + 4 * i, 6, 0.6, 1, backOut)})` }}>{c}</div>
          ))}
        </div>
        {o.small ? <div className="small">{o.small}</div> : null}
      </div>
    </div>
  );
};

export const WAVE = Array.from({ length: 36 }, (_, i) =>
  Math.round(26 + 160 * Math.abs(0.6 * Math.sin(i * 0.9) + 0.4 * Math.sin(i * 0.37 + 1))),
);
const Mag = () => (
  <svg width="52" height="52" viewBox="0 0 24 24"><circle cx="10" cy="10" r="6.5" fill="none" stroke="#5f6368" strokeWidth="2.4" /><path d="M15 15l5.5 5.5" stroke="#5f6368" strokeWidth="2.6" strokeLinecap="round" /></svg>
);
const Mic = () => (
  <svg width="44" height="52" viewBox="0 0 20 24"><rect x="6" y="2" width="8" height="13" rx="4" fill="#4285F4" /><path d="M3 11a7 7 0 0 0 14 0" fill="none" stroke="#34A853" strokeWidth="2.2" /><path d="M10 18v4" stroke="#FBBC05" strokeWidth="2.2" /></svg>
);
const Lens = () => (
  <svg width="50" height="50" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="5" fill="none" stroke="#4285F4" strokeWidth="2.2" /><circle cx="12" cy="12" r="3.6" fill="#EA4335" /></svg>
);

const Takeover: React.FC<{ o: Ov; len: number }> = ({ o, len }) => {
  const f = useCurrentFrame();
  const bgO = tw(f, 0, 3, 0, 1, quadOut);
  const allO = tw(f, len - 4, 4, 1, 0, quadIn);
  const pillY = tw(f, 2, 6, -60, 0, backOut);
  const pillO = tw(f, 2, 6, 0, 1, quadOut);
  const reveal = tw(f, 6, 14, 100, 0, (t) => t);
  const sheetS = tw(f, 8, 6, 0.88, 1, backOut);
  const sheetY = tw(f, 8, 6, 60, 0, backOut);
  const sheetO = tw(f, 8, 6, 0, 1, quadOut);
  return (
    <div className="tk" style={{ opacity: allO }}>
      <div className="bgk" style={{ opacity: bgO }} />
      <div className="pill" style={{ opacity: pillO, transform: `translateY(${pillY}px)` }}>
        <Mag />
        <div className="q"><span style={{ clipPath: `inset(0 ${reveal}% 0 0)` }}>{o.query}</span></div>
        <Mic />
        <Lens />
      </div>
      <div className="sheet" style={{ opacity: sheetO, transform: `translateY(${sheetY}px) scale(${sheetS})` }}>
        <div className="ct">{o.cardTitle}</div>
        <div className="cs">{o.cardSub}</div>
        <div className="wave">
          {WAVE.map((h, i) => (
            <i key={i} style={{ height: h, transform: `scaleY(${tw(f, 12 + i * 0.5, 8, 0.08, 1, quadOut)})` }} />
          ))}
        </div>
        <div className="chips">{o.chips.map((c: string) => <div className="chip" key={c}>{c}</div>)}</div>
      </div>
    </div>
  );
};

const OVERLAY: Record<string, React.FC<{ o: Ov; len: number }>> = {
  titleCard: TitleCard, cta: Cta, floatCard: FloatCard, takeover: Takeover, steps: Steps, split: Split, chipCard: ChipCard,
};

export const Reel: React.FC = () => {
  useFonts();
  return (
    <AbsoluteFill>
      <div id="stage">
        <OffthreadVideo className="bg" src={staticFile(edit.source)} />
        {edit.overlays.map((o: Ov) => {
          const C = OVERLAY[o.type];
          if (!C) return null; // HyperFrames-only types (promptDemo) are skipped here; render demo reels with HyperFrames
          const len = o.to - o.from;
          return (
            <Sequence key={o.id} from={o.from} durationInFrames={len} layout="none" name={o.id}>
              <C o={o} len={len} />
            </Sequence>
          );
        })}
        {(edit.captions as Cap[]).map((c) => (
          <Sequence key={c.id} from={c.from} durationInFrames={c.to - c.from} layout="none" name={c.text}>
            <Caption c={c} />
          </Sequence>
        ))}
      </div>
    </AbsoluteFill>
  );
};
