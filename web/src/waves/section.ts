import {
  WAVE_CAVEAT,
  WAVE_CAVEAT_DEPTH,
  WAVE_CAVEAT_GUST,
  WAVE_CAVEAT_NO_DEPTH,
} from "../config";
import type { Lake } from "../types";
import { el, formatThousands } from "../ui/format";
import { currentTheme } from "../ui/theme";
import {
  DEPTH_UNKNOWN,
  FETCH_UNIT_M,
  openWaterInches,
  regionsForWind,
  waveHeight,
  windBin,
} from "@rules/waves/index.js";
import type { WavePoint, WaveRegion } from "@rules/waves/index.js";
import { WindControl } from "./control";
import type { WaveField } from "./load";
import { waveColor } from "./ramp";
import type { ForecastWind, WindSetting } from "./wind";

/**
 * "Where on this water is it calm in this wind", in the sheet.
 *
 * The section owns the wind and hands the recomputed field to whoever draws it: the caller
 * gets a `WaveState` on every change and pushes it at the map. Nothing here is specific to
 * one lake or one state; a 40-acre pond with one sample point and Lake St. Clair with four
 * hundred go through the same code, which is the whole point of the wave field.
 */

export interface WaveState {
  lake: Lake;
  field: WaveField;
  wind: WindSetting;
  minRunFt: number;
  regions: WaveRegion[];
}

export interface WaterSectionOptions {
  lake: Lake;
  field: WaveField;
  /** `limits.min_run_ft` from the briefing settings, or the contract's 2,000 ft fallback. */
  minRunFt: number;
  initialWind: WindSetting;
  /** The briefing's wind for this water body, when there is one. */
  forecast: ForecastWind | null;
  onChange: (state: WaveState) => void;
  onRegionTap: (region: WaveRegion, point: WavePoint, state: WaveState) => void;
}

export class WaterSection {
  readonly element: HTMLElement;
  private opts: WaterSectionOptions;
  private control: WindControl;
  private openLine: HTMLElement;
  private list: HTMLElement;
  private caveatLine: HTMLElement;
  private state: WaveState;

  constructor(opts: WaterSectionOptions) {
    this.opts = opts;
    this.state = {
      lake: opts.lake,
      field: opts.field,
      wind: { ...opts.initialWind },
      minRunFt: opts.minRunFt,
      regions: [],
    };

    this.element = el("section", "detail-section water-section");
    const head = el("div", "water-head");
    head.append(el("h3", "detail-h", "Water"));
    this.openLine = el("span", "water-open");
    head.append(this.openLine);
    this.element.append(head);

    this.control = new WindControl({
      value: this.state.wind,
      forecast: opts.forecast,
      onChange: (wind) => {
        this.state = { ...this.state, wind };
        this.recompute();
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

  /**
   * Everything that is not true about this number, in one line: where it comes from, which
   * wind speed is in it, and what the missing depth does to it.
   */
  private caveat(): string {
    const parts = [WAVE_CAVEAT];
    if (this.control?.atForecastGust) parts.push(WAVE_CAVEAT_GUST);
    parts.push(this.opts.field.hasDepth ? WAVE_CAVEAT_DEPTH : WAVE_CAVEAT_NO_DEPTH);
    return parts.join(" ");
  }

  private recompute(): void {
    const { field } = this.state;
    this.state = {
      ...this.state,
      regions: regionsForWind(
        field.points,
        field.labels,
        this.state.wind.dir,
        this.state.wind.kt,
        this.state.minRunFt,
      ),
    };
    this.paint();
    // Fires on the first pass too: the map needs the field as soon as the section exists.
    this.opts.onChange(this.state);
  }

  private paint(): void {
    this.caveatLine.textContent = this.caveat();
    const open = openWaterInches(this.state.regions);
    this.openLine.textContent = open == null ? "" : `open water ${open} in`;

    this.list.replaceChildren();
    if (this.state.regions.length === 0) {
      this.list.append(el("p", "muted", "No sample points on this water body."));
      return;
    }
    let calmestShown = false;
    for (const region of this.state.regions) {
      const isCalmest = !calmestShown && region.hs_in != null;
      if (isCalmest) calmestShown = true;
      this.list.append(this.regionRow(region, isCalmest));
    }
  }

  private regionRow(region: WaveRegion, isCalmest: boolean): HTMLElement {
    const usable = region.hs_in != null && region.point != null;
    const row = el("button", "wave-region");
    row.type = "button";
    row.setAttribute("role", "listitem");
    row.classList.toggle("is-unusable", !usable);

    const swatch = el("span", "wave-swatch");
    swatch.style.setProperty(
      "--wave-color",
      waveColor(region.hs_in ?? region.hs_all_in, currentTheme()),
    );
    swatch.setAttribute("aria-hidden", "true");
    row.append(swatch);

    const text = el("span", "wave-region-text");
    const nameLine = el("span", "wave-region-name", region.label);
    text.append(nameLine);
    if (isCalmest) {
      const tag = el("span", "tag wave-calmest", "calmest");
      nameLine.append(" ");
      nameLine.append(tag);
    }
    if (!usable) {
      text.append(el("span", "wave-region-sub", "run too short into this wind"));
    }
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

    row.title = usable
      ? `${region.label}: ${region.hs_in} in over ${region.n_usable} of ${region.n_points} sample points, ` +
        `${formatThousands(region.run_ft)} ft of run into this wind`
      : `${region.label}: no sample point has ${formatThousands(this.state.minRunFt)} ft of run into this wind`;

    // A region you cannot land in is still worth looking at, so the muted rows fly there
    // too; without a usable point the calmest point of the label stands for it.
    const at = region.point ?? this.calmestPointFor(region.label);
    if (at != null) {
      row.addEventListener("click", () => {
        const point = this.state.field.points[at];
        if (point) this.opts.onRegionTap(region, point, this.state);
      });
    } else {
      row.disabled = true;
    }
    return row;
  }

  /** Index of the calmest point carrying `label` in this wind, ignoring the run gate. */
  private calmestPointFor(label: string): number | null {
    const { field, wind } = this.state;
    const labelIndex = field.labels.indexOf(label);
    if (labelIndex < 0) return null;
    const bin = windBin(wind.dir);
    let best: number | null = null;
    let bestHs = Infinity;
    for (let i = 0; i < field.points.length; i++) {
      const p = field.points[i]!;
      if (p.label !== labelIndex) continue;
      const depth = p.depth_dm === DEPTH_UNKNOWN ? null : p.depth_dm / 10;
      const { hsM } = waveHeight(wind.kt, p.fetch[bin]! * FETCH_UNIT_M, depth);
      if (hsM < bestHs) {
        bestHs = hsM;
        best = i;
      }
    }
    return best;
  }
}
