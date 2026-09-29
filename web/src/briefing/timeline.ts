/**
 * The Briefing tab's timeline chart (docs/data-contract.md, "Forecast timeline and waves over
 * time" → Client). One hourly column per entry of `timeline.hours`, rows stacked under a
 * shared time axis, a sticky label column on the left, and a detail block for the hour the
 * forecast clock points at.
 *
 * Nothing here depends on hue: scores carry their word, patterns and lightness mark the
 * wave limits and the airport's bad hours, and every number sits in its hour's column.
 */

import "../styles/timeline.css";
import { clock, hourIndex, type ClockState } from "../state/clock";
import { el } from "../ui/format";
import type { WaveLimits } from "../waves/ramp";
import { SCORE_RANK, SCORE_WORD, formatCeiling, formatWind, limitingLabel, shortWeekday } from "./labels";
import type {
  BriefingRegion,
  LimitingFactor,
  Score,
  Timeline,
  TimelineHomeWater,
  TimelineHour,
  Wind,
} from "./types";

const SVG_NS = "http://www.w3.org/2000/svg";
/** Column width. 30 px keeps two-digit numbers legible and puts ~10 hours on a phone. */
const COL = 30;
/** Numbers and arrows every this many hours, on even local hours. */
const LABEL_EVERY = 2;
/** Regions shown before the list folds; `.tl-regions.is-clipped` in timeline.css agrees. */
const REGIONS_SHOWN = 6;

export interface TimelineDeps {
  airportId: string;
  /** Today's region list from `home_water`, only for each region's lat/lon. */
  homeRegions: BriefingRegion[];
  limits: WaveLimits;
  onSelectRegion: (id: number, lat: number, lon: number) => void;
  /** Select the home water and get the sheet out of the way so the map shows. */
  onShowWaves: (id: number) => void;
}

// -- the one-line answer -----------------------------------------------------

export interface TimelineLead {
  score: Score | null;
  /** "Favorable Tue 10:00–14:00" */
  headline: string;
  /** "gusts after", "dark after", "gusts" (no window), or null. */
  after: string | null;
}

/** Worse of the airport and home water scores for hour `i`, with what limits it. */
function combined(tl: Timeline, i: number): { score: Score; limiting: LimitingFactor | null } {
  const h = tl.hours[i]!;
  const hw = tl.home_water;
  const ws = hw?.score[i];
  if (hw && ws && SCORE_RANK[ws] > SCORE_RANK[h.score]) {
    return { score: ws, limiting: hw.limiting[i] ?? null };
  }
  return { score: h.score, limiting: h.limiting };
}

function hhmm(t: string): string {
  return t.slice(11, 16);
}

function weekday(t: string): string {
  return shortWeekday(t.slice(0, 10));
}

/**
 * The answer the chip, the peek row and the card all show, from `timeline.windows`: the next
 * window that has not ended, else the best level any future daylight hour reaches.
 */
export function timelineLead(tl: Timeline, now: Date = new Date()): TimelineLead {
  const t = now.getTime();
  const w = tl.windows.find((win) => Date.parse(win.end) > t);
  if (w) {
    const started = Date.parse(w.start) <= t;
    const when = started ? `now–${hhmm(w.end)}` : `${weekday(w.start)} ${hhmm(w.start)}–${hhmm(w.end)}`;
    let after: string | null = null;
    if (w.limiting_after) {
      after = `${limitingLabel(w.limiting_after)} after`;
    } else {
      const endIdx = tl.hours.findIndex((h) => Date.parse(h.t) === Date.parse(w.end));
      if (endIdx >= 0 && !tl.hours[endIdx]!.daylight) after = "dark after";
    }
    return { score: w.score, headline: `${SCORE_WORD[w.score]} ${when}`, after };
  }

  let best: { score: Score; limiting: LimitingFactor | null } | null = null;
  tl.hours.forEach((h, i) => {
    if (!h.daylight || Date.parse(h.t) + 3_600_000 <= t) return;
    const c = combined(tl, i);
    if (!best || SCORE_RANK[c.score] < SCORE_RANK[best.score]) best = c;
  });
  if (!best) return { score: null, headline: "No daylight hours left in the forecast", after: null };
  const b = best as { score: Score; limiting: LimitingFactor | null };
  return {
    score: b.score,
    headline: `${SCORE_WORD[b.score]} at best · no window`,
    after: limitingLabel(b.limiting),
  };
}

