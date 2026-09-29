import { WAVES } from "../config";
import { el } from "../ui/format";
import {
  BIN_DEGREES,
  binOf,
  clampKt,
  compassName,
  sameWind,
  snapToBin,
  type ForecastWind,
  type WindSetting,
} from "./wind";

/**
 * The wind you are asking about: a 16-point direction dial and a speed slider.
 *
 * Both are sized for a thumb in a cockpit: every button is at least 44 px, the dial itself
 * is a 148 px target that can be tapped anywhere on it, and the slider has −/+ buttons
 * beside it so a bumpy approach does not cost you ten knots. Direction snaps to the 16 bins
 * the wave field is computed in, because anything finer would be a lie: `fetch[16]` has no
 * more resolution than that.
 */

export interface WindControlOptions {
  value: WindSetting;
  /** The briefing's wind for this water body, if there is one; enables the reset button. */
  forecast: ForecastWind | null;
  onChange: (wind: WindSetting) => void;
}

const DIAL = 148;
const CENTER = DIAL / 2;
const RIM = CENTER - 13;
/** The arrow lives in the outer ring, clear of the reading in the middle. */
const ARROW_TAIL = RIM - 3;
const ARROW_TIP = 34;

export class WindControl {
  readonly element: HTMLElement;
  private value: WindSetting;
  private forecast: ForecastWind | null;
  /** Replaces the "gust" note under the speed while set (the forecast timeline's own words). */
  private note: string | null = null;
  private readonly onChange: (wind: WindSetting) => void;

  private dial: HTMLElement;
  private ticks: SVGLineElement[] = [];
  private arrow: SVGGElement;
  private degText: HTMLElement;
  private nameText: HTMLElement;
  private speed: HTMLInputElement;
  private speedText: HTMLElement;
  private reset: HTMLButtonElement;

  constructor(opts: WindControlOptions) {
    this.value = { ...opts.value };
    this.forecast = opts.forecast ? { ...opts.forecast } : null;
    this.onChange = opts.onChange;

    this.element = el("div", "wind-control");

    // -- direction ---------------------------------------------------------
    const dirRow = el("div", "wind-row wind-row--dial");
    dirRow.append(this.stepButton("−22°", "Turn the wind 22 degrees anticlockwise", () => this.stepDir(-1)));

    const { node, ticks, arrow } = buildDial();
    this.dial = el("div", "wind-dial");
    this.dial.setAttribute("role", "slider");
    this.dial.setAttribute("aria-label", "Wind direction");
    this.dial.setAttribute("aria-valuemin", "0");
    this.dial.setAttribute("aria-valuemax", "359");
    this.dial.tabIndex = 0;
    this.ticks = ticks;
    this.arrow = arrow;
    this.dial.append(node);

    const read = el("div", "wind-read");
    this.degText = el("span", "wind-deg");
    this.nameText = el("span", "wind-name");
    read.append(this.degText, this.nameText);
    this.dial.append(read);

    this.wireDial();
    dirRow.append(this.dial);
    dirRow.append(this.stepButton("+22°", "Turn the wind 22 degrees clockwise", () => this.stepDir(1)));
    this.element.append(dirRow);

    // -- speed -------------------------------------------------------------
    const speedRow = el("div", "wind-row wind-row--speed");
    speedRow.append(this.stepButton("−", "One knot less", () => this.setKt(this.value.kt - 1)));

    this.speed = document.createElement("input");
    this.speed.type = "range";
    this.speed.className = "wind-slider";
    this.speed.min = "0";
    this.speed.max = String(WAVES.maxWindKt);
    this.speed.step = "1";
    this.speed.setAttribute("aria-label", "Wind speed in knots");
    this.speed.addEventListener("input", () => this.setKt(Number(this.speed.value)));
    speedRow.append(this.speed);

    speedRow.append(this.stepButton("+", "One knot more", () => this.setKt(this.value.kt + 1)));
    this.speedText = el("span", "wind-kt");
    speedRow.append(this.speedText);
    this.element.append(speedRow);

    // -- back to the briefing's wind (only without a forecast timeline) ----
    const reset = el("button", "btn btn-quiet wind-reset");
    reset.type = "button";
    reset.addEventListener("click", () => {
      if (this.forecast) this.set(this.forecast, true);
    });
    this.reset = reset;
    this.element.append(reset);

    this.paint();
  }

