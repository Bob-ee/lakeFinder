import {
  WAVE_CAVEAT,
  WAVE_CAVEAT_DEPTH,
  WAVE_CAVEAT_FORECAST,
  WAVE_CAVEAT_GUST,
  WAVE_CAVEAT_NO_DEPTH,
} from "../config";
import type { Lake } from "../types";
import { el, formatThousands } from "../ui/format";
import { openWaterInches, pointWave, regionsForWind, regionsForWinds } from "@rules/waves/index.js";
import type { RegionWind, WavePoint, WaveRegion } from "@rules/waves/index.js";
import { atGust, type HourWind, type LakeForecast } from "../forecast/lake";
import { hourLabel, windText } from "../forecast/time";
import { clock, type ClockState, type HourIso } from "../state/clock";
import { WindControl } from "./control";
import type { WaveField } from "./load";
import { BAND_WORD, waveBand, type WaveBand, type WaveLimits } from "./ramp";
import type { ForecastWind, WindSetting } from "./wind";

/**
 * "Where on this water is it calm in this wind", in the sheet.
 *
 * The section owns the wind and hands the recomputed field to whoever draws it: the caller
 * gets a `WaveState` on every change and pushes it at the map. Nothing here is specific to
 * one lake or one state; a 40-acre pond with one sample point and Lake St. Clair with four
 * hundred go through the same code, which is the whole point of the wave field.
 *
 * Three ways to pick the wind:
 * - `dial`: no forecast timeline for this water (offline, no api). One wind from the dial for
 *   every point, seeded from the briefing, exactly as before the timeline existed.
 * - `forecast`: the forecast clock's hour, every region on its own cell's forecast at the gust.
 * - `manual`: the dial was touched while a forecast was showing ("What if: 250/12"), one wind
 *   for every point until "Back to forecast".
 */

export type WaveMode =
  | { kind: "dial" }
  | { kind: "manual" }
  | {
      kind: "forecast";
      /** The axis hour on show. */
      hour: HourIso;
      /** The clock is live (following the current hour). */
      live: boolean;
      past: boolean;
    };

export interface WaveState {
  lake: Lake;
  field: WaveField;
  /** The headline wind the waves are computed with: the dial's, or the focus region's at the gust. */
  wind: WindSetting;
  /** The wind each point uses (per region in forecast mode); null leaves the point out. */
  windOf: (label: number, index: number) => RegionWind | null;
  mode: WaveMode;
  /** Forecast mode: the focus region's (or the centre's) forecast, gust included. */
  headline: HourWind | null;
  /** Forecast mode: the region the headline wind is for; null = the water body's centre. */
  headlineWhere: string | null;
  minRunFt: number;
  /** The pilot's wave limits, which band every point and region. */
  limits: WaveLimits;
  regions: WaveRegion[];
}

export interface WaterSectionOptions {
  lake: Lake;
  field: WaveField;
  /** `limits.min_run_ft` from the briefing settings, or the contract's 2,000 ft fallback. */
  minRunFt: number;
  /** `limits.wave_ok_in` / `wave_max_in`, or the contract defaults. */
  limits: WaveLimits;
  initialWind: WindSetting;
  /** The briefing's wind for this water body, when there is one. */
  forecast: ForecastWind | null;
  onChange: (state: WaveState) => void;
  onRegionTap: (region: WaveRegion, point: WavePoint, state: WaveState) => void;
}

export class WaterSection {
  readonly element: HTMLElement;
  /** Called when the focus region changes, so the time bar's readout can follow. */
  onFocusChange: (() => void) | null = null;

  private opts: WaterSectionOptions;
  private control: WindControl;
  private openLine: HTMLElement;
  private modeLine: HTMLElement;
  private modeText: HTMLElement;
  private modeWind: HTMLElement;
  private backButton: HTMLButtonElement;
  private list: HTMLElement;
  private caveatLine: HTMLElement;
  private state: WaveState;

  private timeline: LakeForecast | null = null;
  private clockState: ClockState = clock.current;
  private unsubscribe: (() => void) | null = null;
  private frame: number | null = null;
  private liveTimer: number | null = null;
  private lastIndex = -1;
  /** Regions per axis hour, so replaying the bar costs nothing the second time. */
  private regionCache = new Map<number, WaveRegion[]>();
  /** Label index the pilot tapped last; the dial and readouts show its wind. */
  private focusLabel: number | null = null;
  private labelIndex = new Map<string, number>();

