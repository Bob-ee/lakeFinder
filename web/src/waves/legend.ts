import { STORAGE_KEYS } from "../config";
import { el } from "../ui/format";
import { icon } from "../ui/icons";
import { readLocal, writeLocal } from "../ui/theme";
import { WAVE_BANDS, bandText } from "./ramp";
import { compassName } from "./wind";
import { waveSwatch, type WaveState } from "./section";

/**
 * The key for the dots on the water, and the wind they are drawn for.
 *
 * It is DOM rather than map layers on purpose: a legend and a wind arrow never move with
 * the map, so putting them in a fixed card keeps them legible over any basemap and costs
 * the renderer nothing. The band edges are the pilot's own wave limits, written out as
 * numbers, next to the same marks the map draws. It sits on the left, under the briefing chip, clear of the
 * right-hand control stack and of the sheet.
 *
 * Minimized by default to a one-line chip ("Waves" and the band marks, shapes as well as
 * colours); the pilot's open/minimized choice is kept across selections and reloads. On a
 * phone with the sheet at half or full it shows as the chip whatever the choice, so it
 * never competes with the sheet for the top of the map.
 */
export class WaveLegend {
  readonly element: HTMLElement;
  private bands: HTMLElement;
  private arrow: HTMLElement;
  private windText: HTMLElement;
  private chip: HTMLButtonElement;
  private head: HTMLButtonElement;
  private open: boolean;
  private compact = false;

  constructor(parent: HTMLElement) {
    this.open = readLocal(STORAGE_KEYS.waveKey) === "open";
    this.element = el("div", "wave-key");
    this.element.hidden = true;

    // Minimized: one tap target, "Waves" plus the marks in miniature.
    this.chip = el("button", "wave-key-chip");
    this.chip.type = "button";
    this.chip.setAttribute("aria-expanded", "false");
    this.chip.setAttribute("aria-label", "Show the wave key");
    const marks = el("span", "wave-key-marks");
    for (const band of WAVE_BANDS.slice(0, 3)) marks.append(waveSwatch(band));
    const caret = el("span", "wave-key-caret");
    caret.innerHTML = icon("chevron");
    this.chip.append(el("span", "wave-key-chip-text", "Waves"), marks, caret);
    this.chip.addEventListener("click", () => this.setOpen(true));

    // Expanded: the header is the minimize control.
    this.head = el("button", "wave-key-head");
    this.head.type = "button";
    this.head.setAttribute("aria-expanded", "true");
    this.head.setAttribute("aria-label", "Minimize the wave key");
    const headCaret = el("span", "wave-key-caret is-open");
    headCaret.innerHTML = icon("chevron");
    this.head.append(el("span", "wave-key-title", "Wave height"), headCaret);
    this.head.addEventListener("click", () => this.setOpen(false));

    const card = el("div", "wave-key-card");
    card.append(this.head);

    this.bands = el("div", "wave-key-bands");
    card.append(this.bands);

    const windRow = el("div", "wave-key-wind");
    this.arrow = el("span", "wave-key-arrow");
    this.arrow.innerHTML =
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 L12 21 M12 21 L7 14 M12 21 L17 14" ' +
      'fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    this.windText = el("span", "wave-key-windtext");
    windRow.append(this.arrow, this.windText);
    card.append(windRow);

    this.element.append(this.chip, card);
    parent.append(this.element);
    this.apply();
  }

  /** Phone sheet at half or full: show the chip only, without forgetting the choice. */
  setCompact(on: boolean): void {
    if (this.compact === on) return;
    this.compact = on;
    this.apply();
  }

  private setOpen(on: boolean): void {
    this.open = on;
    // Tapping the chip while compact is an explicit ask: honour it until the next change.
    this.compact = false;
    writeLocal(STORAGE_KEYS.waveKey, on ? "open" : "min");
    this.apply();
    (on ? this.head : this.chip).focus({ preventScroll: true });
  }

  private apply(): void {
    const expanded = this.open && !this.compact;
    this.element.classList.toggle("is-min", !expanded);
    this.chip.setAttribute("aria-expanded", String(expanded));
  }

  show(state: WaveState): void {
    this.bands.replaceChildren();
    for (const band of WAVE_BANDS) {
      const text = bandText(band, state.limits);
      if (text == null) continue;
      const row = el("div", "wave-key-band");
      row.append(waveSwatch(band), el("span", "wave-key-band-text", text));
      this.bands.append(row);
    }
    const { dir, kt } = state.wind;
    // The arrow points the way the wind blows, so it reads as an arrow over the water
    // rather than as a compass needle: wind FROM 270 blows toward 090, i.e. to the right.
    this.arrow.style.transform = `rotate(${dir}deg)`;
    this.windText.textContent = `${String(dir).padStart(3, "0")}° ${compassName(dir)} · ${kt} kt`;
    this.element.hidden = false;
  }

  hide(): void {
    this.element.hidden = true;
  }
}
