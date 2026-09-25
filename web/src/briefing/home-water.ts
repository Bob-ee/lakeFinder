import { getHealth, getSettings, putSettings } from "./api";
import type { HomeWater, Settings } from "./types";
import type { WaveLimits } from "../waves/ramp";

/**
 * The one place the client reads and writes `settings.home_water`.
 *
 * It is set from a water body's sheet and cleared from the briefing settings dialog, so
 * two screens need the same answer and neither should re-read the whole settings file to
 * paint a button. The settings object is fetched once, kept, and rewritten whole on a
 * change, because the contract's `PUT /api/settings` takes the full object.
 *
 * Everything degrades to "the service is not reachable": no button, no error noise. A PUT
 * also triggers a briefing run on the service, which is exactly what changing home water
 * should do.
 */
export class HomeWaterStore {
  private pending: Promise<Settings | null> | null = null;
  /** undefined while the first probe is in flight; null when the service said nothing. */
  private state: HomeWater | null | undefined = undefined;
  private listeners = new Set<(water: HomeWater | null | undefined) => void>();
  private reachable: boolean | undefined = undefined;

  /** undefined = not known yet, null = none set or the service is unreachable. */
  get current(): HomeWater | null | undefined {
    return this.state;
  }

  /** undefined while probing; false once `/api/health` or the settings read failed. */
  get serviceUp(): boolean | undefined {
    return this.reachable;
  }

  onChange(fn: (water: HomeWater | null | undefined) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  /** Loads (once) and returns the settings object, or null when the service is down. */
  load(): Promise<Settings | null> {
    this.pending ??= this.fetchOnce();
    return this.pending;
  }

  /** Lets the settings dialog hand back the object it already fetched or saved. */
  adopt(settings: Settings): void {
    this.pending = Promise.resolve(settings);
    this.reachable = true;
    this.state = settings.home_water ?? null;
    this.emit();
  }

  /** Writes the full settings object with `home_water` set or cleared. */
  async set(water: HomeWater | null): Promise<void> {
    const settings = await this.load();
    if (!settings) throw new Error("Briefing service not reachable");
    const next: Settings = { ...settings, home_water: water };
    this.adopt(await putSettings(next));
  }

  private async fetchOnce(): Promise<Settings | null> {
    const health = await getHealth();
    if (!health) {
      this.reachable = false;
      this.state = null;
      this.emit();
      return null;
    }
    try {
      const settings = await getSettings();
      this.adopt(settings);
      return settings;
    } catch (err) {
      console.warn("[home water] settings could not be read", err);
      this.reachable = false;
      this.state = null;
      this.emit();
      return null;
    }
  }

  private emit(): void {
    for (const fn of this.listeners) fn(this.state);
  }
}

/** One store for the whole app: the sheet and the settings dialog share it. */
export const homeWater = new HomeWaterStore();

/**
 * `limits.min_run_ft` for the wave-field region list. The contract's fallback is 2,000 ft
 * when the service is not reachable; the caller passes that in so this module does not
 * need the config.
 */
/**
 * `limits.wave_ok_in` and `limits.wave_max_in`, which band the wave field on the map. Each
 * falls back on its own, so a half-filled settings file still bands sensibly.
 */
export async function waveLimits(fallback: WaveLimits): Promise<WaveLimits> {
  const settings = await homeWater.load();
  const ok = settings?.limits?.wave_ok_in;
  const max = settings?.limits?.wave_max_in;
  const valid = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0;
  return { okIn: valid(ok) ? ok : fallback.okIn, maxIn: valid(max) ? max : fallback.maxIn };
}

export async function minRunFt(fallback: number): Promise<number> {
  const settings = await homeWater.load();
  const value = settings?.limits?.min_run_ft;
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : fallback;
}
