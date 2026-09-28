import { el } from "../ui/format";

export type TabId = "detail" | "briefing" | "nearest" | "saved";

interface TabDef {
  id: TabId;
  label: string;
}

const TABS: TabDef[] = [
  { id: "detail", label: "Detail" },
  { id: "briefing", label: "Briefing" },
  { id: "nearest", label: "Nearest" },
  { id: "saved", label: "Saved" },
];

/**
 * Tab strip for the sheet. Detail, Briefing and Nearest are live (Nearest is mounted by
 * main.ts from lists/nearest.ts); Saved is a placeholder until phase 4.
 */
export class Tabs {
  private active: TabId = "detail";
  private buttons = new Map<TabId, HTMLButtonElement>();
  private panels = new Map<TabId, HTMLElement>();
  private listeners = new Set<(id: TabId) => void>();

  constructor(
    private strip: HTMLElement,
    private body: HTMLElement,
    private onChange?: (id: TabId) => void,
  ) {
    for (const tab of TABS) {
      const btn = el("button", "tab");
      btn.type = "button";
      btn.textContent = tab.label;
      btn.setAttribute("role", "tab");
      btn.id = `tab-${tab.id}`;
      btn.addEventListener("click", () => this.select(tab.id));
      this.strip.append(btn);
      this.buttons.set(tab.id, btn);

      const panel = el("div", "tab-panel");
      panel.setAttribute("role", "tabpanel");
      panel.setAttribute("aria-labelledby", btn.id);
      this.body.append(panel);
      this.panels.set(tab.id, panel);
    }
    this.panels.get("detail")?.append(detailPlaceholder());
    this.panels.get("saved")?.append(placeholder("Saved lakes", "phase 4", savedBlurb()));
    this.apply();
  }

  get current(): TabId {
    return this.active;
  }

  select(id: TabId): void {
    if (this.active === id) return;
    this.active = id;
    this.apply();
    this.onChange?.(id);
    for (const fn of this.listeners) fn(id);
  }

  /** Extra tab-change listeners beyond the constructor's one. */
  addListener(fn: (id: TabId) => void): void {
    this.listeners.add(fn);
  }

  /** The Detail panel is owned by the sheet renderer; everything else is static. */
  panel(id: TabId): HTMLElement {
    const p = this.panels.get(id);
    if (!p) throw new Error(`unknown tab ${id}`);
    return p;
  }

  private apply(): void {
    for (const [id, btn] of this.buttons) {
      const on = id === this.active;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-selected", String(on));
      btn.tabIndex = on ? 0 : -1;
    }
    for (const [id, panel] of this.panels) {
      panel.hidden = id !== this.active;
    }
  }
}

/**
 * The Detail panel is empty until a lake is selected, which can now happen while the
 * sheet is already open for the briefing. Says so rather than showing nothing.
 */
export function detailPlaceholder(): HTMLElement {
  const wrap = el("div", "placeholder");
  wrap.append(el("h3", "detail-h", "No lake selected"));
  wrap.append(
    el("p", "muted", "Tap a lake on the map, search for one, or pick one from the briefing."),
  );
  return wrap;
}

function placeholder(title: string, phase: string, body: string): HTMLElement {
  const wrap = el("div", "placeholder");
  const head = el("div", "placeholder-head");
  head.append(el("h3", "detail-h", title), el("span", "tag", `coming in ${phase}`));
  wrap.append(head, el("p", "muted", body));
  return wrap;
}

function savedBlurb(): string {
  return (
    "Starred lakes with tags, notes and a verified toggle, backed by the browser database " +
    "and synced to the homelab. Arrives with the personal layer in phase 4."
  );
}