// -- chart -------------------------------------------------------------------

interface Row {
  key: string;
  y: number;
  h: number;
}

/** Only one chart exists at a time; a rebuild drops the previous one's clock listener. */
let unsubscribe: (() => void) | null = null;
/** Scroll position kept across rebuilds (a refresh must not throw the pilot back to now). */
let savedScroll: number | null = null;

export function renderTimeline(tl: Timeline, deps: TimelineDeps): HTMLElement {
  unsubscribe?.();
  unsubscribe = null;

  const hours = tl.hours;
  const n = hours.length;
  const hw = tl.home_water;
  const axis = hours.map((h) => h.t);
  const width = n * COL;
  const now = Date.now();
  const nowIdx = hourIndex(axis, null, new Date(now));
  const nowInside = n > 0 && now >= Date.parse(axis[0]!) && now < Date.parse(axis[n - 1]!) + 3_600_000;
  const isPast = (i: number): boolean => hours[i]!.past || Date.parse(axis[i]!) + 3_600_000 <= now;

  // Row layout, top to bottom.
  const rows: Row[] = [];
  let y = 0;
  const add = (key: string, h: number): Row => {
    const r = { key, y, h };
    rows.push(r);
    y += h;
    return r;
  };
  const rDay = add("day", 20);
  const rHour = add("hour", 22);
  const rWin = add("windows", 28);
  const rWx = add("wx", 24);
  const rWind = add("wind", 64);
  const rKt = add("kt", 20);
  const rGust = add("gust", 18);
  const rDir = add("dir", 30);
  const rWave = hw ? add("wave", 84) : null;
  const rCalm = hw ? add("calm", 22) : null;
  const height = y + 4;

  const root = el("section", "tl");
  root.setAttribute("aria-label", "Forecast timeline, hour by hour");

  const chart = el("div", "tl-chart");
  const labels = el("div", "tl-labels");
  labels.style.height = `${height}px`;
  const scroller = el("div", "tl-scroll");
  chart.append(labels, scroller);
  root.append(chart);

  const svg = svgEl("svg", {
    width,
    height,
    viewBox: `0 0 ${width} ${height}`,
    class: "tl-svg",
    role: "img",
    "aria-label": "Wind, gusts, direction and wave height by hour. Tap an hour for details.",
    tabindex: "0",
  });
  scroller.append(svg);

  const defs = svgEl("defs");
  defs.innerHTML =
    `<pattern id="tl-hatch" width="10" height="10" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">` +
    `<line x1="0" y1="0" x2="0" y2="10" class="tl-hatch-line"/></pattern>` +
    `<pattern id="tl-hatch-dense" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">` +
    `<line x1="0" y1="0" x2="0" y2="5" class="tl-hatch-line tl-hatch-line--dense"/></pattern>`;
  svg.append(defs);

  const layer = (cls: string): SVGGElement => {
    const g = svgEl("g", { class: cls }) as SVGGElement;
    svg.append(g);
    return g;
  };
  const gBack = layer("tl-back");
  const gSel = layer("tl-sel-back");
  const gData = layer("tl-data");
  const gVeil = layer("tl-veil");
  const gTop = layer("tl-top");

  const cx = (i: number): number => i * COL + COL / 2;

  // Night and day separators.
  for (let i = 0; i < n; i++) {
    if (!hours[i]!.daylight) {
      gBack.append(svgEl("rect", { x: i * COL, y: 0, width: COL, height, class: "tl-night" }));
    }
  }
  for (let i = 0; i < n; i++) {
    const hh = hours[i]!.t.slice(11, 13);
    if (hh === "00" && i > 0) {
      gBack.append(svgEl("line", { x1: i * COL, x2: i * COL, y1: 0, y2: height, class: "tl-midnight" }));
    }
    const text = hh === "00" ? `${weekday(hours[i]!.t)} ${Number(hours[i]!.t.slice(8, 10))}` : weekday(hours[i]!.t);
    // Day names at midnight and noon, so one is always in view; and at the start of the axis
    // when the first midnight is far enough away not to collide.
    const firstFits = i === 0 && hh !== "00" && Number(hh) <= 20 && Number(hh) !== 12;
    if (hh === "00" || hh === "12" || firstFits) {
      gData.append(
        svgText(i * COL + 4, rDay.y + 14, text, hh === "00" ? "tl-day tl-day--strong" : "tl-day", "start"),
      );
    }
    if (Number(hh) % LABEL_EVERY === 0) {
      gData.append(svgText(cx(i), rHour.y + 15, hh, "tl-hour"));
    }
  }
  gBack.append(svgEl("line", { x1: 0, x2: width, y1: rHour.y + rHour.h, y2: rHour.y + rHour.h, class: "tl-axis" }));

  // Bar labels that slide along their bar so a scrolled-into bar still says what it is.
  const sticky: { text: SVGElement; x0: number; x1: number }[] = [];

  // Windows: labelled bars right under the axis.
  for (const w of tl.windows) {
    const a = hourIndexExact(axis, w.start);
    let b = hourIndexExact(axis, w.end);
    if (a < 0) continue;
    if (b < 0) b = n;
    const x = a * COL + 2;
    const wpx = (b - a) * COL - 4;
    const g = svgEl("g", { class: "tl-window", "data-score": w.score });
    g.append(svgEl("rect", { x, y: rWin.y + 4, width: wpx, height: rWin.h - 8, rx: 5, class: "tl-window-bar" }));
    const span = `${hhmm(w.start).slice(0, 2)}–${hhmm(w.end).slice(0, 2)}`;
    const word = SCORE_WORD[w.score];
    const label = wpx >= 118 ? `${word} ${span}` : wpx >= 64 ? word : word.slice(0, 3);
    const text = svgText(x + 7, rWin.y + 19, label, "tl-window-text", "start");
    g.append(text);
    gData.append(g);
    sticky.push({ text, x0: x + 7, x1: x + wpx - 6 });
  }

  // Airport: runs of hours that are not favorable, with the word for what limits them.
  let run = 0;
  while (run < n) {
    const h = hours[run]!;
    let end = run + 1;
    while (end < n && hours[end]!.score === h.score && hours[end]!.limiting === h.limiting) end++;
    if (h.score !== "favorable") {
      const x = run * COL + 1;
      const wpx = (end - run) * COL - 2;
      gData.append(
        svgEl("rect", {
          x,
          y: rWx.y + 3,
          width: wpx,
          height: rWx.h - 6,
          rx: 4,
          class: `tl-wx tl-wx--${h.score}`,
        }),
      );
      const word = limitingLabel(h.limiting) ?? SCORE_WORD[h.score].toLowerCase();
      if (wpx >= word.length * 6.4 + 8) {
        const text = svgText(x + 5, rWx.y + 16, word, `tl-wx-text tl-wx-text--${h.score}`, "start");
        gData.append(text);
        sticky.push({ text, x0: x + 5, x1: x + wpx - 4 });
      }
    }
    run = end;
  }

  // Wind: speed as a filled line, gust as a dashed line, one scale.
  const peak = Math.max(0, ...hours.map((h) => Math.max(h.wind.kt, h.wind.gust ?? 0)));
  const windMax = Math.max(20, Math.ceil((peak + 2) / 10) * 10);
  const yW = (v: number): number => rWind.y + rWind.h - 3 - (v / windMax) * (rWind.h - 10);
  for (let v = 10; v < windMax; v += 10) {
    gBack.append(svgEl("line", { x1: 0, x2: width, y1: yW(v), y2: yW(v), class: "tl-grid" }));
  }
  const kt = hours.map((h) => h.wind.kt);
  gData.append(svgEl("path", { d: areaPath(kt, cx, yW, yW(0)), class: "tl-wind-area" }));
  gData.append(svgEl("path", { d: linePath(kt, cx, yW), class: "tl-wind-line" }));
  gData.append(svgEl("path", { d: linePath(hours.map((h) => h.wind.gust), cx, yW), class: "tl-gust-line" }));

  for (let i = 0; i < n; i++) {
    if (Number(hours[i]!.t.slice(11, 13)) % LABEL_EVERY !== 0) continue;
    const w = hours[i]!.wind;
    gData.append(svgText(cx(i), rKt.y + 15, String(w.kt), "tl-num"));
    if (w.gust != null) gData.append(svgText(cx(i), rGust.y + 13, String(w.gust), "tl-num tl-num--muted"));
    gData.append(arrow(cx(i), rDir.y + rDir.h / 2, w));
  }

  // Home water: calmest region and open water, the pilot's limits as bands and guides.
  const calm = hw ? hw.best.map((b) => b?.hs_in ?? null) : [];
  if (hw && rWave && rCalm) {
    const open = hw.open_in;
    const obs = hw.observed.map((o) => o.wave_in ?? 0);
    // Sized to the calm water and the limits; rough open water above it is clipped at the
    // top of the row (its number is in the hour's detail).
    const top = Math.max(deps.limits.maxIn * 1.6, ...calm.map((v) => v ?? 0), ...obs) + 2;
    const waveMax = Math.ceil(top / 4) * 4;
    const yV = (v: number): number => rWave.y + rWave.h - 4 - (Math.min(v, waveMax) / waveMax) * (rWave.h - 10);
    const yMax = yV(deps.limits.maxIn);
    const yOk = yV(deps.limits.okIn);
    gBack.append(
      svgEl("rect", { x: 0, y: rWave.y + 2, width, height: Math.max(0, yMax - rWave.y - 2), fill: "url(#tl-hatch-dense)", class: "tl-band" }),
      svgEl("rect", { x: 0, y: yMax, width, height: Math.max(0, yOk - yMax), fill: "url(#tl-hatch)", class: "tl-band" }),
      svgEl("line", { x1: 0, x2: width, y1: yMax, y2: yMax, class: "tl-guide" }),
      svgEl("line", { x1: 0, x2: width, y1: yOk, y2: yOk, class: "tl-guide" }),
    );
    gData.append(svgEl("path", { d: linePath(open, cx, yV), class: "tl-open-line" }));
    gData.append(svgEl("path", { d: linePath(calm, cx, yV), class: "tl-calm-line" }));
    for (let i = 0; i < n; i++) {
      if (Number(hours[i]!.t.slice(11, 13)) % LABEL_EVERY !== 0) continue;
      const v = calm[i];
      gData.append(svgText(cx(i), rCalm.y + 16, v == null ? "–" : String(v), "tl-num"));
    }
    // Buoy dots go over the veil so the measured past stays readable.
    for (const o of hw.observed) {
      if (o.wave_in == null) continue;
      const i = hourIndexExact(axis, o.t);
      if (i < 0) continue;
      gTop.append(svgEl("circle", { cx: cx(i), cy: yV(o.wave_in), r: 4.5, class: "tl-obs" }));
    }
    labelAt(labels, rWave.y + 2, shortName(hw.name), "tl-label tl-label--strong");
    labelAt(labels, yMax - 7, `max ${deps.limits.maxIn}`, "tl-label tl-label--tick");
    if (yOk - yMax >= 12) labelAt(labels, yOk - 7, `ok ${deps.limits.okIn}`, "tl-label tl-label--tick");
    labelAt(labels, rCalm.y + 4, "calm in", "tl-label");
  }

  // Past hours: a veil, so the forecast ahead is what the eye lands on.
  for (let i = 0; i < n; i++) {
    if (isPast(i)) gVeil.append(svgEl("rect", { x: i * COL, y: rWin.y, width: COL, height: height - rWin.y, class: "tl-past" }));
  }
  if (nowInside) {
    const frac = Math.min(1, Math.max(0, (now - Date.parse(axis[nowIdx]!)) / 3_600_000));
    const x = (nowIdx + frac) * COL;
    gSel.append(svgEl("line", { x1: x, x2: x, y1: rHour.y + rHour.h, y2: height, class: "tl-now" }));
    gTop.append(svgEl("rect", { x: x - 16, y: rDay.y + 2, width: 32, height: 16, rx: 8, class: "tl-now-pill" }));
    gTop.append(svgText(x, rDay.y + 14, "now", "tl-now-text"));
  }

  // Selection: a filled column behind the data and an outline over it.
  const selBack = svgEl("rect", { x: 0, y: 0, width: COL, height, class: "tl-sel-fill" });
  const selLine = svgEl("rect", { x: 1, y: 1, width: COL - 2, height: height - 2, rx: 4, class: "tl-sel-line" });
  gSel.append(selBack);
  gTop.append(selLine);
  // The selected hour's own numbers, for the odd hours the rows do not label.
  const gSelText = layer("tl-sel-text");
  const labelSelected = (i: number): void => {
    gSelText.replaceChildren();
    if (Number(hours[i]!.t.slice(11, 13)) % LABEL_EVERY === 0) return;
    const w = hours[i]!.wind;
    gSelText.append(svgText(cx(i), rHour.y + 15, hours[i]!.t.slice(11, 13), "tl-hour tl-hour--sel"));
    gSelText.append(svgText(cx(i), rKt.y + 15, String(w.kt), "tl-num"));
    if (w.gust != null) gSelText.append(svgText(cx(i), rGust.y + 13, String(w.gust), "tl-num tl-num--muted"));
    gSelText.append(arrow(cx(i), rDir.y + rDir.h / 2, w));
    if (rCalm) {
      const v = calm[i];
      gSelText.append(svgText(cx(i), rCalm.y + 16, v == null ? "–" : String(v), "tl-num"));
    }
  };

  const slideLabels = (): void => {
    const left = scroller.scrollLeft;
    for (const s of sticky) {
      const w = (s.text as SVGTextContentElement).getComputedTextLength();
      const x = Math.max(s.x0, Math.min(left + 6, s.x1 - w));
      s.text.setAttribute("x", String(x));
      // Half a word is worse than none: the bar alone still shows.
      s.text.style.visibility = x < left && x + w > left ? "hidden" : "";
    }
  };

  // Row names in the sticky column.
  labelAt(labels, rWin.y + 7, "window", "tl-label");
  labelAt(labels, rWx.y + 5, deps.airportId, "tl-label tl-label--strong");
  labelAt(labels, rWind.y + 2, "wind", "tl-label tl-label--strong");
  for (let v = 10; v < windMax; v += 10) labelAt(labels, yW(v) - 7, String(v), "tl-label tl-label--tick");
  labelAt(labels, rKt.y + 3, "kt", "tl-label");
  labelAt(labels, rGust.y + 1, "gust", "tl-label");
  labelAt(labels, rDir.y + 7, "toward", "tl-label");

  root.append(legend(hw != null));

  // Detail for the clock's hour.
  const detail = el("div", "tl-detail");
  detail.setAttribute("aria-live", "polite");
  root.append(detail);

  const select = (i: number): void => {
    const target = axis[i];
    if (target == null) return;
    // Tapping the hour already shown goes back to live.
    const cur = clock.current;
    const same = cur.hour != null && hourIndex(axis, cur.hour) === i;
    clock.setHour(same ? null : target);
  };
  svg.addEventListener("click", (e) => {
    const box = svg.getBoundingClientRect();
    const i = Math.floor((e.clientX - box.left) / COL);
    if (i >= 0 && i < n) select(i);
  });
  svg.addEventListener("keydown", (e) => {
    const cur = hourIndex(axis, clock.current.hour);
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      const next = Math.min(n - 1, Math.max(0, cur + (e.key === "ArrowRight" ? 1 : -1)));
      clock.setHour(axis[next]!);
    } else if (e.key === "Escape") {
      clock.setHour(null);
    }
  });

  // Initial scroll: the saved position, else the current hour two columns from the left.
  let placed = false;
  const initial = savedScroll ?? Math.max(0, (nowIdx - 2) * COL);
  const place = (): void => {
    if (placed || scroller.clientWidth === 0) return;
    placed = true;
    scroller.scrollLeft = initial;
    slideLabels();
  };
  new ResizeObserver(place).observe(scroller);
  scroller.addEventListener(
    "scroll",
    () => {
      if (placed) savedScroll = scroller.scrollLeft;
      slideLabels();
    },
    { passive: true },
  );

  let wasConnected = false;
  unsubscribe = clock.subscribe((state: ClockState) => {
    if (root.isConnected) wasConnected = true;
    else if (wasConnected) {
      unsubscribe?.();
      unsubscribe = null;
      return;
    }
    const live = state.hour == null;
    const i = hourIndex(axis, state.hour, new Date());
    if (i < 0) return;
    selBack.setAttribute("x", String(i * COL));
    selLine.setAttribute("x", String(i * COL + 1));
    svg.classList.toggle("is-live", live);
    if (live) gSelText.replaceChildren();
    else labelSelected(i);
    detail.replaceChildren(...hourDetail(tl, i, live, isPast(i), deps));
    // A change from elsewhere (the map's time bar) scrolls the column into view.
    if (placed && !live) {
      const left = i * COL;
      if (left < scroller.scrollLeft || left + COL > scroller.scrollLeft + scroller.clientWidth) {
        scroller.scrollTo({ left: Math.max(0, left - scroller.clientWidth / 3), behavior: "smooth" });
      }
    }
  });

  return root;
}

