import type { Timeline } from "../briefing/types";
import type { Sheet } from "../sheet";
import { clock } from "../state/clock";
import type { WaveField } from "../waves/load";
import type { WaterSection } from "../waves/section";
import { ForecastWindClient, devHourOffset } from "./api";
import { LakeForecast } from "./lake";
import { TimeBar } from "./timebar";

/**
 * Waves over time: fetches the selected water body's forecast wind, hands it to the Water
 * section (which then follows the forecast clock) and shows the map's time bar. With no
 * forecast (offline, api down, no wave field) none of this appears and the section keeps its
 * wind dial exactly as before.
 */
export class ForecastWaves {
  private client = new ForecastWindClient();
  private bar: TimeBar;
  private section: WaterSection | null = null;
  private devHourUsed = false;

  constructor(
    parent: HTMLElement,
    private readonly opts: {
      sheet: Sheet;
      timeline: () => Timeline | null | undefined;
      /** The bar's reserved height changed: the map re-frames with the new padding. */
      onLayout?: (deltaPx: number) => void;
    },
  ) {
    this.bar = new TimeBar(parent, opts.timeline);
    this.place();
  }

  /**
   * A Water section was mounted for a water body with a wave field. `alive` is false once the
   * pilot has moved on to another selection, so a late answer is dropped.
   */
  async attach(section: WaterSection, field: WaveField, alive: () => boolean): Promise<void> {
    this.detach();
    this.section = section;
    const data = await this.client.get(field.lakeId, field.points, field.labels);
    if (!alive() || this.section !== section || !data) return;
    const forecast = new LakeForecast(data, field.points, field.labels);
    if (forecast.isStale() || forecast.length === 0) return;

    section.setForecast(forecast);
    section.onFocusChange = () => this.bar.refresh();
    this.bar.show(forecast, (h) => section.readoutAt(h));
    const reserved = this.reservedPx;
    if (reserved > 0) this.opts.onLayout?.(reserved);

    const offset = devHourOffset();
    if (offset != null && !this.devHourUsed) {
      this.devHourUsed = true;
      const i = Math.min(Math.max(forecast.liveIndex() + offset, 0), forecast.length - 1);
      clock.setHour(forecast.times[i] ?? null);
    }
  }

  /** The selection changed or cleared: the old section stops following the clock. */
  detach(): void {
    this.section?.destroy();
    this.section = null;
    this.bar.hide();
  }

  /** Map pixels the bar takes from the bottom of the map (0 when hidden), for framing padding. */
  get reservedPx(): number {
    return this.bar.visible ? this.bar.element.offsetHeight + 8 : 0;
  }

  /** Call when the sheet moves: the bar rides above a phone sheet and hides under a full one. */
  place(): void {
    const { sheet } = this.opts;
    this.bar.place({
      panel: sheet.isPanel,
      sheetPx: sheet.heightPx,
      covered: !sheet.isPanel && sheet.current === "full",
      compact: !sheet.isPanel && sheet.current === "half",
    });
  }
}