  constructor(opts: WaterSectionOptions) {
    this.opts = opts;
    const dial = { ...opts.initialWind };
    this.state = {
      lake: opts.lake,
      field: opts.field,
      wind: dial,
      windOf: () => dial,
      mode: { kind: "dial" },
      headline: null,
      headlineWhere: null,
      minRunFt: opts.minRunFt,
      limits: opts.limits,
      regions: [],
    };
    for (const p of opts.field.points) {
      const name = opts.field.labels[p.label];
      if (name != null && !this.labelIndex.has(name)) this.labelIndex.set(name, p.label);
    }

    this.element = el("section", "detail-section water-section");
    const head = el("div", "water-head");
    head.append(el("h3", "detail-h", "Water"));
    this.openLine = el("span", "water-open");
    head.append(this.openLine);
    this.element.append(head);

    // Which hour and whose wind, or "What if" with the way back.
    this.modeLine = el("div", "water-mode");
    this.modeLine.hidden = true;
    const modeWords = el("div", "water-mode-words");
    this.modeText = el("span", "water-mode-text");
    this.modeWind = el("span", "water-mode-wind");
    modeWords.append(this.modeText, this.modeWind);
    this.backButton = el("button", "btn water-mode-back", "Back to forecast");
    this.backButton.type = "button";
    this.backButton.addEventListener("click", () => clock.backToForecast());
    this.modeLine.append(modeWords, this.backButton);
    this.element.append(this.modeLine);

    this.control = new WindControl({
      value: this.state.wind,
      forecast: opts.forecast,
      onChange: (wind) => {
        this.state = { ...this.state, wind };
        if (this.timeline && !this.clockState.manual) {
          // Touching the dial over a forecast is a "what if"; the clock tells everyone.
          clock.setManual();
        } else {
          this.recompute();
        }
      },
    });
    this.element.append(this.control.element);

    this.list = el("div", "wave-regions");
    this.list.setAttribute("role", "list");
    this.element.append(this.list);

    this.caveatLine = el("p", "muted small wave-caveat");
    this.element.append(this.caveatLine);

    this.recompute();
  }

  /** The current field, for the first paint of the map overlay. */
  get current(): WaveState {
    return this.state;
  }

  get forecast(): LakeForecast | null {
    return this.timeline;
  }

  /**
   * The forecast wind over time for this water body arrived: from here on the clock drives
   * the waves and the dial is a "what if". A new water body opens on the forecast.
   */
  setForecast(forecast: LakeForecast): void {
    this.timeline = forecast;
    this.regionCache.clear();
    this.control.setReference(null);
    clock.backToForecast();
    this.unsubscribe?.();
    this.unsubscribe = clock.subscribe((s) => {
      this.clockState = s;
      this.schedule();
    });
    // Live follows the hour as it turns.
    this.liveTimer = window.setInterval(() => {
      if (this.clockState.hour == null && !this.clockState.manual && this.timeline?.liveIndex() !== this.lastIndex) {
        this.schedule();
      }
    }, 60_000);
  }

  /** Stops listening to the clock; the section is being replaced. */
  destroy(): void {
    this.unsubscribe?.();
    this.unsubscribe = null;
    if (this.frame != null) cancelAnimationFrame(this.frame);
    this.frame = null;
    if (this.liveTimer != null) window.clearInterval(this.liveTimer);
    this.liveTimer = null;
    this.onFocusChange = null;
  }

  /** The time bar's readout at axis hour `h`: the focus region's forecast, else the centre's. */
  readoutAt(h: number): { wind: HourWind | null; where: string | null } {
    const t = this.timeline;
    if (!t) return { wind: null, where: null };
    if (this.focusLabel != null) {
      const wind = t.labelWind(h, this.focusLabel);
      if (wind) return { wind, where: this.state.field.labels[this.focusLabel] ?? null };
    }
    return { wind: t.centroidWind(h), where: null };
  }