function hourIndexExact(axis: readonly string[], t: string): number {
  const at = Date.parse(t);
  return axis.findIndex((a) => Date.parse(a) === at);
}

/** "Lake St. Clair" → "St. Clair": the label column is narrow. */
function shortName(name: string): string {
  const s = name.replace(/^Lake\s+/i, "").replace(/\s+Lake$/i, "");
  return s.length > 10 ? `${s.slice(0, 9)}…` : s;
}

// -- detail --------------------------------------------------------------------

function hourDetail(tl: Timeline, i: number, live: boolean, past: boolean, deps: TimelineDeps): Node[] {
  const h = tl.hours[i]!;
  const out: Node[] = [];

  const head = el("div", "tl-detail-head");
  const when = el("span", "tl-detail-when", `${weekday(h.t)} ${hhmm(h.t)}`);
  head.append(when);
  head.append(el("span", "tl-detail-tag", live ? "now" : past ? "past · model" : "forecast"));
  if (!live) {
    const back = el("button", "btn btn-quiet tl-now-btn", "Back to now");
    back.type = "button";
    back.addEventListener("click", () => clock.setHour(null));
    head.append(back);
  } else {
    head.append(el("span", "tl-detail-hint", "Tap an hour"));
  }
  out.push(head);

  // Airport weather.
  const air = el("div", "tl-detail-line");
  air.append(el("span", "tl-detail-name", deps.airportId));
  air.append(pill(h.score, h.limiting));
  const facts = [`wind ${formatWind(h.wind)}`];
  if (h.vis_sm != null) facts.push(`vis ${h.vis_sm} sm`);
  facts.push(formatCeiling(h.ceiling_ft, h.ceiling_known));
  if (h.fog_risk) facts.push("fog risk");
  if (h.precip_prob != null && h.precip_prob >= 20) facts.push(`precip ${h.precip_prob}%`);
  air.append(el("span", "tl-detail-facts", facts.join(" · ")));
  out.push(air);

  const hw = tl.home_water;
  if (hw) out.push(...waterDetail(hw, i, h, deps));
  return out;
}

