import type { MapLibreEvent, PaddingOptions } from "maplibre-gl";
import type { MapController } from "../map";
import { el } from "../ui/format";
import { readLocal, writeLocal } from "../ui/theme";
import { toast } from "../ui/toast";
import { type Fix, type LocationService, statusNote } from "./index";
import { OwnMarker } from "./marker";

/** Heading-up at or above this ground speed while following (contract: 10 kt). */
const HEADING_UP_KT = 10;
/** ...and back to north-up only below this, so a water taxi at 9-11 kt does not spin the map. */
const HEADING_UP_OFF_KT = 7;
/** Resuming follow-me from a state-wide view lands here, about 10 nm across a phone. */
const FOLLOW_MIN_ZOOM = 11;
/** In heading-up the fix sits this far down the visible map, so more of it is ahead. */
const HEADING_UP_LOOKAHEAD = 0.35;
const NORTH_UP_KEY = "seaplane.northUp";

export interface FollowDeps {
  map: MapController;
  location: LocationService;
  /** Same padding the selection uses: keeps the fix clear of the sheet or side panel. */
  padding: () => PaddingOptions;
  /** The right-edge control stack; the north-up toggle goes in under the recenter button. */
  controls: HTMLElement;
  /** False when something (a `?lake=` link) already owns the camera at startup. */
  autoFollow: () => boolean;
}

/**
 * Follow-me, heading-up, the wake lock and the own-position marker (design.md 7.7).
 *
 * Following starts on the first fix once permission is granted. A user pan or rotate
 * pauses it (zoom does not), and so does selecting a lake, which needs the camera; the
 * recenter button resumes it. Heading-up needs following plus speed; the compass button
 * forces north-up and is remembered.
 */
export class FollowController {
  private following = false;
  private everFollowed = false;
  private headingUp = false;
  private forceNorthUp = readLocal(NORTH_UP_KEY) === "1";
  private userZooming = false;
  private lock: WakeLockSentinel | null = null;
  private marker: OwnMarker;
  private recenterBtn: HTMLButtonElement | null;
  /** Set by a recenter tap with no fix, so a denial that answers it can explain itself. */
  private askedAt: number | null = null;
  private compassBtn: HTMLButtonElement;
  private compassNeedle: HTMLElement;
  private listeners = new Set<(following: boolean) => void>();

  constructor(private deps: FollowDeps) {
    const mlMap = deps.map.map;
    this.marker = new OwnMarker(mlMap);

    this.recenterBtn = deps.controls.querySelector<HTMLButtonElement>(".map-btn-recenter");
    const { button, needle } = this.buildCompass();
    this.compassBtn = button;
    this.compassNeedle = needle;
    if (this.recenterBtn) this.recenterBtn.after(button);
    else deps.controls.prepend(button);

    const userGesture = (e: MapLibreEvent<unknown>): boolean => e.originalEvent != null;
    mlMap.on("dragstart", (e) => {
      if (userGesture(e)) this.pause();
    });
    mlMap.on("rotatestart", (e) => {
      if (userGesture(e)) this.pause();
    });
    mlMap.on("zoomstart", (e) => {
      if (userGesture(e)) this.userZooming = true;
    });
    mlMap.on("zoomend", () => {
      this.userZooming = false;
    });
    mlMap.on("rotate", () => this.syncCompass());

    document.addEventListener("visibilitychange", () => {
      // The browser drops the lock when the page is hidden; ask again on the way back.
      if (document.visibilityState === "visible" && this.following) void this.acquireLock();
    });

    deps.location.onChange((fix, status) => {
      if (this.askedAt != null && status === "denied") {
        // The answer to a recenter tap: say where the switch is, long enough to read.
        this.askedAt = null;
        toast(statusNote(status) ?? "", 9000);
      } else if (fix) {
        this.askedAt = null;
      }
      this.onFix(fix);
    });
    this.syncButtons();
  }

  get isFollowing(): boolean {
    return this.following;
  }

  onFollowChange(fn: (following: boolean) => void): void {
    this.listeners.add(fn);
  }

  /** The recenter button: resume following, or say why it cannot and go to the home view. */
  recenter(): void {
    const fix = this.deps.location.fix;
    if (!fix) {
      this.deps.map.recenter();
      const status = this.deps.location.status;
      // This tap is the user gesture the browser wants before it will prompt.
      this.askedAt = Date.now();
      this.deps.location.request();
      if (status === "denied") toast(statusNote(status) ?? "", 9000);
      else toast("Finding your position…");
      return;
    }
    this.setFollowing(true);
    this.moveCamera(fix, true);
  }

  /** Any user pan or rotate, and a lake selection. */
  pause(): void {
    if (!this.following) return;
    this.setFollowing(false);
  }

  private onFix(fix: Fix | null): void {
    this.marker.update(fix);
    if (!fix) {
      this.setFollowing(false);
      return;
    }
    if (!this.everFollowed && this.deps.autoFollow()) {
      this.setFollowing(true);
      this.moveCamera(fix, true);
      return;
    }
    this.everFollowed = true;
    if (this.following) this.moveCamera(fix, false);
    else this.updateHeadingMode(fix);
  }

  private setFollowing(on: boolean): void {
    if (on) this.everFollowed = true;
    if (this.following === on) return;
    this.following = on;
    if (on) void this.acquireLock();
    else void this.releaseLock();
    if (!on && this.headingUp) this.headingUp = false;
    this.syncButtons();
    for (const fn of this.listeners) fn(on);
  }

