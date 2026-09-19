import { BRIEFING, DATA_FILES } from "../config";
import { db } from "../state/db";
import type { Briefing } from "./types";

export type BriefingState =
  /** First load has not finished yet. */
  | { kind: "loading" }
  /** A briefing is in hand. `fromCache` means the network copy could not be reached. */
  | { kind: "ok"; briefing: Briefing; fromCache: boolean }
  /** 404: the api service has not written briefing.json yet. Quiet, not an error. */
  | { kind: "empty" }
  /** The fetch failed and nothing is cached. Also quiet: no toast, just a line. */
  | { kind: "unavailable" };

type Listener = (state: BriefingState) => void;

/**
 * Loads `/data/briefing.json` (a static file; the client never reads the briefing through
 * `/api`), keeps the last good copy in IndexedDB so the card survives offline, and
 * refetches when the tab becomes visible and every ten minutes while it stays visible.
 *
 * A 404 is not an error: it means the api service has not produced a briefing yet, and the
 * card shows a quiet empty state. Only a transport failure or a 5xx falls back to the
 * stored copy, because in that case the file is presumed to still exist on the server.
 */
export class BriefingStore {
  private state: BriefingState = { kind: "loading" };
  private listeners = new Set<Listener>();
  private timer: number | null = null;
  private inFlight: Promise<void> | null = null;
  private started = false;

  get current(): BriefingState {
    return this.state;
  }

  /** The briefing currently on display, or null. */
  get briefing(): Briefing | null {
    return this.state.kind === "ok" ? this.state.briefing : null;
  }

  onChange(fn: Listener): () => void {
    this.listeners.add(fn);
    fn(this.state);
    return () => this.listeners.delete(fn);
  }

  /** Idempotent: first load plus the visibility and interval wiring. */
  start(): void {
    if (this.started) return;
    this.started = true;
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        void this.refresh();
        this.schedule();
      } else {
        this.unschedule();
      }
    });
    window.addEventListener("pagehide", () => this.unschedule());
    void this.refresh();
    if (document.visibilityState === "visible") this.schedule();
  }

  /** Fetches once. Concurrent calls share the in-flight request. */
  refresh(): Promise<void> {
    if (this.inFlight) return this.inFlight;
    this.inFlight = this.fetchOnce().finally(() => {
      this.inFlight = null;
    });
    return this.inFlight;
  }

  /**
   * Adopts a briefing handed over directly, which is what `POST /api/briefing/refresh`
   * returns, so the card updates without waiting for the static file to be re-read. The
   * body is validated like any other: a service that answers with something else must not
   * be able to replace a good briefing with a half-rendered one.
   */
  adopt(body: unknown): void {
    const briefing = asBriefing(body);
    if (!briefing) {
      console.warn("[briefing] refresh returned something that is not a briefing; refetching");
      void this.refresh();
      return;
    }
    this.set({ kind: "ok", briefing, fromCache: false });
    void this.store(briefing);
  }

  private async fetchOnce(): Promise<void> {
    let res: Response;
    try {
      res = await fetch(DATA_FILES.briefing, { cache: "no-cache" });
    } catch (err) {
      console.warn("[briefing] fetch failed, falling back to the stored copy", err);
      await this.fallback();
      return;
    }

    if (res.status === 404) {
      // No briefing yet. Deliberately not an error and deliberately not cached-over.
      this.set({ kind: "empty" });
      return;
    }
    if (!res.ok) {
      console.warn(`[briefing] /data/briefing.json returned HTTP ${res.status}`);
      await this.fallback();
      return;
    }

    let parsed: unknown;
    try {
      parsed = await res.json();
    } catch (err) {
      console.warn("[briefing] briefing.json did not parse", err);
      await this.fallback();
      return;
    }

    const briefing = asBriefing(parsed);
    if (!briefing) {
      console.warn("[briefing] briefing.json is missing required fields; ignoring it");
      await this.fallback();
      return;
    }
    this.set({ kind: "ok", briefing, fromCache: false });
    await this.store(briefing);
  }

  private async fallback(): Promise<void> {
    const cached = await this.read();
    if (cached) this.set({ kind: "ok", briefing: cached, fromCache: true });
    else this.set({ kind: "unavailable" });
  }

  private async read(): Promise<Briefing | null> {
    const handle = db();
    if (!handle) return null;
    try {
      const row = await (await handle).get("briefing", BRIEFING.cacheKey);
      return row ? asBriefing(row.payload) : null;
    } catch (err) {
      console.warn("[briefing] cache read failed", err);
      return null;
    }
  }

  private async store(briefing: Briefing): Promise<void> {
    const handle = db();
    if (!handle) return;
    try {
      await (await handle).put("briefing", {
        key: BRIEFING.cacheKey,
        stored_at: Date.now(),
        payload: briefing,
      });
    } catch (err) {
      console.warn("[briefing] cache write failed", err);
    }
  }

  private schedule(): void {
    this.unschedule();
    this.timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void this.refresh();
    }, BRIEFING.pollMs);
  }

  private unschedule(): void {
    if (this.timer != null) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  private set(state: BriefingState): void {
    this.state = state;
    for (const fn of this.listeners) fn(state);
  }
}

/**
 * Shallow structural check. The card reads a lot of optional-ish corners, so anything that
 * is not at least a briefing-shaped object is treated as no briefing at all rather than
 * throwing halfway through a render.
 */
function asBriefing(value: unknown): Briefing | null {
  if (typeof value !== "object" || value === null) return null;
  const v = value as Partial<Briefing>;
  if (typeof v.generated_at !== "string") return null;
  if (typeof v.timezone !== "string") return null;
  if (!Array.isArray(v.days)) return null;
  if (!Array.isArray(v.lakes)) return null;
  return value as Briefing;
}
