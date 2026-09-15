import { el } from "../ui/format";

export type Snap = "hidden" | "peek" | "half" | "full";

/** Matches the `--panel` breakpoint in styles/sheet.css. iPad landscape and wider. */
export const PANEL_QUERY = "(min-width: 840px)";

const PEEK_PX = 92;

export interface SheetSlots {
  /** Peek row: verdict bar, name, county, one-line phrase, star. No sentences. */
  peek: HTMLElement;
  /** Tab strip: Detail / Nearest / Saved. */
  tabs: HTMLElement;
  /** Scrolling body; holds whichever tab panel is active. */
  body: HTMLElement;
}

/**
 * Three snap points on phones (peek / half / full), a fixed right-side panel at the
 * panel breakpoint. Same element either way, per design.md 7.1.
 */
export class Sheet {
  readonly root: HTMLElement;
  readonly slots: SheetSlots;
  private snap: Snap = "hidden";
  private panelMedia: MediaQueryList;
  private listeners = new Set<(snap: Snap) => void>();
  private drag: { startY: number; startH: number; pointerId: number } | null = null;

  constructor(parent: HTMLElement) {
    this.root = el("section", "sheet");
    this.root.dataset["snap"] = "hidden";
    this.root.setAttribute("aria-label", "Lake detail");

    const handle = el("div", "sheet-handle");
    handle.setAttribute("role", "separator");
    handle.setAttribute("aria-label", "Resize detail sheet");
    handle.tabIndex = 0;
    handle.append(el("span", "sheet-grip"));

    const peek = el("div", "sheet-peek");
    const tabs = el("div", "sheet-tabs");
    tabs.setAttribute("role", "tablist");
    const body = el("div", "sheet-body");

    this.root.append(handle, peek, tabs, body);
    parent.append(this.root);
    this.slots = { peek, tabs, body };

    this.panelMedia = matchMedia(PANEL_QUERY);
    this.panelMedia.addEventListener("change", () => {
      this.applyHeight();
      this.emit();
    });

    this.wireDrag(handle);
    handle.addEventListener("keydown", (e) => this.onHandleKey(e));
    handle.addEventListener("click", () => this.cycle());
    window.addEventListener("resize", () => this.applyHeight());
  }

  get isPanel(): boolean {
    return this.panelMedia.matches;
  }

  get current(): Snap {
    return this.snap;
  }

  onSnapChange(fn: (snap: Snap) => void): void {
    this.listeners.add(fn);
  }

  setSnap(snap: Snap): void {
    if (this.snap === snap) return;
    this.snap = snap;
    this.root.dataset["snap"] = snap;
    this.root.classList.toggle("is-open", snap !== "hidden");
    this.applyHeight();
    this.emit();
  }

  /** Height of the sheet in CSS pixels, 0 when hidden or docked as a panel. */
  get heightPx(): number {
    if (this.isPanel || this.snap === "hidden") return 0;
    return this.snapHeight(this.snap);
  }

  /** Width of the docked panel, 0 on phones. */
  get widthPx(): number {
    return this.isPanel && this.snap !== "hidden" ? this.root.getBoundingClientRect().width : 0;
  }

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

  private cycle(): void {
    if (this.isPanel) return;
    const order: Snap[] = ["peek", "half", "full"];
    const at = order.indexOf(this.snap);
    this.setSnap(order[(at + 1) % order.length] ?? "half");
  }

  private onHandleKey(e: KeyboardEvent): void {
    const order: Snap[] = ["peek", "half", "full"];
    const at = Math.max(0, order.indexOf(this.snap));
    if (e.key === "ArrowUp") {
      e.preventDefault();
      this.setSnap(order[Math.min(order.length - 1, at + 1)] ?? "full");
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      this.setSnap(order[Math.max(0, at - 1)] ?? "peek");
    }
  }

  private wireDrag(handle: HTMLElement): void {
    handle.addEventListener("pointerdown", (e) => {
      if (this.isPanel || this.snap === "hidden") return;
      handle.setPointerCapture(e.pointerId);
      this.drag = {
        startY: e.clientY,
        startH: this.root.getBoundingClientRect().height,
        pointerId: e.pointerId,
      };
      this.root.classList.add("is-dragging");
    });

    const end = (e: PointerEvent) => {
      if (!this.drag || this.drag.pointerId !== e.pointerId) return;
      const height = this.root.getBoundingClientRect().height;
      this.drag = null;
      this.root.classList.remove("is-dragging");
      // Snap to whichever point is closest to where the finger let go.
      const candidates: Snap[] = ["peek", "half", "full"];
      let best: Snap = "half";
      let bestDist = Infinity;
      for (const c of candidates) {
        const d = Math.abs(this.snapHeight(c) - height);
        if (d < bestDist) {
          bestDist = d;
          best = c;
        }
      }
      this.root.style.setProperty("--sheet-height", `${this.snapHeight(best)}px`);
      if (best === this.snap) this.applyHeight();
      else this.setSnap(best);
    };

    handle.addEventListener("pointermove", (e) => {
      if (!this.drag || this.drag.pointerId !== e.pointerId) return;
      e.preventDefault();
      const next = Math.min(
        window.innerHeight * 0.95,
        Math.max(56, this.drag.startH - (e.clientY - this.drag.startY)),
      );
      this.root.style.setProperty("--sheet-height", `${Math.round(next)}px`);
    });
    handle.addEventListener("pointerup", end);
    handle.addEventListener("pointercancel", end);
  }

  private emit(): void {
    for (const fn of this.listeners) fn(this.snap);
  }
}
