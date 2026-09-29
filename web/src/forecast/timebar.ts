import type { Timeline } from "../briefing/types";
import { el } from "../ui/format";
import { clock, type ClockState } from "../state/clock";
import type { HourWind, LakeForecast } from "./lake";
import { isDaylight } from "./sun";
import { hourLabel, wall, windText } from "./time";

/**
 * The map's time bar: scrub the forecast axis and the wave dots follow, every region on its
 * own forecast wind (contract "Map time bar").
 *
 * It only writes the forecast clock; everything else (the Water section, the dots, the key,
 * the Briefing chart) listens to that clock, so a column tapped in the Briefing moves this
 * bar and a drag here moves the chart. Built for a thumb in a cockpit: the whole track is the
 * drag target, the buttons are 48 px, and nothing is told apart by hue alone (night is
 * darker, the past is hatched, windows are bars with a solid or dashed edge).
 */

export interface TimeBarReadout {
  wind: HourWind | null;
  /** Region name, or null for the water body's centre. */
  where: string | null;
}

/** One hour per this many ms while playing. */
const PLAY_MS = 500;

export class TimeBar {
  readonly element: HTMLElement;
  private track: HTMLElement;
  private layers: HTMLElement;
  private thumb: HTMLElement;
  private nowMark: HTMLElement;
  private readTime: HTMLElement;
  private readWind: HTMLElement;
  private playButton: HTMLButtonElement;
  private nowButton: HTMLButtonElement;

  private forecast: LakeForecast | null = null;
  private readout: ((h: number) => TimeBarReadout) | null = null;
  private state: ClockState = clock.current;
  private playTimer: number | null = null;
  private liveTimer: number | null = null;
  private dragging = false;
  /** Laid out for a phone sheet that covers the bar's spot, or with no forecast: not shown. */
  private suppressed = false;

  constructor(
    parent: HTMLElement,
    private readonly timeline: () => Timeline | null | undefined,
  ) {
    this.element = el("div", "timebar");
    this.element.hidden = true;
    this.element.setAttribute("role", "group");
    this.element.setAttribute("aria-label", "Forecast time");

    this.playButton = el("button", "timebar-btn timebar-play");
    this.playButton.type = "button";
    this.playButton.addEventListener("click", () => (this.playTimer == null ? this.play() : this.pause()));

    const read = el("div", "timebar-read");
    this.readTime = el("span", "timebar-time");
    this.readWind = el("span", "timebar-wind");
    read.append(this.readTime, this.readWind);
    read.setAttribute("aria-live", "polite");

    this.nowButton = el("button", "timebar-btn timebar-now", "Now");
    this.nowButton.type = "button";
    this.nowButton.setAttribute("aria-label", "Back to the current hour");
    this.nowButton.addEventListener("click", () => {
      this.pause();
      clock.setHour(null);
    });

    this.track = el("div", "timebar-track");
    this.track.tabIndex = 0;
    this.track.setAttribute("role", "slider");
    this.track.setAttribute("aria-label", "Forecast hour");
    this.layers = el("div", "timebar-layers");
    this.nowMark = el("div", "tb-nowmark");
    this.thumb = el("div", "tb-thumb");
    this.thumb.append(el("span", "tb-knob"));
    this.track.append(this.layers, this.nowMark, this.thumb);
    this.wireTrack();

    // Flat children on a grid: two rows normally, one row (no readout) when compact.
    this.element.append(this.playButton, read, this.nowButton, this.track);
    // Nothing on the bar should reach the map or the sheet underneath.
    for (const type of ["pointerdown", "touchstart", "wheel", "dblclick"] as const) {
      this.element.addEventListener(type, (e) => e.stopPropagation(), { passive: true });
    }
    parent.append(this.element);

    clock.subscribe((s) => {
      this.state = s;
      this.paintPosition();
    });
    this.paintPlay();
  }

  /** Shows the bar for a water body's forecast; `readout` names the wind at an hour. */
  show(forecast: LakeForecast, readout: (h: number) => TimeBarReadout): void {
    this.pause();
    this.forecast = forecast;
    this.readout = readout;
    this.paintAxis();
    this.paintPosition();
    this.applyVisible();
    if (this.liveTimer == null) {
      this.liveTimer = window.setInterval(() => {
        // The current hour moves on: redraw the past shading and a live thumb.
        if (this.forecast) {
          this.paintAxis();
          this.paintPosition();
        }
      }, 60_000);
    }
  }

  hide(): void {
    this.pause();
    this.forecast = null;
    this.readout = null;
    if (this.liveTimer != null) window.clearInterval(this.liveTimer);
    this.liveTimer = null;
    this.applyVisible();
  }