  /** Several clock changes in one frame (a drag, a replay) cost one recompute. */
  private schedule(): void {
    if (this.frame != null) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = null;
      this.recompute();
    });
  }

  /**
   * Everything that is not true about this number, in one line: where it comes from, which
   * wind speed is in it, and what the missing depth does to it.
   */
  private caveat(): string {
    const parts: string[] = [];
    if (this.state.mode.kind === "forecast") {
      parts.push(WAVE_CAVEAT_FORECAST);
    } else {
      parts.push(WAVE_CAVEAT);
      if (this.state.mode.kind === "dial" && this.control?.atForecastGust) parts.push(WAVE_CAVEAT_GUST);
    }
    parts.push(this.opts.field.hasDepth ? WAVE_CAVEAT_DEPTH : WAVE_CAVEAT_NO_DEPTH);
    return parts.join(" ");
  }

  private recompute(): void {
    const { field, minRunFt } = this.state;
    const t = this.timeline;

    if (t && !this.clockState.manual) {
      const h = t.index(this.clockState.hour);
      this.lastIndex = h;
      const windOf = t.waveWindOf(h);
      let regions = this.regionCache.get(h);
      if (!regions) {
        regions = regionsForWinds(field.points, field.labels, windOf, minRunFt);
        this.regionCache.set(h, regions);
      }
      const { wind: headline, where } = this.readoutAt(h);
      const gustWind = atGust(headline);
      const wind = gustWind ?? this.state.wind;
      // The dial shows the forecast it is standing in for, without starting a "what if".
      this.control.set(wind, false);
      this.control.setNote(headline?.gust != null && headline.gust > headline.kt ? "gust" : "forecast");
      this.state = {
        ...this.state,
        wind,
        windOf,
        regions,
        headline,
        headlineWhere: where,
        mode: {
          kind: "forecast",
          hour: t.times[h] ?? "",
          live: this.clockState.hour == null,
          past: h < t.past,
        },
      };
    } else {
      const wind = this.control.wind;
      const constant = { dir: wind.dir, kt: wind.kt };
      this.control.setNote(null);
      this.state = {
        ...this.state,
        wind,
        windOf: () => constant,
        regions: regionsForWind(field.points, field.labels, wind.dir, wind.kt, minRunFt),
        headline: null,
        headlineWhere: null,
        mode: t ? { kind: "manual" } : { kind: "dial" },
      };
    }
    this.paint();
    // Fires on the first pass too: the map needs the field as soon as the section exists.
    this.opts.onChange(this.state);
  }

  private paintMode(): void {
    const { mode } = this.state;
    this.modeLine.hidden = mode.kind === "dial";
    this.modeLine.classList.toggle("is-manual", mode.kind === "manual");
    this.backButton.hidden = mode.kind !== "manual";
    if (mode.kind === "forecast") {
      const when = mode.past ? "past, model" : mode.live ? "forecast, now" : "forecast";
      this.modeText.textContent = `${hourLabel(mode.hour)} ${when}`;
      const where = this.state.headlineWhere ?? "centre";
      this.modeWind.textContent = this.state.headline
        ? `Wind at ${where} ${windText(this.state.headline)} · each region on its own wind`
        : "No forecast wind for this hour";
    } else if (mode.kind === "manual") {
      this.modeText.textContent = `What if: ${windText(this.state.wind)}`;
      this.modeWind.textContent = "One wind for the whole water body";
    }
  }

  private paint(): void {
    this.paintMode();
    this.caveatLine.textContent = this.caveat();
    const open = openWaterInches(this.state.regions);
    this.openLine.textContent = open == null ? "" : `open water ${open} in`;

    this.list.replaceChildren();
    if (this.state.regions.length === 0) {
      this.list.append(
        el(
          "p",
          "muted",
          this.state.field.points.length === 0
            ? "No sample points on this water body."
            : "No forecast wind for this hour.",
        ),
      );
      return;
    }
    let calmestShown = false;
    for (const region of this.state.regions) {
      const isCalmest = !calmestShown && region.hs_in != null;
      if (isCalmest) calmestShown = true;
      this.list.append(this.regionRow(region, isCalmest));
    }
  }

  /** The forecast wind for a region at the hour on show, in forecast mode only. */
  private regionWind(region: WaveRegion): HourWind | null {
    const { mode } = this.state;
    if (mode.kind !== "forecast" || !this.timeline) return null;
    const label = this.labelIndex.get(region.label);
    return label == null ? null : this.timeline.labelWind(this.lastIndex, label);
  }

  private regionRow(region: WaveRegion, isCalmest: boolean): HTMLElement {
    const usable = region.hs_in != null && region.point != null;
    const row = el("button", "wave-region");
    row.type = "button";
    row.setAttribute("role", "listitem");
    row.classList.toggle("is-unusable", !usable);
    const label = this.labelIndex.get(region.label) ?? null;
    const focused = label != null && label === this.focusLabel;
    row.classList.toggle("is-focus", focused);
    if (focused) row.setAttribute("aria-current", "true");

    const band = waveBand(region.hs_in, usable, this.state.limits);
    row.append(waveSwatch(band));

    const text = el("span", "wave-region-text");
    const nameLine = el("span", "wave-region-name", region.label);
    text.append(nameLine);
    if (isCalmest) {
      const tag = el("span", "tag wave-calmest", "calmest");
      nameLine.append(" ");
      nameLine.append(tag);
    }
    const wind = this.regionWind(region);
    const subParts: string[] = [];
    if (wind) subParts.push(`wind ${windText(wind)}`);
    if (!usable) subParts.push("run too short into this wind");
    if (subParts.length > 0) text.append(el("span", "wave-region-sub", subParts.join(" · ")));
    row.append(text);

    const trail = el("span", "wave-region-trail");
    if (usable) {
      trail.append(el("span", "wave-in", `${region.hs_in} in`));
      trail.append(el("span", "wave-run", `${formatThousands(region.run_ft)} ft`));
    } else {
      trail.append(el("span", "wave-in wave-in--muted", `${region.hs_all_in} in`));
      trail.append(el("span", "wave-run", "no run"));
    }
    row.append(trail);

    const windWords = wind ? ` in ${windText(wind)}` : "";
    row.title = usable
      ? `${region.label}: ${region.hs_in} in, ${BAND_WORD[band]}, over ${region.n_usable} of ${region.n_points} sample points, ` +
        `${formatThousands(region.run_ft)} ft of run into this wind${windWords}`
      : `${region.label}: no sample point has ${formatThousands(this.state.minRunFt)} ft of run into this wind${windWords}`;

    // A region you cannot land in is still worth looking at, so the muted rows fly there
    // too; without a usable point the calmest point of the label stands for it.
    const at = region.point ?? this.calmestPointFor(region.label);
    if (at != null) {
      row.addEventListener("click", () => {
        const point = this.state.field.points[at];
        if (label != null && label !== this.focusLabel) {
          this.focusLabel = label;
          this.recompute();
          this.onFocusChange?.();
        }
        if (point) this.opts.onRegionTap(region, point, this.state);
      });
    } else {
      row.disabled = true;
    }
    return row;
  }

  /** Index of the calmest point carrying `label` in its wind, ignoring the run gate. */
  private calmestPointFor(label: string): number | null {
    const { field, windOf } = this.state;
    const labelIndex = this.labelIndex.get(label);
    if (labelIndex == null) return null;
    let best: number | null = null;
    let bestHs = Infinity;
    for (let i = 0; i < field.points.length; i++) {
      const p = field.points[i]!;
      if (p.label !== labelIndex) continue;
      const w = windOf(p.label, i);
      if (!w) continue;
      const { hsM } = pointWave(p, w.dir, w.kt, 0);
      if (hsM < bestHs) {
        bestHs = hsM;
        best = i;
      }
    }
    return best;
  }
}

/**
 * The same mark the map draws for a band, as a small DOM dot: the key and the region list
 * both use it, so the shape cue (heavy ring for over the limit, hollow for no run) reads the
 * same everywhere. Colours come from the CSS variables `applyWaveBandVars` sets.
 */
export function waveSwatch(band: WaveBand): HTMLElement {
  const swatch = el("span", `wave-swatch wave-swatch--${band}`);
  swatch.setAttribute("aria-hidden", "true");
  return swatch;
}