function waterDetail(hw: TimelineHomeWater, i: number, h: TimelineHour, deps: TimelineDeps): Node[] {
  const out: Node[] = [];
  const line = el("div", "tl-detail-line");
  line.append(el("span", "tl-detail-name", hw.name));
  const score = hw.score[i];
  if (score) line.append(pill(score, hw.limiting[i] ?? null));
  const facts: string[] = [];
  const best = hw.best[i];
  facts.push(best ? `calmest ${best.label} ${best.hs_in} in` : "no region with a usable run");
  const open = hw.open_in[i];
  if (open != null) facts.push(`open water ${open} in`);
  const marine = hw.marine_in[i];
  if (marine != null) facts.push(`marine model ${marine} in`);
  line.append(el("span", "tl-detail-facts", facts.join(" · ")));
  out.push(line);

  const obs = hw.observed.find((o) => Date.parse(o.t) === Date.parse(h.t));
  if (obs) {
    const parts = [`Buoy ${obs.station} measured`];
    if (obs.wave_in != null) parts.push(`${obs.wave_in} in`);
    if (obs.wind) parts.push(`wind ${formatWind(obs.wind)}`);
    out.push(el("p", "tl-detail-obs", parts.join(" ")));
  }

  // Every region for this hour, calm to rough, in the home-water row look.
  const order = hw.labels
    .map((label, k) => ({ label, hs: hw.hs_in[i]?.[k] ?? null, wind: hw.wind[i]?.[k] ?? null }))
    .sort((a, b) => (a.hs ?? Infinity) - (b.hs ?? Infinity));
  if (order.length > 0) {
    const list = el("div", "brief-regions tl-regions");
    list.setAttribute("role", "list");
    for (const r of order) list.append(regionRow(hw.id, r.label, r.hs, r.wind, deps));
    out.push(list);
    if (order.length > REGIONS_SHOWN) {
      const hidden = order.length - REGIONS_SHOWN;
      list.classList.add("is-clipped");
      const more = el("button", "btn btn-quiet brief-regions-more", `Show ${hidden} rougher`);
      more.type = "button";
      more.addEventListener("click", () => {
        const clipped = list.classList.toggle("is-clipped");
        more.textContent = clipped ? `Show ${hidden} rougher` : "Show the calm end only";
      });
      out.push(more);
    }
  }

  const show = el("button", "btn btn-primary tl-show-btn", "Waves on the map");
  show.type = "button";
  show.addEventListener("click", () => deps.onShowWaves(hw.id));
  out.push(show);
  return out;
}

