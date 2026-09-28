import { minRunFt } from "../briefing/home-water";
import { WAVES, WIND } from "../config";
import type { Lake } from "../types";
import { el, formatChordBearings, formatDistanceFt, formatThousands } from "../ui/format";
import { runAlong } from "./extents";
import type { NearStation, WindData } from "./data";
import {
  chordComponents,
  formatAge,
  formatAgo,
  formatNm,
  pad3,
  shapeFor,
  shapeSvg,
  sourceWord,
  windText,
} from "./format";
import type { WindReading } from "./types";

/**
 * The selected water body's "Wind" block and its peek line.
 *
 * Nearest three stations, model wind at the lake, and the headwind / crosswind for landing
 * along the longest chord. Everything reads from `WindData`'s cache and repaints when a
 * fetch lands, so an offline pilot sees the last answer with its age and nothing at all if
 * there has never been one.
 */

/** Which wind the peek line and the components use: a station close enough, else the model. */
interface Basis {
  wind: WindReading;
  /** "KDET 14 min" or "model". */
  tag: string;
  /** "Using KDET, observed 14 min ago" or "Using the model wind". */
  using: string;
  ageMin: number;
}

export class WindSection {
  readonly element: HTMLElement;
  private unsubscribe: () => void;
  /** Run of water along the wind for the last looked-up bearing; `ft` null = no extent entry. */
  private run: { bearing: number; ft: number | null } | null = null;
  private runPending: number | null = null;
  private minRun: number | null = null;

  constructor(
    private lake: Lake,
    private data: WindData,
    private peekLine: HTMLElement,
  ) {
    this.element = el("section", "detail-section wind-section");
    this.element.hidden = true;
    this.peekLine.hidden = true;
    this.unsubscribe = data.onChange(() => this.render());
    data.requestAround(lake.lat, lake.lon);
    void data.point(lake.lat, lake.lon).then(() => this.render());
    this.render();
  }

  private basis(near: NearStation[]): Basis | null {
    // Nearest within 15 nm; a stale one (past 60 min) only when nothing fresher is that close.
    const within = near.filter((n) => n.nm <= WIND.peekStationNm && windText(n.station) != null);
    const close = within.find((n) => n.ageMin <= WIND.staleAfterMin) ?? within[0];
    if (close) {
      return {
        wind: close.station,
        tag: `${close.station.id} ${formatAge(close.ageMin)}`,
        using: `Using ${close.station.id} (${formatNm(close.nm)}), observed ${formatAgo(close.ageMin)}`,
        ageMin: close.ageMin,
      };
    }
    const model = this.data.cachedPoint(this.lake.lat, this.lake.lon);
    if (model && windText(model.wind) != null) {
      const t = Date.parse(model.wind.time);
      const age = Number.isNaN(t) ? (Date.now() - model.fetchedAt) / 60_000 : (Date.now() - t) / 60_000;
      return {
        wind: model.wind,
        tag: "model",
        using: "Using the model wind: no station within 15 nm",
        ageMin: Math.max(0, age),
      };
    }
    return null;
  }

  /** Called when the selection moves on; the block stops listening. */
  dispose(): void {
    this.unsubscribe();
  }

  render(): void {
    const pool = this.data.nearest(this.lake.lat, this.lake.lon, 12);
    const near = pool.slice(0, WIND.nearestCount);
    const model = this.data.cachedPoint(this.lake.lat, this.lake.lon);
    const basis = this.basis(pool);
    this.renderPeek(basis);

    if (near.length === 0 && !model) {
      this.element.hidden = true;
      this.element.replaceChildren();
      return;
    }
    this.element.hidden = false;

    const head = el("div", "wind-head");
    head.append(el("h3", "detail-h", "Wind"));
    const b = this.lake.bbox;
    const fetchedAt = this.data.fetchedAt([b[0], b[1], b[2], b[3]]) ?? model?.fetchedAt ?? null;
    if (fetchedAt != null) {
      const age = (Date.now() - fetchedAt) / 60_000;
      const upd = el("span", "wind-updated", `updated ${formatAgo(age)}`);
      if (age > WIND.staleAfterMin) upd.append(" ", el("span", "stale-badge", "stale"));
      head.append(upd);
    }
    const parts: HTMLElement[] = [head];

    if (basis) parts.push(this.componentsBlock(basis));

    if (near.length > 0) {
      const list = el("ul", "wind-stations");
      for (const n of near) list.append(stationRow(n));
      parts.push(list);
    }

    if (model) {
      const text = windText(model.wind);
      const row = el("div", "wind-model");
      row.append(el("span", "wind-model-label", "Model at this water"));
      row.append(el("span", "wind-value", text ? `${text} kt` : "no data"));
      const t = Date.parse(model.wind.time);
      if (!Number.isNaN(t)) {
        row.append(el("span", "wind-age", formatAgo((Date.now() - t) / 60_000)));
      }
      parts.push(row);
    }

    parts.push(
      el(
        "p",
        "muted small",
        "Observations and model wind as reported, not a forecast. Arrows point downwind.",
      ),
    );
    this.element.replaceChildren(...parts);
  }