  /** The readout's subject changed (a region was picked); the hour did not. */
  refresh(): void {
    this.paintPosition();
  }

  /**
   * Where the bar sits. On a phone it rides just above the sheet and steps aside while the
   * sheet is at full; beside the iPad panel it sits at the bottom of the map.
   */
  place(opts: { panel: boolean; sheetPx: number; covered: boolean; compact: boolean }): void {
    this.element.classList.toggle("is-panel", opts.panel);
    // Over a half sheet the readout is in the Water section right below: one row is enough.
    this.element.classList.toggle("is-compact", opts.compact);
    // Compact over a half sheet: tucked right onto the sheet's edge.
    this.element.style.bottom = opts.panel ? "" : `${Math.round(opts.sheetPx + (opts.compact ? 4 : 8))}px`;
    this.suppressed = opts.covered;
    if (opts.covered) this.pause();
    this.applyVisible();
  }

  get visible(): boolean {
    return !this.element.hidden;
  }

  private applyVisible(): void {
    this.element.hidden = this.forecast == null || this.suppressed;
  }

  // -- playing ---------------------------------------------------------------

  private play(): void {
    const f = this.forecast;
    if (!f) return;
    let i = this.index();
    // From the end, play again from now.
    if (i >= f.length - 1) {
      i = f.liveIndex();
      clock.setHour(f.times[i] ?? null);
    }
    this.playTimer = window.setInterval(() => {
      const next = this.index() + 1;
      if (!this.forecast || next >= this.forecast.length) {
        this.pause();
        return;
      }
      clock.setHour(this.forecast.times[next] ?? null);
      if (next >= this.forecast.length - 1) this.pause();
    }, PLAY_MS);
    this.paintPlay();
  }

  private pause(): void {
    if (this.playTimer != null) window.clearInterval(this.playTimer);
    this.playTimer = null;
    this.paintPlay();
  }

  private paintPlay(): void {
    const playing = this.playTimer != null;
    this.playButton.innerHTML = playing
      ? '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>'
      : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5 L19 12 L8 19 Z"/></svg>';
    this.playButton.setAttribute("aria-label", playing ? "Pause" : "Play the forecast hour by hour");
    this.playButton.setAttribute("aria-pressed", String(playing));
  }

  // -- the track -------------------------------------------------------------

  private index(): number {
    return this.forecast ? this.forecast.index(this.state.hour) : -1;
  }

  private indexAt(clientX: number): number {
    const f = this.forecast;
    if (!f) return -1;
    const box = this.track.getBoundingClientRect();
    const x = Math.min(Math.max(clientX - box.left, 0), box.width - 0.001);
    return Math.min(f.length - 1, Math.floor((x / box.width) * f.length));
  }

  private go(i: number): void {
    const f = this.forecast;
    if (!f) return;
    const j = Math.min(Math.max(i, 0), f.length - 1);
    clock.setHour(f.times[j] ?? null);
  }