function regionRow(
  lakeId: number,
  label: string,
  hs: number | null,
  wind: Wind | null,
  deps: TimelineDeps,
): HTMLElement {
  const item = el("button", "brief-region");
  item.type = "button";
  item.setAttribute("role", "listitem");
  const { okIn, maxIn } = deps.limits;
  if (hs != null) item.dataset["score"] = hs <= okIn ? "favorable" : hs <= maxIn ? "marginal" : "unfavorable";
  item.classList.toggle("is-unusable", hs == null);
  item.append(el("span", "brief-region-bar"));
  item.append(el("span", "brief-region-label", label));
  const trail = el("span", "brief-region-trail");
  if (hs != null) trail.append(el("span", "brief-region-in", `${hs} in`));
  const detail = el("span", "brief-region-detail");
  if (wind) detail.append(el("span", "brief-region-wind", formatWind(wind)));
  if (hs == null) detail.append(el("span", "brief-region-run", "run too short into this wind"));
  trail.append(detail);
  item.append(trail);
  const where = deps.homeRegions.find((r) => r.label === label);
  item.addEventListener("click", () => {
    if (where) deps.onSelectRegion(lakeId, where.lat, where.lon);
    else deps.onShowWaves(lakeId);
  });
  return item;
}

function pill(score: Score, limiting: LimitingFactor | null): HTMLElement {
  const limit = score === "favorable" ? null : limitingLabel(limiting);
  const p = el("span", "score-pill", limit ? `${SCORE_WORD[score]} · ${limit}` : SCORE_WORD[score]);
  p.dataset["score"] = score;
  return p;
}

