/**
 * The forecast clock: which hour the app is showing (docs/data-contract.md, "Forecast
 * timeline and waves over time"). `null` means live, the current hour. The Briefing chart,
 * the map time bar and the Water section all read and set this one value.
 *
 * `manual` is the wind dial's "what if" mode: while it is set, the Water section ignores the
 * clock and uses the dial's wind; `backToForecast()` clears it.
 */

/** Local ISO 8601 with offset, exactly as the api writes it on the time axis. */
export type HourIso = string;

export interface ClockState {
  hour: HourIso | null;
  manual: boolean;
}

type ClockListener = (state: ClockState) => void;

class ForecastClock {
  private state: ClockState = { hour: null, manual: false };
  private listeners = new Set<ClockListener>();

  get current(): ClockState {
    return this.state;
  }

  /** Show `hour` (null = live). Leaves manual wind mode. */
  setHour(hour: HourIso | null): void {
    if (hour === this.state.hour && !this.state.manual) return;
    this.state = { hour, manual: false };
    this.emit();
  }

  /** The wind dial was touched. */
  setManual(): void {
    if (this.state.manual) return;
    this.state = { ...this.state, manual: true };
    this.emit();
  }

  backToForecast(): void {
    if (!this.state.manual) return;
    this.state = { ...this.state, manual: false };
    this.emit();
  }

  /** Calls `fn` now and on every change; returns an unsubscribe. */
  subscribe(fn: ClockListener): () => void {
    this.listeners.add(fn);
    fn(this.state);
    return () => this.listeners.delete(fn);
  }

  private emit(): void {
    for (const fn of this.listeners) fn(this.state);
  }
}

export const clock = new ForecastClock();

/**
 * Index of `hour` on an axis of hour strings. Compares instants, not strings, so an axis
 * written across a DST change still matches. `null` (live) resolves to the hour containing now.
 */
export function hourIndex(axis: readonly HourIso[], hour: HourIso | null, now: Date = new Date()): number {
  const target = hour == null ? now.getTime() : Date.parse(hour);
  for (let i = axis.length - 1; i >= 0; i--) {
    if (Date.parse(axis[i]!) <= target) return i;
  }
  return axis.length > 0 ? 0 : -1;
}