  /**
   * The briefing wind the reset button returns to; null hides the button. The Water section
   * clears it once the forecast timeline takes over, whose own "Back to forecast" replaces it.
   */
  setReference(forecast: ForecastWind | null): void {
    this.forecast = forecast ? { ...forecast } : null;
    this.paint();
  }

  /** A word under the speed ("gust"), or null for the default. */
  setNote(note: string | null): void {
    if (note === this.note) return;
    this.note = note;
    this.paint();
  }

  get wind(): WindSetting {
    return { ...this.value };
  }

  /** True while the speed on the dial is the briefing's gust rather than a chosen value. */
  get atForecastGust(): boolean {
    return this.forecast != null && this.forecast.isGust && this.value.kt === this.forecast.kt;
  }

  /** Sets the control without notifying, unless `notify` is true. */
  set(wind: WindSetting, notify = false): void {
    const next = { dir: snapToBin(wind.dir), kt: clampKt(wind.kt) };
    if (sameWind(next, this.value)) return;
    this.value = next;
    this.paint();
    if (notify) this.onChange(this.wind);
  }

  private stepDir(delta: number): void {
    const bin = (binOf(this.value.dir) + delta + 16) % 16;
    this.set({ dir: BIN_DEGREES[bin]!, kt: this.value.kt }, true);
  }

  private setKt(kt: number): void {
    const next = clampKt(kt);
    if (next === this.value.kt) return;
    this.value = { ...this.value, kt: next };
    this.paint();
    this.onChange(this.wind);
  }

  private stepButton(text: string, label: string, onTap: () => void): HTMLButtonElement {
    const btn = el("button", "wind-step", text);
    btn.type = "button";
    btn.setAttribute("aria-label", label);
    btn.addEventListener("click", onTap);
    return btn;
  }

  /** Tap or drag anywhere on the dial; the nearest of the 16 bins wins. */
  private wireDial(): void {
    const fromEvent = (e: PointerEvent): void => {
      const box = this.dial.getBoundingClientRect();
      const dx = e.clientX - (box.left + box.width / 2);
      const dy = e.clientY - (box.top + box.height / 2);
      if (Math.hypot(dx, dy) < 6) return; // dead centre carries no bearing
      const deg = (Math.atan2(dx, -dy) * 180) / Math.PI;
      this.set({ dir: snapToBin(deg), kt: this.value.kt }, true);
    };

    this.dial.addEventListener("pointerdown", (e) => {
      this.dial.setPointerCapture(e.pointerId);
      e.preventDefault();
      fromEvent(e);
    });
    this.dial.addEventListener("pointermove", (e) => {
      if (!this.dial.hasPointerCapture(e.pointerId)) return;
      e.preventDefault();
      fromEvent(e);
    });
    this.dial.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft" || e.key === "ArrowDown") {
        e.preventDefault();
        this.stepDir(-1);
      } else if (e.key === "ArrowRight" || e.key === "ArrowUp") {
        e.preventDefault();
        this.stepDir(1);
      }
    });
  }

  private paint(): void {
    const bin = binOf(this.value.dir);
    this.degText.textContent = `${String(this.value.dir).padStart(3, "0")}°`;
    this.nameText.textContent = compassName(this.value.dir);
    this.dial.setAttribute("aria-valuenow", String(this.value.dir));
    this.dial.setAttribute(
      "aria-valuetext",
      `wind from ${this.value.dir} degrees, ${compassName(this.value.dir)}`,
    );
    this.ticks.forEach((tick, i) => {
      tick.classList.toggle("is-on", i === bin);
    });
    this.arrow.setAttribute("transform", `rotate(${this.value.dir} ${CENTER} ${CENTER})`);

    this.speed.value = String(this.value.kt);
    this.speedText.replaceChildren(el("span", "wind-kt-value", `${this.value.kt} kt`));
    // The briefing scores waves at the gust; say so while the control is still on it, so
    // the sheet and the ranked row are obviously the same number.
    const note = this.note ?? (this.atForecastGust ? "gust" : null);
    if (note) {
      this.speedText.append(el("span", "wind-kt-note", note));
    }

    this.reset.hidden = this.forecast == null;
    if (this.forecast) {
      const atForecast = sameWind(this.value, this.forecast);
      this.reset.disabled = atForecast;
      this.reset.textContent = atForecast
        ? `Forecast wind: ${String(this.forecast.dir).padStart(3, "0")}/${this.forecast.kt}`
        : `Back to the forecast (${String(this.forecast.dir).padStart(3, "0")}/${this.forecast.kt})`;
    }
  }
}