function legend(water: boolean): HTMLElement {
  const box = el("div", "tl-legend");
  const item = (swatch: string, text: string): void => {
    const s = el("span", "tl-legend-item");
    s.innerHTML = `<svg viewBox="0 0 22 10" aria-hidden="true">${swatch}</svg>`;
    s.append(text);
    box.append(s);
  };
  item(`<line x1="1" y1="5" x2="21" y2="5" class="tl-wind-line"/>`, "wind");
  item(`<line x1="1" y1="5" x2="21" y2="5" class="tl-gust-line"/>`, "gust");
  if (water) {
    item(`<line x1="1" y1="5" x2="21" y2="5" class="tl-calm-line"/>`, "calmest region");
    item(`<line x1="1" y1="5" x2="21" y2="5" class="tl-open-line"/>`, "open water");
    item(`<circle cx="11" cy="5" r="3.5" class="tl-obs"/>`, "buoy");
    item(`<rect x="1" y="0" width="20" height="10" fill="url(#tl-hatch-dense)" class="tl-band tl-band--key"/>`, "over max");
  }
  item(`<rect x="1" y="0" width="20" height="10" class="tl-night"/>`, "night");
  return box;
}

// -- svg helpers -----------------------------------------------------------------

function svgEl(tag: string, attrs: Record<string, string | number> = {}): SVGElement {
  const node = document.createElementNS(SVG_NS, tag) as SVGElement;
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  return node;
}

