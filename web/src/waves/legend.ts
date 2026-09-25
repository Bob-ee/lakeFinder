import { el } from "../ui/format";
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
 */
export class WaveLegend {
  readonly element: HTMLElement;
  private bands: HTMLElement;
  private arrow: HTMLElement;
  private windText: HTMLElement;

  constructor(parent: HTMLElement) {
    this.element = el("div", "wave-key");
    this.element.hidden = true;
    this.element.setAttribute("aria-hidden", "true");

    this.element.append(el("div", "wave-key-title", "Wave height"));

    this.bands = el("div", "wave-key-bands");
    this.element.append(this.bands);

    const windRow = el("div", "wave-key-wind");
    this.arrow = el("span", "wave-key-arrow");
    this.arrow.innerHTML =
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 L12 21 M12 21 L7 14 M12 21 L17 14" ' +
      'fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    this.windText = el("span", "wave-key-windtext");
    windRow.append(this.arrow, this.windText);
    this.element.append(windRow);

    parent.append(this.element);
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