  private renderPeek(basis: Basis | null): void {
    const text = basis ? windText(basis.wind) : null;
    if (!basis || !text) {
      this.peekLine.hidden = true;
      return;
    }
    this.peekLine.hidden = false;
    this.peekLine.textContent = `Wind ${text} (${basis.tag})`;
    this.peekLine.classList.toggle("is-stale", basis.ageMin > WIND.staleAfterMin);
  }

  /**
   * Where to land in this wind, the way the briefing scores it (handoff section 7): into
   * the wind when the water along it is at least `min_run_ft`; the longest-chord
   * headwind / crosswind only when that run is short (or there is no extent for this lake).
   * Below 5 kt the wind sets no direction and only the chord is shown.
   */
  private componentsBlock(basis: Basis): HTMLElement {
    const box = el("div", "wind-components");
    const w = basis.wind;
    const noun = this.lake.kind === "river" || this.lake.kind === "connecting_water" ? "reach" : "chord";
    const chord = `Longest ${noun} ${formatDistanceFt(this.lake.chord_ft)}, ${formatChordBearings(this.lake.chord_bearing_deg)}`;

    if (w.speed_kt == null || w.speed_kt < WIND.lightWindKt || w.dir_deg == null) {
      const what = w.speed_kt === 0 ? "Calm" : w.dir_deg == null ? "Variable wind" : "Light wind";
      box.append(el("div", "wind-comp-line", what), el("div", "wind-comp-title", chord));
      box.append(el("div", "wind-comp-src", basis.using));
      return box;
    }

    const into = w.dir_deg;
    const run = this.run?.bearing === into ? this.run.ft : undefined;
    if (run === undefined) {
      // Still looking up the run: say which way the wind is, pick the display once it lands.
      this.lookupRun(into);
      box.append(el("div", "wind-comp-title", `Into the wind, ${pad3(into)}°`));
      box.append(el("div", "wind-comp-src", basis.using));
      return box;
    }
    if (run != null && this.minRun != null && run >= this.minRun) {
      box.append(el("div", "wind-comp-title", `Into the wind, ${pad3(into)}°`));
      const line = el("div", "wind-comp-line");
      const num = el("span", "wind-comp");
      // Feet, or miles past five (formatDistanceFt): 138,023 ft on Lake St. Clair reads as nothing.
      const [n, unit] = formatDistanceFt(run).split(" ");
      num.append(el("span", "wind-comp-num", n ?? ""), ` ${unit ?? "ft"} of water`);
      line.append(num);
      box.append(line);
      box.append(el("div", "wind-comp-src", basis.using));
      return box;
    }

    const c = chordComponents(this.lake.chord_bearing_deg, w);
    if (!c) return box;
    const title =
      run != null
        ? `Run into the wind is short (${formatThousands(Math.round(run))} ft): landing ${pad3(c.heading)}° along the longest ${noun}`
        : `Landing ${pad3(c.heading)}° along the longest ${noun}`;
    box.append(el("div", "wind-comp-title", title));
    const line = el("div", "wind-comp-line");
    const hw = el("span", "wind-comp");
    hw.append(el("span", "wind-comp-num", `${Math.abs(c.headwind)}`), ` kt ${c.headwind < 0 ? "tailwind" : "headwind"}`);
    const xw = el("span", "wind-comp");
    xw.append(
      el("span", "wind-comp-num", `${c.crosswind}`),
      ` kt crosswind${c.side ? ` from the ${c.side}` : ""}`,
    );
    line.append(hw, xw);
    box.append(line);
    if (c.gustHeadwind != null && c.gustCrosswind != null) {
      box.append(
        el("div", "wind-comp-gust", `In the gust: ${c.gustHeadwind} kt head, ${c.gustCrosswind} kt cross`),
      );
    }
    box.append(el("div", "wind-comp-src", basis.using));
    return box;
  }

  /** Fetches the run along `bearing` and the pilot's minimum run, then repaints once. */
  private lookupRun(bearing: number): void {
    if (this.runPending === bearing) return;
    this.runPending = bearing;
    void Promise.all([runAlong(this.lake.id, bearing), minRunFt(WAVES.defaultMinRunFt)]).then(
      ([ft, min]) => {
        this.runPending = null;
        this.minRun = min;
        this.run = { bearing, ft };
        this.render();
      },
    );
  }
}

function stationRow(n: NearStation): HTMLElement {
  const s = n.station;
  const li = el("li", "wind-station");
  const glyph = el("span", "wind-station-glyph");
  glyph.innerHTML = shapeSvg(shapeFor(s), s.dir_deg == null ? 0 : (s.dir_deg + 180) % 360);
  const main = el("div", "wind-station-main");
  const top = el("div", "wind-station-top");
  top.append(el("span", "wind-station-id", s.id), el("span", "wind-station-src", sourceWord(s.source)));
  const text = windText(s);
  top.append(el("span", "wind-value", text ? `${text} kt` : "—"));
  const sub = el(
    "div",
    "wind-station-sub",
    [formatNm(n.nm), `observed ${formatAgo(n.ageMin)}`].join(" · "),
  );
  if (n.ageMin > WIND.staleAfterMin) sub.append(" ", el("span", "stale-badge", "stale"));
  main.append(top, sub);
  if (s.name) main.title = s.name;
  li.append(glyph, main);
  return li;
}
