import { VERDICT_PHRASE, WAVES } from "../config";
import { type Fix, type LocationService, statusNote, usableCourse } from "../location";
import {
  angleDiff,
  bearingDeg,
  distanceNm,
  formatBearing,
  formatNm,
} from "../location/geo";
import type { Lake } from "../types";
import { el } from "../ui/format";
import { readLocal, writeLocal } from "../ui/theme";
import { lakeRow } from "./row";

/** Contract "Nearest tab". */
const MAX_ROWS = 25;
const CONE_MIN_KT = 30;
const CONE_HALF_DEG = 45;
const CONE_ANY_DIRECTION_NM = 2;
const MIN_INTERVAL_MS = 5000;
const MIN_MOVE_NM = 0.2;
const CONE_KEY = "seaplane.nearestCone";
const ALL_WATER_KEY = "seaplane.nearestAllWater";

export interface NearestDeps {
  lakes: () => readonly Lake[];
  location: LocationService;
  mapCenter: () => { lat: number; lon: number };
  onSelect: (id: number) => void;
  /** The briefing setting `min_run_ft`, else the settings default (home-water.ts). */
  minRunFt: () => Promise<number>;
}

interface Origin {
  lat: number;
  lon: number;
  fromFix: boolean;
}

/**
 * The Nearest tab: water long enough to use (chord >= min run, every verdict), by distance
 * from the fix (or the map centre when there is none), first 25, with a forward cone above
 * 30 kt and a "Show all water" toggle that drops the length filter. Rows are the search row with
 * distance, bearing and the verdict word, so the same muscle memory reads all three lists.
 */
export class NearestList {
  readonly element: HTMLElement;
  private head: HTMLElement;
  private list: HTMLElement;
  private visible = false;
  private coneWanted = readLocal(CONE_KEY) !== "0";
  private allWater = readLocal(ALL_WATER_KEY) === "1";
  private minRun: number = WAVES.defaultMinRunFt;
  private last: { origin: Origin; at: number; cone: boolean } | null = null;

  constructor(private deps: NearestDeps) {
    this.element = el("div", "nearest");
    this.head = el("div", "nearest-head");
    this.list = el("div", "nearest-list");
    this.list.setAttribute("role", "listbox");
    this.list.setAttribute("aria-label", "Nearest water");
    this.element.append(this.head, this.list);
    deps.location.onChange((fix) => this.onFix(fix));
    void this.refreshMinRun();
  }

  private async refreshMinRun(): Promise<void> {
    try {
      const ft = await this.deps.minRunFt();
      if (Number.isFinite(ft) && ft > 0 && ft !== this.minRun) {
        this.minRun = ft;
        if (this.visible) this.render();
      }
    } catch {
      // Keep the default.
    }
  }

  /** Called by the tab strip; the list only does work while it is on screen. */
  setVisible(on: boolean): void {
    this.visible = on;
    if (on) {
      this.render();
      // The pilot may have changed the limit in settings since the last look.
      void this.refreshMinRun();
    }
  }

  /** Map `moveend`: matters only while the origin is the map centre. */
  onMapMoved(): void {
    if (this.visible && !this.deps.location.fix) this.render();
  }

  private onFix(fix: Fix | null): void {
    if (!this.visible) return;
    if (!fix || !this.last || !this.last.origin.fromFix) {
      this.render();
      return;
    }
    const cone = this.coneActive(fix);
    if (cone !== this.last.cone) {
      this.render();
      return;
    }
    // At most every 5 s or every 0.2 nm, whichever comes later: both must have passed.
    const moved = distanceNm(this.last.origin.lat, this.last.origin.lon, fix.lat, fix.lon);
    if (Date.now() - this.last.at >= MIN_INTERVAL_MS && moved >= MIN_MOVE_NM) this.render();
  }

  private coneAvailable(fix: Fix | null): boolean {
    return fix != null && usableCourse(fix) != null && (fix.speed_kt ?? 0) > CONE_MIN_KT;
  }

  private coneActive(fix: Fix | null): boolean {
    return this.coneWanted && this.coneAvailable(fix);
  }

