import { STORAGE_KEYS } from "../config";
import { el } from "../ui/format";
import { icon } from "../ui/icons";
import { readLocal, writeLocal } from "../ui/theme";

export type Snap = "hidden" | "peek" | "half" | "full";
type OpenSnap = "peek" | "half" | "full";

/** Matches the `--panel` breakpoint in styles/sheet.css. iPad landscape and wider. */
export const PANEL_QUERY = "(min-width: 840px)";

const PEEK_PX = 92;
const ORDER: OpenSnap[] = ["peek", "half", "full"];
/** Vertical movement before a press on the header (or body) counts as a drag, not a tap. */
const SLOP_PX = 7;
/** Release speed, px/ms (0.5 = 500 px/s), above which a drag is a flick to the next snap. */
const FLICK = 0.5;
/** Matches the height transition in styles/sheet.css. */
const SETTLE_MS = 220;
/** Rightward swipe on the panel header that collapses the iPad panel. */
const PANEL_SWIPE_PX = 56;

const rank = (s: Snap): number => (s === "hidden" ? -1 : ORDER.indexOf(s));

interface Stored {
  snap?: Snap;
  collapsed?: boolean;
}

function readStored(): Stored {
  try {
    const raw = readLocal(STORAGE_KEYS.sheet);
    const parsed = raw ? (JSON.parse(raw) as Stored) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

export interface SheetSlots {
  /** Peek row: verdict bar, name, county, one-line phrase, star. No sentences. */
  peek: HTMLElement;
  /** Tab strip: Detail / Nearest / Saved. */
  tabs: HTMLElement;
  /** Scrolling body; holds whichever tab panel is active. */
  body: HTMLElement;
}

interface Gesture {
  pointerId: number;
  startX: number;
  startY: number;
  startH: number;
  active: boolean;
  samples: { t: number; y: number }[];
}

/**
 * Three snap points on phones (peek / half / full), a right-side panel at the panel
 * breakpoint. Same element either way, per design.md 7.1.
 *
 * The pilot owns the height. Selecting a lake never moves a visible sheet; from hidden it
 * shows peek. Only an explicit request for content that needs room (the briefing chip, the
 * Nearest button) may raise it, via `grow`, which never shrinks. The last height the pilot
 * chose is stored and used the first time the sheet shows after a reload.
 */
export class Sheet {
  readonly root: HTMLElement;
  readonly slots: SheetSlots;
  private snap: Snap = "hidden";
  /** The open height a tap on the handle returns to from peek. */
  private lastOpen: "half" | "full" = "half";
  /** The pilot's stored snap, used once: the first time the sheet shows after load. */
  private restore: OpenSnap | null;
  private collapsed: boolean;
  private panelMedia: MediaQueryList;
  private reducedMotion: MediaQueryList;
  private listeners = new Set<(snap: Snap) => void>();
  private handle: HTMLButtonElement;
  private restoreTab: HTMLButtonElement;
  private gesture: Gesture | null = null;
  private closeTimer = 0;
  private settleTimer = 0;
  private suppressClickUntil = 0;

  constructor(parent: HTMLElement) {
    const stored = readStored();
    this.restore =
      stored.snap === "half" || stored.snap === "full" || stored.snap === "peek"
        ? stored.snap
        : stored.snap === "hidden"
          ? "peek"
          : null;
    if (this.restore === "half" || this.restore === "full") this.lastOpen = this.restore;
    this.collapsed = stored.collapsed === true;

    this.root = el("section", "sheet");
    this.root.dataset["snap"] = "hidden";
    this.root.setAttribute("aria-label", "Lake detail");

    // A real button: Enter / Space toggle, arrows step, Escape goes to peek.
    const handle = el("button", "sheet-handle");
    handle.type = "button";
    handle.append(el("span", "sheet-grip"));
    this.handle = handle;

    // Panel mode only: the bar with the collapse control.
    const panelbar = el("div", "sheet-panelbar");
    const collapse = el("button", "sheet-collapse");
    collapse.type = "button";
    collapse.setAttribute("aria-label", "Hide the side panel");
    collapse.innerHTML = `<span>Hide panel</span>${icon("chevron")}`;
    collapse.addEventListener("click", () => this.setCollapsed(true, true));
    panelbar.append(collapse);

    const peek = el("div", "sheet-peek");
    const tabs = el("div", "sheet-tabs");
    tabs.setAttribute("role", "tablist");
    const body = el("div", "sheet-body");
    body.id = "sheet-body";
    handle.setAttribute("aria-controls", body.id);

    this.root.append(handle, panelbar, peek, tabs, body);
    parent.append(this.root);
    this.slots = { peek, tabs, body };

    // Collapsed panel: a slim tab on the right edge with the lake's name and verdict word.
    this.restoreTab = el("button", "sheet-restore");
    this.restoreTab.type = "button";
    this.restoreTab.hidden = true;
    this.restoreTab.addEventListener("click", () => this.setCollapsed(false, true));
    parent.append(this.restoreTab);
    new MutationObserver(() => this.paintRestoreTab()).observe(peek, {
      childList: true,
      subtree: true,
      characterData: true,
    });

    this.panelMedia = matchMedia(PANEL_QUERY);
    this.reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
    this.panelMedia.addEventListener("change", () => {
      this.applyHeight();
      this.applyCollapsed();
      this.emit();
    });

    this.wireHeader();
    this.wireBody(body);
    handle.addEventListener("click", () => {
      if (Date.now() < this.suppressClickUntil) return;
      this.toggle();
    });
    peek.addEventListener("click", (e) => {
      if (Date.now() < this.suppressClickUntil || this.isPanel) return;
      if ((e.target as Element).closest("button, a, input, select, textarea")) return;
      this.toggle();
    });
    this.root.addEventListener(
      "click",
      (e) => {
        // A drag that began on a tab or the star must not also press it.
        if (Date.now() < this.suppressClickUntil) {
          e.stopPropagation();
          e.preventDefault();
        }
      },
      true,
    );
    this.root.addEventListener("keydown", (e) => this.onKey(e));
    window.addEventListener("resize", () => this.applyHeight());
    this.applyCollapsed();
    this.paintHandle();
  }

  get isPanel(): boolean {
    return this.panelMedia.matches;
  }

  get current(): Snap {
    return this.snap;
  }

  /** True while the iPad panel is tucked away to its tab. */
  get isCollapsed(): boolean {
    return this.collapsed;
  }

  onSnapChange(fn: (snap: Snap) => void): void {
    this.listeners.add(fn);
  }

  /** Programmatic snap change. Not remembered as the pilot's choice. */
  setSnap(snap: Snap): void {
    if (this.snap === snap) return;
    const wasOpen = this.snap !== "hidden";
    this.snap = snap;
    this.root.dataset["snap"] = snap;
    if (snap === "half" || snap === "full") this.lastOpen = snap;
    window.clearTimeout(this.closeTimer);

    if (snap === "hidden") {
      if (wasOpen && !this.isPanel && !this.reducedMotion.matches) {
        // Slide down, then drop out of the layout.
        this.root.style.setProperty("--sheet-height", "0px");
        this.closeTimer = window.setTimeout(() => {
          if (this.snap === "hidden") this.root.classList.remove("is-open");
        }, SETTLE_MS);
      } else {
        this.root.classList.remove("is-open");
        this.applyHeight();
      }
    } else {
      this.restore = null;
      if (!this.root.classList.contains("is-open")) {
        // Start from zero so the first show slides up rather than popping in.
        this.root.style.setProperty("--sheet-height", "0px");
        this.root.classList.add("is-open");
        void this.root.offsetHeight;
      }
      this.applyHeight();
    }
    this.applyCollapsed();
    this.paintHandle();
    this.emit();
  }

  /**
   * A lake was selected. A visible sheet stays exactly where the pilot left it; a hidden
   * one shows peek, or the stored snap on the first show after a reload.
   */
  reveal(): void {
    if (this.snap !== "hidden") return;
    this.setSnap(this.restore ?? "peek");
  }

  /**
   * The pilot asked for content that needs room (briefing chip, Nearest button): raise the
   * sheet to at least `min`, never lower it, and bring a collapsed panel back.
   */
  grow(min: OpenSnap): void {
    if (this.collapsed) this.setCollapsed(false, false);
    const floor = this.snap === "hidden" && this.restore && rank(this.restore) > rank(min) ? this.restore : min;
    if (rank(this.snap) < rank(floor)) this.setSnap(floor);
  }

  /** Height of the sheet in CSS pixels, 0 when hidden or docked as a panel. */
  get heightPx(): number {
    if (this.isPanel || this.snap === "hidden") return 0;
    return this.snapHeight(this.snap);
  }

  /** Width of the docked panel, 0 on phones and while the panel is collapsed. */
  get widthPx(): number {
    return this.isPanel && this.snap !== "hidden" && !this.collapsed
      ? this.root.getBoundingClientRect().width
      : 0;
  }

  // -- the pilot's choices ---------------------------------------------------

  /** A snap the pilot chose by drag, tap or key: applied and remembered. */
  private userSnap(snap: Snap): void {
    this.setSnap(snap);
    this.persist();
  }

  private setCollapsed(on: boolean, byUser: boolean): void {
    if (this.collapsed === on) return;
    this.collapsed = on;
    this.applyCollapsed();
    if (byUser) {
      this.persist();
      if (on) this.restoreTab.focus({ preventScroll: true });
      else this.handle.blur();
    }
    this.emit();
  }

  private persist(): void {
    const data: Stored = { snap: this.snap, collapsed: this.collapsed };
    writeLocal(STORAGE_KEYS.sheet, JSON.stringify(data));
  }

  /** Handle (and peek row) tap: peek <-> the last open height. */
  private toggle(): void {
    if (this.isPanel || this.snap === "hidden") return;
    this.userSnap(this.snap === "peek" ? this.lastOpen : "peek");
  }

  private onKey(e: KeyboardEvent): void {
    if (this.isPanel || this.snap === "hidden") return;
    const t = e.target as Element;
    if (t.closest("input, select, textarea")) return;
    if (e.key === "Escape") {
      if (this.snap !== "peek") {
        e.preventDefault();
        this.userSnap("peek");
        this.handle.focus({ preventScroll: true });
      }
      return;
    }
    if (t !== this.handle) return;
    const at = Math.max(0, rank(this.snap));
    if (e.key === "ArrowUp") {
      e.preventDefault();
      this.userSnap(ORDER[Math.min(ORDER.length - 1, at + 1)] ?? "full");
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      this.userSnap(ORDER[Math.max(0, at - 1)] ?? "peek");
    }
  }

  // -- geometry --------------------------------------------------------------

  private snapHeight(snap: Snap): number {
    const vh = window.innerHeight;
    switch (snap) {
      case "hidden":
        return 0;
      case "peek":
        return PEEK_PX;
      case "half":
        return Math.round(vh * 0.5);
      case "full":
        return Math.round(vh * 0.92);
    }
  }

  private applyHeight(): void {
    if (this.isPanel) {
      this.root.style.removeProperty("--sheet-height");
      return;
    }
    this.root.style.setProperty("--sheet-height", `${this.snapHeight(this.snap)}px`);
  }

  private applyCollapsed(): void {
    const tucked = this.isPanel && this.collapsed;
    this.root.classList.toggle("is-collapsed", tucked);
    this.root.toggleAttribute("inert", tucked);
    this.restoreTab.hidden = !(tucked && this.snap !== "hidden");
    document.documentElement.style.setProperty("--panel-w", `${this.widthPx}px`);
    this.paintRestoreTab();
  }

  private paintHandle(): void {
    const open = this.snap === "half" || this.snap === "full";
    this.handle.setAttribute("aria-expanded", String(open));
    this.handle.setAttribute(
      "aria-label",
      open ? "Lower the detail sheet to its peek row" : "Raise the detail sheet",
    );
    this.handle.title = "Drag, tap, or use the arrow keys to resize";
  }

  /** Mirrors the peek row: verdict bar, name, and the verdict (or briefing) word. */
  private paintRestoreTab(): void {
    if (this.restoreTab.hidden) return;
    const peek = this.slots.peek;
    const name = peek.querySelector(".peek-title")?.textContent?.trim() || "Detail";
    const word =
      peek.querySelector(".peek-verdict, .peek-score")?.textContent?.trim() ?? "";
    const bar = peek.querySelector(".verdict-bar");
    const text = el("span", "sheet-restore-text");
    text.append(el("span", "sheet-restore-name", name));
    if (word) {
      const w = el("span", "sheet-restore-word", word);
      const verdict = peek.dataset["verdict"];
      if (verdict) w.dataset["verdict"] = verdict;
      text.append(w);
    }
    const chev = el("span", "sheet-restore-chev");
    chev.innerHTML = icon("chevron");
    this.restoreTab.replaceChildren(chev);
    if (bar) this.restoreTab.append(bar.cloneNode(true));
    this.restoreTab.append(text);
    this.restoreTab.setAttribute("aria-label", `Show the side panel: ${name}${word ? `, ${word}` : ""}`);
  }

  // -- gestures --------------------------------------------------------------

  /** Handle, peek row and tab strip: pointer events, `touch-action: none` in CSS. */
  private wireHeader(): void {
    const inHeader = (t: EventTarget | null): boolean =>
      t instanceof Element && t.closest(".sheet-body") == null && this.root.contains(t);

    this.root.addEventListener("pointerdown", (e) => {
      if (!inHeader(e.target) || this.snap === "hidden" || this.gesture) return;
      if (e.pointerType === "mouse" && e.button !== 0) return;
      this.gesture = {
        pointerId: e.pointerId,
        startX: e.clientX,
        startY: e.clientY,
        startH: this.root.getBoundingClientRect().height,
        active: false,
        samples: [{ t: e.timeStamp, y: e.clientY }],
      };
    });

    this.root.addEventListener("pointermove", (e) => {
      const g = this.gesture;
      if (!g || g.pointerId !== e.pointerId) return;
      const dx = e.clientX - g.startX;
      const dy = e.clientY - g.startY;
      if (this.isPanel) return; // Panel: horizontal swipe, judged on release.
      if (!g.active) {
        if (Math.abs(dx) >= SLOP_PX && Math.abs(dx) > Math.abs(dy)) {
          this.gesture = null;
          return;
        }
        if (Math.abs(dy) < SLOP_PX) return;
        g.active = true;
        try {
          this.root.setPointerCapture(e.pointerId);
        } catch {
          // The pointer may already be gone.
        }
        this.beginDrag();
      }
      e.preventDefault();
      this.dragTo(g, e.clientY, e.timeStamp);
    });

    const end = (e: PointerEvent, cancelled: boolean) => {
      const g = this.gesture;
      if (!g || g.pointerId !== e.pointerId) return;
      this.gesture = null;
      if (this.isPanel) {
        const dx = e.clientX - g.startX;
        const dy = e.clientY - g.startY;
        if (!cancelled && dx >= PANEL_SWIPE_PX && Math.abs(dx) > Math.abs(dy) * 1.5) {
          this.suppressClickUntil = Date.now() + 400;
          this.setCollapsed(true, true);
        }
        return;
      }
      if (!g.active) return;
      this.suppressClickUntil = Date.now() + 400;
      this.settle(g, cancelled);
    };
    this.root.addEventListener("pointerup", (e) => end(e, false));
    this.root.addEventListener("pointercancel", (e) => end(e, true));
  }

  /**
   * The scrolling body, touch only. At half or full, a downward drag that starts with the
   * body scrolled to the top moves the sheet; at half, an upward drag raises it toward full.
   * Anything else is an ordinary scroll. Touch events rather than pointer events because
   * only a non-passive touchmove can take a gesture back from the scroller on iOS Safari.
   */
  private wireBody(body: HTMLElement): void {
    let g: (Gesture & { atTop: boolean; decided: boolean }) | null = null;

    body.addEventListener(
      "touchstart",
      (e) => {
        g = null;
        if (this.isPanel || (this.snap !== "half" && this.snap !== "full")) return;
        if (e.touches.length !== 1) return;
        const target = e.target as Element;
        if (target.closest("input, select, textarea, .wind-dial, [data-sheet-nodrag]")) return;
        const t = e.touches[0]!;
        g = {
          pointerId: t.identifier,
          startX: t.clientX,
          startY: t.clientY,
          startH: this.root.getBoundingClientRect().height,
          active: false,
          decided: false,
          atTop: scrolledToTop(target, body),
          samples: [{ t: e.timeStamp, y: t.clientY }],
        };
      },
      { passive: true },
    );

    body.addEventListener(
      "touchmove",
      (e) => {
        if (!g) return;
        if (e.touches.length !== 1) {
          if (g.active) this.settle(g, true);
          g = null;
          return;
        }
        const t = e.touches[0]!;
        const dx = t.clientX - g.startX;
        const dy = t.clientY - g.startY;
        const wantsSheet = (dir: number) =>
          (dir > 0 && g!.atTop) || (dir < 0 && this.snap === "half");
        if (!g.decided) {
          if (Math.abs(dx) < SLOP_PX && Math.abs(dy) < SLOP_PX) {
            // Hold the scroller still while it could still become a sheet drag.
            if (dy !== 0 && wantsSheet(Math.sign(dy)) && e.cancelable) e.preventDefault();
            return;
          }
          g.decided = true;
          if (Math.abs(dx) > Math.abs(dy) || !wantsSheet(Math.sign(dy))) {
            g = null;
            return;
          }
          g.active = true;
          this.beginDrag();
        }
        if (e.cancelable) e.preventDefault();
        this.dragTo(g, t.clientY, e.timeStamp);
      },
      { passive: false },
    );

    const end = (cancelled: boolean) => {
      if (g?.active) {
        this.suppressClickUntil = Date.now() + 400;
        this.settle(g, cancelled);
      }
      g = null;
    };
    body.addEventListener("touchend", () => end(false));
    body.addEventListener("touchcancel", () => end(true));
  }

  private beginDrag(): void {
    window.clearTimeout(this.settleTimer);
    this.root.classList.add("is-dragging", "is-settling");
  }

  private dragTo(g: Gesture, y: number, t: number): void {
    const h = Math.min(window.innerHeight * 0.95, Math.max(0, g.startH - (y - g.startY)));
    this.root.style.setProperty("--sheet-height", `${Math.round(h)}px`);
    g.samples.push({ t, y });
    while (g.samples.length > 2 && t - g.samples[0]!.t > 100) g.samples.shift();
  }

  /** Release: a flick goes to the next snap its way, otherwise the nearest one. */
  private settle(g: Gesture, cancelled: boolean): void {
    const h = this.root.getBoundingClientRect().height;
    this.root.classList.remove("is-dragging");

    let v = 0; // px/ms, positive = upward
    const first = g.samples[0];
    const last = g.samples[g.samples.length - 1];
    if (!cancelled && first && last && last.t > first.t) v = (first.y - last.y) / (last.t - first.t);

    let target: Snap;
    if (v > FLICK) {
      target = ORDER.find((s) => this.snapHeight(s) > h + 8) ?? "full";
    } else if (v < -FLICK) {
      target = [...ORDER].reverse().find((s) => this.snapHeight(s) < h - 8) ?? "hidden";
    } else if (h < PEEK_PX * 0.6) {
      target = "hidden";
    } else {
      target = ORDER.reduce((best, s) =>
        Math.abs(this.snapHeight(s) - h) < Math.abs(this.snapHeight(best) - h) ? s : best,
      );
    }

    // Keep the tabs and body drawn while the height animates to peek.
    window.clearTimeout(this.settleTimer);
    this.settleTimer = window.setTimeout(
      () => this.root.classList.remove("is-settling"),
      SETTLE_MS,
    );
    if (target === this.snap) {
      this.applyHeight();
      this.persist();
    } else {
      this.userSnap(target);
    }
  }

  private emit(): void {
    document.documentElement.style.setProperty("--panel-w", `${this.widthPx}px`);
    for (const fn of this.listeners) fn(this.snap);
  }
}

/** True when neither the target nor any scroller between it and the body is scrolled down. */
function scrolledToTop(target: Element, body: HTMLElement): boolean {
  for (let n: Element | null = target; n; n = n.parentElement) {
    if (n.scrollTop > 0) return false;
    if (n === body) break;
  }
  return true;
}
