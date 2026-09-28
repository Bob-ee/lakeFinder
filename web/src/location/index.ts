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

export type LocationStatus = "pending" | "prompt" | "active" | "denied" | "unavailable" | "unsupported";

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
      return deniedHelp();
    case "prompt":
      return "Tap the recenter button to show your position.";
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

/**
 * Where the switch is. A denial usually arrives with no prompt at all: iOS refuses silently when
 * Location Services › Safari Websites is "Never", and Chrome silently blocks a site after a
 * dismissed or blocked prompt.
 */
function deniedHelp(): string {
  const ua = navigator.userAgent;
  const ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
  if (ios) {
    return "Location is off for this app. Settings › Privacy & Security › Location Services: turn it on, and set Safari Websites to While Using. Then tap recenter.";
  }
  if (/Android/.test(ua)) {
    return "Location is blocked for this site. In Chrome tap the icon left of the address › Permissions › Location › Allow (installed app: long-press its icon › App info › Permissions). Also check Chrome has Location in Android settings. Then tap recenter.";
  }
  return "Location is blocked for this site. Allow it in the browser's site settings, then tap recenter.";
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
    // Ask at launch only when the answer is already yes. A prompt fired at launch, while the map is
    // still coming up, is easy to dismiss, and a dismissal reads as a denial for the whole session;
    // otherwise the recenter tap asks (a user gesture, so the browser shows its prompt).
    const perms = navigator.permissions;
    if (!perms?.query) {
      this.watch();
      return;
    }
    perms
      .query({ name: "geolocation" })
      .then((p) => {
        p.addEventListener("change", () => {
          if (p.state === "granted") this.watch();
          else if (p.state === "denied") this.onDenied();
        });
        if (p.state === "granted") this.watch();
        else if (p.state === "denied") this.setStatus("denied");
        else this.setStatus("prompt");
      })
      .catch(() => this.watch());
  }

  /**
   * Ask now, from a user gesture (the recenter button). Restarts the watch even after a denial:
   * the user may have just changed the setting, and a gesture lets the browser prompt again.
   */
  request(): void {
    if (typeof navigator === "undefined" || !("geolocation" in navigator)) return;
    if (this.statusValue === "active" && this.watchId != null) return;
    this.stop();
    this.watch();
  }

  private watch(): void {
    if (this.watchId != null) return;
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
      this.onDenied();
    } else if (err.code === err.POSITION_UNAVAILABLE) {
      // A Wi-Fi iPad lands here; the watch stays open in case an external GPS appears.
      if (!this.fixValue) this.setStatus("unavailable");
    } else if (!this.fixValue) {
      // Timeout before any fix: say so, keep watching.
      this.setStatus("unavailable");
    }
  }

  private onDenied(): void {
    this.fixValue = null;
    this.stop();
    this.setStatus("denied");
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