  private wireTrack(): void {
    this.track.addEventListener("pointerdown", (e) => {
      if (!this.forecast) return;
      this.pause();
      this.dragging = true;
      this.track.setPointerCapture(e.pointerId);
      this.track.classList.add("is-dragging");
      e.preventDefault();
      this.go(this.indexAt(e.clientX));
    });
    this.track.addEventListener("pointermove", (e) => {
      if (!this.dragging || !this.track.hasPointerCapture(e.pointerId)) return;
      e.preventDefault();
      const i = this.indexAt(e.clientX);
      if (i !== this.index() || this.state.manual) this.go(i);
    });
    const end = (e: PointerEvent): void => {
      if (!this.dragging) return;
      this.dragging = false;
      this.track.classList.remove("is-dragging");
      if (this.track.hasPointerCapture(e.pointerId)) this.track.releasePointerCapture(e.pointerId);
    };
    this.track.addEventListener("pointerup", end);
    this.track.addEventListener("pointercancel", end);
    this.track.addEventListener("keydown", (e) => {
      const i = this.index();
      const step: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, ArrowDown: -1, ArrowUp: 1, PageDown: -6, PageUp: 6 };
      if (e.key in step) {
        e.preventDefault();
        this.pause();
        this.go(i + step[e.key]!);
      } else if (e.key === "Home") {
        e.preventDefault();
        this.go(0);
      } else if (e.key === "End") {
        e.preventDefault();
        this.go(Number.MAX_SAFE_INTEGER);
      }
    });
  }

  /** Night, past, windows, day labels: redrawn per water body and as the hour turns. */
  private paintAxis(): void {
    const f = this.forecast;
    this.layers.replaceChildren();
    if (!f) return;
    const n = f.length;
    const pct = (i: number) => `${(i / n) * 100}%`;
    const span = (cls: string, from: number, to: number): HTMLElement => {
      const s = el("div", cls);
      s.style.left = pct(from);
      s.style.width = `${((to - from) / n) * 100}%`;
      return s;
    };

    // Night: the briefing's daylight (home airport) where it has the hour, else civil
    // twilight computed for this water body.
    const tl = this.timeline();
    const briefDay = new Map<number, boolean>();
    for (const h of tl?.hours ?? []) briefDay.set(Date.parse(h.t), h.daylight);
    const night: boolean[] = f.times.map((t) => {
      const ms = Date.parse(t);
      const known = briefDay.get(ms);
      return known != null ? !known : !isDaylight(ms + 1_800_000, f.centroid.lat, f.centroid.lon);
    });
    const band = el("div", "tb-band");
    this.layers.append(band);
    for (let i = 0; i < n; ) {
      if (!night[i]) {
        i++;
        continue;
      }
      let j = i;
      while (j < n && night[j]) j++;
      band.append(span("tb-night", i, j));
      i = j;
    }

    // The past: hatched and dimmed, up to the current hour.
    const live = f.liveIndex();
    if (live > 0) band.append(span("tb-past", 0, live));
    this.nowMark.style.left = pct(live);

    // Hour ticks every 3 h, longer at noon; a full-height line and the day name at midnight.
    for (let i = 0; i < n; i++) {
      const w = wall(f.times[i]!);
      if (w.hour === 0) {
        const line = el("div", "tb-midnight");
        line.style.left = pct(i);
        this.layers.append(line);
      } else if (w.hour % 3 === 0) {
        const tick = el("div", `tb-tick${w.hour === 12 ? " is-noon" : ""}`);
        tick.style.left = pct(i);
        this.layers.append(tick);
      }
    }
    // Day names centred on the part of each day that is on the axis.
    let start = 0;
    for (let i = 1; i <= n; i++) {
      if (i === n || wall(f.times[i]!).date !== wall(f.times[start]!).date) {
        if (i - start >= 5) {
          const label = el("div", "tb-day", wall(f.times[start]!).day);
          label.style.left = pct((start + i) / 2);
          this.layers.append(label);
        }
        start = i;
      }
    }

    // Windows from the briefing, as bars under the band: solid for favorable, dashed for
    // marginal, so the difference is shape and not colour.
    const first = Date.parse(f.times[0]!);
    // They are the home water's (and airport's) windows: drawn only on that water body.
    const windows = tl?.home_water?.id === f.lakeId ? tl.windows : [];
    for (const w of windows ?? []) {
      const a = (Date.parse(w.start) - first) / 3_600_000;
      const b = (Date.parse(w.end) - first) / 3_600_000;
      if (!Number.isFinite(a) || !Number.isFinite(b) || b <= 0 || a >= n) continue;
      const bar = span(`tb-window tb-window--${w.score}`, Math.max(0, a), Math.min(n, b));
      bar.title = `${w.score} ${hourLabel(w.start)} to ${wall(w.end).hhmm}`;
      this.layers.append(bar);
    }
  }

  private paintPosition(): void {
    const f = this.forecast;
    if (!f) return;
    const i = this.index();
    const n = f.length;
    this.thumb.style.left = `${((i + 0.5) / n) * 100}%`;
    const t = f.times[i] ?? "";
    const live = this.state.hour == null;
    const past = i < f.past || i < f.liveIndex();
    this.element.classList.toggle("is-manual", this.state.manual);
    this.element.classList.toggle("is-past", past);
    this.nowButton.disabled = live && !this.state.manual;

    const r = this.readout?.(i) ?? { wind: null, where: null };
    const when = past ? "past" : live ? "now" : "forecast";
    const wind = r.wind ? windText(r.wind) : "no wind";
    if (this.state.manual) {
      this.readTime.textContent = `${hourLabel(t)} · what if`;
      this.readWind.textContent = "Dial wind on every region";
    } else {
      this.readTime.textContent = `${hourLabel(t)} · ${when} · ${wind}`;
      this.readWind.textContent = r.where ? `at ${r.where}` : "at the centre · each region its own wind";
    }
    this.track.setAttribute("aria-valuemin", "0");
    this.track.setAttribute("aria-valuemax", String(n - 1));
    this.track.setAttribute("aria-valuenow", String(i));
    this.track.setAttribute("aria-valuetext", `${hourLabel(t)}, ${when}, wind ${wind}`);
  }
}