const SVG_NS = "http://www.w3.org/2000/svg";

/**
 * 16 ticks and an arrow that points the way the wind is going, from the rim it comes from
 * toward the middle. Drawn once; changing the wind only moves one `transform` and one class.
 */
function buildDial(): { node: SVGSVGElement; ticks: SVGLineElement[]; arrow: SVGGElement } {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${DIAL} ${DIAL}`);
  svg.setAttribute("class", "wind-dial-svg");
  svg.setAttribute("aria-hidden", "true");

  const ring = document.createElementNS(SVG_NS, "circle");
  ring.setAttribute("cx", String(CENTER));
  ring.setAttribute("cy", String(CENTER));
  ring.setAttribute("r", String(RIM));
  ring.setAttribute("class", "wind-dial-ring");
  svg.append(ring);

  const ticks: SVGLineElement[] = [];
  for (let i = 0; i < 16; i++) {
    const angle = (i * 22.5 * Math.PI) / 180;
    const cardinal = i % 4 === 0;
    const inner = RIM - (cardinal ? 12 : 7);
    const line = document.createElementNS(SVG_NS, "line");
    line.setAttribute("x1", String(CENTER + Math.sin(angle) * RIM));
    line.setAttribute("y1", String(CENTER - Math.cos(angle) * RIM));
    line.setAttribute("x2", String(CENTER + Math.sin(angle) * inner));
    line.setAttribute("y2", String(CENTER - Math.cos(angle) * inner));
    line.setAttribute("class", `wind-tick${cardinal ? " is-cardinal" : ""}`);
    svg.append(line);
    ticks.push(line);
  }

  // Drawn pointing from the north rim inward, then rotated to the wind direction.
  const arrow = document.createElementNS(SVG_NS, "g");
  arrow.setAttribute("class", "wind-arrow");
  const shaft = document.createElementNS(SVG_NS, "line");
  shaft.setAttribute("x1", String(CENTER));
  shaft.setAttribute("y1", String(CENTER - ARROW_TAIL));
  shaft.setAttribute("x2", String(CENTER));
  shaft.setAttribute("y2", String(CENTER - ARROW_TIP - 12));
  arrow.append(shaft);
  const head = document.createElementNS(SVG_NS, "path");
  head.setAttribute(
    "d",
    `M ${CENTER} ${CENTER - ARROW_TIP} L ${CENTER - 8} ${CENTER - ARROW_TIP - 15} ` +
      `L ${CENTER + 8} ${CENTER - ARROW_TIP - 15} Z`,
  );
  head.setAttribute("class", "wind-arrow-head");
  arrow.append(head);
  svg.append(arrow);

  return { node: svg, ticks, arrow };
}
