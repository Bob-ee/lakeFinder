/**
 * Location (design.md 7.7, data-contract "Flight mode / Location"): one `watchPosition`
 * with high accuracy, turned into the contract's fix shape and fanned out to the follow-me
 * camera, the own-position marker and the Nearest list.
 *
 * Dev builds also honour `?fakefix=lat,lon,speed_kt,course` (see fake.ts). The check sits
 * behind `import.meta.env.DEV`, which Vite replaces with `false` in a production build, so
 * the branch and the fake module are both dropped from the bundle.
 */
import { MS_TO_KT } from "./geo";

export interface Fix {
  lat: number;
  lon: number;
  accuracy_m: number;
  speed_kt: number | null;
  /** True course over ground; only meaningful when `speed_kt >= COURSE_MIN_KT` (usableCourse). */
  course_deg: number | null;
  /** ISO 8601. */
  time: string;
}

export type LocationStatus = "pending" | "active" | "denied" | "unavailable" | "unsupported";

/** GPS course is noise when stopped. */
export const COURSE_MIN_KT = 3;

/** The course if it can be trusted, else null. */
export function usableCourse(fix: Fix | null): number | null {
  if (!fix || fix.course_deg == null || fix.speed_kt == null) return null;
  return fix.speed_kt >= COURSE_MIN_KT ? fix.course_deg : null;
}

/** The one-line "why" shown when there is no position (contract: denied / no GPS path). */
export function statusNote(status: LocationStatus): string | null {
  switch (status) {
    case "denied":
      return "Location permission is off for this app.";
    case "unavailable":
      return "This device is not reporting a position (no GPS).";
    case "unsupported":
      return "This browser has no location service.";
    case "pending":
      return "Waiting for a position.";
    default:
      return null;
  }
}

type Listener = (fix: Fix | null, status: LocationStatus) => void;

export class LocationService {
  private fixValue: Fix | null = null;
  private statusValue: LocationStatus = "pending";
  private listeners = new Set<Listener>();
  private watchId: number | null = null;

  get fix(): Fix | null {
    return this.fixValue;
  }

  get status(): LocationStatus {
    return this.statusValue;
  }

  onChange(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  start(): void {
    if (import.meta.env.DEV) {
      const raw = readFakeParam();
      if (raw) {
        void import("./fake").then((m) => m.startFakeFix(raw, (fix) => this.push(fix)));
        return;
      }
    }
    if (typeof navigator === "undefined" || !("geolocation" in navigator)) {
      this.setStatus("unsupported");
      return;
    }
    this.watchId = navigator.geolocation.watchPosition(
      (pos) => this.push(toFix(pos)),
      (err) => this.onError(err),
      { enableHighAccuracy: true, maximumAge: 2000, timeout: 30_000 },
    );
  }

  stop(): void {
    if (this.watchId != null) navigator.geolocation.clearWatch(this.watchId);
    this.watchId = null;
  }

  private push(fix: Fix): void {
    this.fixValue = fix;
    this.statusValue = "active";
    this.emit();
  }

  private onError(err: GeolocationPositionError): void {
    if (err.code === err.PERMISSION_DENIED) {
      this.fixValue = null;
      this.setStatus("denied");
      this.stop();
    } else if (err.code === err.POSITION_UNAVAILABLE) {
      // A Wi-Fi iPad lands here; the watch stays open in case an external GPS appears.
      if (!this.fixValue) this.setStatus("unavailable");
    } else if (!this.fixValue) {
      // Timeout before any fix: say so, keep watching.
      this.setStatus("unavailable");
    }
  }

  private setStatus(status: LocationStatus): void {
    this.statusValue = status;
    this.emit();
  }

  private emit(): void {
    for (const fn of this.listeners) fn(this.fixValue, this.statusValue);
  }
}

function toFix(pos: GeolocationPosition): Fix {
  const c = pos.coords;
  const speed = c.speed != null && Number.isFinite(c.speed) ? c.speed * MS_TO_KT : null;
  const heading = c.heading != null && Number.isFinite(c.heading) ? c.heading : null;
  return {
    lat: c.latitude,
    lon: c.longitude,
    accuracy_m: c.accuracy,
    speed_kt: speed,
    course_deg: heading,
    time: new Date(pos.timestamp).toISOString(),
  };
}

function readFakeParam(): string | null {
  try {
    return new URL(window.location.href).searchParams.get("fakefix");
  } catch {
    return null;
  }
}