  private updateHeadingMode(fix: Fix): void {
    const speed = fix.speed_kt ?? 0;
    const threshold = this.headingUp ? HEADING_UP_OFF_KT : HEADING_UP_KT;
    const next =
      this.following && !this.forceNorthUp && fix.course_deg != null && speed >= threshold;
    if (next !== this.headingUp) {
      this.headingUp = next;
      this.syncButtons();
    }
  }

  private moveCamera(fix: Fix, jump: boolean): void {
    this.updateHeadingMode(fix);
    // A pinch in progress wins; the next fix recentres.
    if (this.userZooming && !jump) return;
    const mlMap = this.deps.map.map;
    const padding = { ...this.deps.padding() };
    if (this.headingUp) {
      const h = mlMap.getContainer().clientHeight;
      const top = padding.top ?? 0;
      const avail = Math.max(0, h - top - (padding.bottom ?? 0));
      padding.top = top + Math.round(avail * HEADING_UP_LOOKAHEAD);
    }
    const bearing = this.headingUp ? (fix.course_deg ?? 0) : 0;
    mlMap.easeTo({
      center: [fix.lon, fix.lat],
      bearing,
      padding,
      ...(jump ? { zoom: Math.max(mlMap.getZoom(), FOLLOW_MIN_ZOOM) } : {}),
      duration: jump ? 600 : 950,
      easing: jump ? undefined : (t: number) => t,
      essential: true,
    });
  }

  // -- wake lock -----------------------------------------------------------

  private async acquireLock(): Promise<void> {
    if (this.lock || document.visibilityState !== "visible") return;
    if (!("wakeLock" in navigator)) return;
    try {
      const lock = await navigator.wakeLock.request("screen");
      if (!this.following) {
        void lock.release();
        return;
      }
      this.lock = lock;
      lock.addEventListener("release", () => {
        if (this.lock === lock) this.lock = null;
      });
    } catch (err) {
      // Low battery mode or a browser policy; the app works without it.
      console.info("[follow] wake lock refused", err);
    }
  }

  private async releaseLock(): Promise<void> {
    const lock = this.lock;
    this.lock = null;
    try {
      await lock?.release();
    } catch {
      // Already released by the browser.
    }
  }

  // -- buttons -------------------------------------------------------------

  private buildCompass(): { button: HTMLButtonElement; needle: HTMLElement } {
    const button = el("button", "icon-btn map-btn compass-btn");
    button.type = "button";
    const needle = el("span", "compass-needle");
    needle.setAttribute("aria-hidden", "true");
    needle.innerHTML =
      '<svg viewBox="0 0 24 24" focusable="false"><path class="compass-n" d="M12 2.5l4 9.5h-8z"/>' +
      '<path class="compass-s" d="M8 12h8l-4 9.5z"/></svg>';
    const mode = el("span", "compass-mode");
    mode.setAttribute("aria-hidden", "true");
    button.append(needle, mode);
    button.addEventListener("click", () => {
      // Left rotated by a pause out of heading-up: the first tap just squares the map to north.
      if (!this.following && Math.abs(this.deps.map.map.getBearing()) > 0.5) {
        this.deps.map.map.easeTo({ bearing: 0, duration: 400 });
        return;
      }
      this.forceNorthUp = !this.forceNorthUp;
      writeLocal(NORTH_UP_KEY, this.forceNorthUp ? "1" : "0");
      const fix = this.deps.location.fix;
      if (this.forceNorthUp || !this.following || !fix) {
        this.headingUp = false;
        this.deps.map.map.easeTo({ bearing: 0, duration: 400 });
      }
      if (this.following && fix) this.moveCamera(fix, false);
      toast(this.forceNorthUp ? "North-up" : "Heading-up above 10 kt while following");
      this.syncButtons();
    });
    return { button, needle };
  }

  private syncButtons(): void {
    const r = this.recenterBtn;
    if (r) {
      r.classList.toggle("is-active", this.following);
      r.setAttribute("aria-pressed", String(this.following));
      r.title = this.following ? "Following your position" : "Follow my position";
      r.setAttribute("aria-label", r.title);
    }
    const b = this.compassBtn;
    b.classList.toggle("is-active", this.forceNorthUp);
    b.setAttribute("aria-pressed", String(this.forceNorthUp));
    b.dataset["mode"] = this.headingUp ? "heading" : "north";
    const label = this.forceNorthUp
      ? "North-up locked. Tap to allow heading-up"
      : this.headingUp
        ? "Heading-up. Tap to lock north-up"
        : "North-up. Tap to lock north-up";
    b.title = label;
    b.setAttribute("aria-label", label);
    this.syncCompass();
  }

  /** The needle points at true north whatever the map bearing, like a compass card. */
  private syncCompass(): void {
    const bearing = this.deps.map.map.getBearing();
    this.compassNeedle.style.transform = `rotate(${-bearing}deg)`;
    // "N" only when the map really is north-up; a map left rotated shows just the needle.
    const mode = this.compassBtn.querySelector(".compass-mode");
    const text = this.headingUp ? "HDG" : Math.abs(bearing) < 0.5 ? "N" : "";
    if (mode && mode.textContent !== text) mode.textContent = text;
  }
}