function svgText(x: number, y: number, text: string, cls: string, anchor = "middle"): SVGElement {
  const t = svgEl("text", { x, y, class: cls, "text-anchor": anchor });
  t.textContent = text;
  return t;
}

function linePath(values: (number | null)[], x: (i: number) => number, y: (v: number) => number): string {
  let d = "";
  let pen = false;
  values.forEach((v, i) => {
    if (v == null) {
      pen = false;
      return;
    }
    d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
    pen = true;
  });
  return d;
}

function areaPath(values: number[], x: (i: number) => number, y: (v: number) => number, base: number): string {
  if (values.length === 0) return "";
  let d = `M${x(0).toFixed(1)},${base.toFixed(1)}`;
  values.forEach((v, i) => (d += `L${x(i).toFixed(1)},${y(v).toFixed(1)}`));
  d += `L${x(values.length - 1).toFixed(1)},${base.toFixed(1)}Z`;
  return d;
}

/** Points downwind, as the wind layer's station arrows do. Light air is a small ring. */
function arrow(x: number, y: number, w: Wind): SVGElement {
  if (w.kt < 3) return svgEl("circle", { cx: x, cy: y, r: 4, class: "tl-calm-dot" });
  const g = svgEl("g", { transform: `translate(${x} ${y}) rotate(${(w.dir + 180) % 360})`, class: "tl-arrow" });
  g.append(svgEl("path", { d: "M0,-10 L6,-1 L2,-1 L2,10 L-2,10 L-2,-1 L-6,-1 Z" }));
  return g;
}

function labelAt(parent: HTMLElement, top: number, text: string, cls: string): void {
  const node = el("span", cls, text);
  node.style.top = `${top}px`;
  parent.append(node);
}
