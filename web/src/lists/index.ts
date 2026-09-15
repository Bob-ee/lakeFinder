import { el } from "../ui/format";

export type TabId = "detail" | "nearest" | "saved";

interface TabDef {
  id: TabId;
  label: string;
}

const TABS: TabDef[] = [
  { id: "detail", label: "Detail" },
  { id: "nearest", label: "Nearest" },
  { id: "saved", label: "Saved" },
];

/**
 * Tab strip for the sheet. Detail is live in phase 1; Nearest and Saved are real slots
 * with placeholder panels so phases 2 and 4 drop straight in.
 */
export class Tabs {
  private active: TabId = "detail";
  private buttons = new Map<TabId, HTMLButtonElement>();
  private panels = new Map<TabId, HTMLElement>();

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
    this.panels.get("nearest")?.append(placeholder("Nearest lakes", "phase 2", nearestBlurb()));
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

function placeholder(title: string, phase: string, body: string): HTMLElement {
  const wrap = el("div", "placeholder");
  const head = el("div", "placeholder-head");
  head.append(el("h3", "detail-h", title), el("span", "tag", `coming in ${phase}`));
  wrap.append(head, el("p", "muted", body));
  return wrap;
}

function nearestBlurb(): string {
  return (
    "Lakes sorted by distance from your position, with an optional forward-cone filter " +
    "while you are moving. Needs the location and flight-mode work in phase 2."
  );
}

function savedBlurb(): string {
  return (
    "Starred lakes with tags, notes and a verified toggle, backed by the browser database " +
    "and synced to the homelab. Arrives with the personal layer in phase 4."
  );
}
