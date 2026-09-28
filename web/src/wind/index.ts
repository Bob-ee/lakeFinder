import type { MapController } from "../map";
import type { Lake } from "../types";
import { toggleRow } from "../ui/controls";
import { el } from "../ui/format";
import type { ResolvedTheme } from "../ui/theme";
import { WindData } from "./data";
import { shapeSvg } from "./format";
import { WindLayer } from "./layer";
import { WindSection } from "./section";

export { WindData } from "./data";

/**
 * Flight-mode wind, wired up in one place: the data cache, the map layer, its row in the
 * layers panel (with a shape key, since shape is the only thing that tells sources apart)
 * and the per-lake sheet block.
 */
export class Wind {
  readonly data = new WindData();
  readonly layer: WindLayer;
  private section: WindSection | null = null;

  constructor(map: MapController, theme: () => ResolvedTheme) {
    this.layer = new WindLayer(map, this.data, theme);
  }

  /** The row for the layers panel: toggle plus the shape key. */
  layerRow(): HTMLElement {
    const wrap = el("div", "wind-layer-row");
    wrap.append(
      toggleRow({
        label: "Wind stations",
        hint: "zoom 8+, online only",
        checked: this.layer.enabled,
        disabled: false,
        onChange: (on) => this.layer.setEnabled(on),
      }),
    );
    const key = el("div", "wind-key");
    for (const [shape, word] of [
      ["metar", "METAR"],
      ["buoy", "Buoy"],
      ["mesonet", "Mesonet"],
    ] as const) {
      const item = el("span", "wind-key-item");
      const g = el("span", "wind-key-glyph");
      g.innerHTML = shapeSvg(shape, 45);
      item.append(g, word);
      key.append(item);
    }
    wrap.append(key);
    return wrap;
  }

  /**
   * Mounts the Wind block for a new selection (before `beforeEl`, when given) and the peek
   * line inside the peek row. Null clears both.
   */
  select(lake: Lake | null, detail: HTMLElement | null, peek: HTMLElement | null, beforeEl?: HTMLElement): void {
    this.section?.dispose();
    this.section = null;
    if (!lake || !detail || !peek) return;
    const line = el("div", "peek-wind");
    const text = peek.querySelector(".peek-text") ?? peek;
    text.append(line);
    this.section = new WindSection(lake, this.data, line);
    if (beforeEl && beforeEl.parentElement === detail) detail.insertBefore(this.section.element, beforeEl);
    else detail.append(this.section.element);
  }
}