  render(): void {
    const fix = this.deps.location.fix;
    const origin: Origin = fix
      ? { lat: fix.lat, lon: fix.lon, fromFix: true }
      : { ...this.deps.mapCenter(), fromFix: false };
    const course = usableCourse(fix);
    const cone = this.coneActive(fix) && course != null;

    const rows: { lake: Lake; nm: number; brg: number }[] = [];
    for (const lake of this.deps.lakes()) {
      if (!this.allWater && !(lake.chord_ft >= this.minRun)) continue;
      const nm = distanceNm(origin.lat, origin.lon, lake.lat, lake.lon);
      rows.push({ lake, nm, brg: 0 });
    }
    rows.sort((a, b) => a.nm - b.nm);
    const shown: typeof rows = [];
    for (const r of rows) {
      r.brg = bearingDeg(origin.lat, origin.lon, r.lake.lat, r.lake.lon);
      if (cone && r.nm > CONE_ANY_DIRECTION_NM && angleDiff(r.brg, course) > CONE_HALF_DEG) continue;
      shown.push(r);
      if (shown.length >= MAX_ROWS) break;
    }

    this.renderHead(origin, fix, cone, course);
    this.list.replaceChildren(
      ...shown.map((r) => {
        const trailing = el("span", "nearest-trailing");
        trailing.append(el("span", "nearest-nm", formatNm(r.nm)), el("span", "nearest-brg", formatBearing(r.brg)));
        const note = el("span", "nearest-verdict", VERDICT_PHRASE[r.lake.verdict]);
        note.dataset["verdict"] = r.lake.verdict;
        return lakeRow(r.lake, { onSelect: this.deps.onSelect, trailing, note });
      }),
    );
    if (shown.length === 0) {
      this.list.append(
        el(
          "p",
          "muted nearest-empty",
          cone ? "No water in the ±45° cone ahead." : "No water in the index.",
        ),
      );
    }
    this.last = { origin, at: Date.now(), cone };
  }

  private renderHead(origin: Origin, fix: Fix | null, cone: boolean, course: number | null): void {
    const title = el("div", "nearest-title");
    title.append(el("h3", "detail-h", "Nearest"));
    if (cone) title.append(el("span", "tag nearest-ahead-tag", "ahead"));
    const where = el("p", "muted small nearest-origin");
    if (origin.fromFix) {
      where.textContent = cone && course != null
        ? `From your position · ±45° of ${formatBearing(course)}, or within 2 nm`
        : "From your position";
    } else {
      where.textContent = "From map center";
    }
    const parts: HTMLElement[] = [title, where];
    const length = this.allWater
      ? "All water, any length"
      : `Runs of ${this.minRun.toLocaleString("en-US")} ft or more, every verdict`;
    parts.push(el("p", "muted small nearest-origin", length));

    if (!fix) {
      const note = statusNote(this.deps.location.status);
      if (note) parts.push(el("p", "small nearest-note", note));
    }

    const toggles = el("div", "nearest-toggles");
    const all = el("button", "btn nearest-all-btn");
    all.type = "button";
    all.setAttribute("aria-pressed", String(this.allWater));
    all.textContent = this.allWater ? "Long enough only" : "Show all water";
    all.addEventListener("click", () => {
      this.allWater = !this.allWater;
      writeLocal(ALL_WATER_KEY, this.allWater ? "1" : "0");
      this.render();
    });
    toggles.append(all);

    if (this.coneAvailable(fix)) {
      const toggle = el("button", "btn nearest-cone-btn");
      toggle.type = "button";
      toggle.setAttribute("aria-pressed", String(this.coneWanted));
      toggle.textContent = this.coneWanted ? "All directions" : "Ahead only";
      toggle.addEventListener("click", () => {
        this.coneWanted = !this.coneWanted;
        writeLocal(CONE_KEY, this.coneWanted ? "1" : "0");
        this.render();
      });
      toggles.append(toggle);
    }
    parts.push(toggles);
    this.head.replaceChildren(...parts);
  }
}

const NEAREST_ICON =
  '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<circle cx="5" cy="6" r="1.6"/><circle cx="5" cy="12" r="1.6"/><circle cx="5" cy="18" r="1.6"/>' +
  '<path d="M10 6h10M10 12h8M10 18h6"/></svg>';

/**
 * A right-edge button that opens the sheet on the Nearest tab. The tab strip lives inside
 * the sheet, so without this there is no way to reach the list while the sheet is hidden.
 */
export function mountNearestButton(controls: HTMLElement, open: () => void): HTMLButtonElement {
  const btn = el("button", "icon-btn map-btn map-btn-nearest");
  btn.type = "button";
  btn.title = "Nearest water";
  btn.setAttribute("aria-label", "Nearest water");
  btn.innerHTML = NEAREST_ICON;
  btn.addEventListener("click", open);
  const after = controls.querySelector(".compass-btn") ?? controls.querySelector(".map-btn-recenter");
  if (after) after.after(btn);
  else controls.prepend(btn);
  return btn;
}
