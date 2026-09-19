import { BRIEFING } from "../config";
import type { Lake } from "../types";
import { ageMinutes, el, formatRelative, formatRelativeShort } from "../ui/format";
import { icon } from "../ui/icons";
import { toast } from "../ui/toast";
import { getHealth, refreshBriefing } from "./api";
import { renderBriefingCard } from "./card";
import {
  SCORE_WORD,
  formatWindow,
  morningHeading,
  outlookLeads,
  relateDay,
  zoneNow,
} from "./labels";
import { openBriefingSettings } from "./settings";
import { BriefingStore, type BriefingState } from "./store";
import type { Health, Score, Window } from "./types";

export { BriefingStore } from "./store";
export type { BriefingState } from "./store";

export interface BriefingHost {
  /** Reveal the briefing panel: select its tab and open the sheet if it is closed. */
  show(): void;
  /** Close the sheet again when the briefing was the only thing holding it open. */
  hide(): void;
  /** True when a lake is selected, i.e. the peek row belongs to the detail view. */
  hasSelection(): boolean;
  lookupLake(id: number): Lake | null;
  /** The single selectLake(id) path in state/index.ts. */
  selectLake(id: number): void;
  /** Select a water body and frame one region of its wave field. */
  selectRegion(id: number, lat: number, lon: number): void;
}

/**
 * Owns the briefing store, the compact chip on the map, the card inside the sheet's own
 * tab, and the peek row shown when the sheet is open for the briefing rather than a lake.
 *
 * The card lives in the sheet because that is the one surface that is already a bottom
 * sheet on a phone and a docked panel on an iPad; the chip keeps it one tap from the
 * default view without stealing map space.
 */
export class BriefingController {
  readonly store = new BriefingStore();
  private chip: HTMLButtonElement | null = null;
  private panel: HTMLElement | null = null;
  private peek: HTMLElement | null = null;
  private health: Health | null | undefined = undefined;
  private refreshing = false;
  private open = false;

  constructor(private host: BriefingHost) {}

  start(): void {
    this.store.onChange(() => this.render());
    this.store.start();
    void this.probe();
  }

  /** Right-hand chip under the search bar. One tap opens the card. */
  mountChip(parent: HTMLElement): HTMLElement {
    const chip = el("button", "brief-chip");
    chip.type = "button";
    chip.setAttribute("aria-label", "Open the daily briefing");
    chip.addEventListener("click", () => this.reveal());
    parent.append(chip);
    this.chip = chip;
    this.render();
    return chip;
  }

  mountPanel(panel: HTMLElement): void {
    this.panel = panel;
    this.render();
  }

  mountPeek(peek: HTMLElement): void {
    this.peek = peek;
  }

  /** True while the sheet is open because of the briefing rather than a lake. */
  get isOpen(): boolean {
    return this.open;
  }

  /** Opens the card and, when no lake is selected, takes over the peek row. */
  reveal(): void {
    this.open = true;
    this.host.show();
    this.renderPeek();
    if (this.health === null) void this.probe();
  }

  private async probe(): Promise<void> {
    this.health = await getHealth();
    this.render();
  }

  private async runRefresh(): Promise<void> {
    if (this.refreshing) return;
    this.refreshing = true;
    this.render();
    try {
      this.store.adopt(await refreshBriefing());
      toast("Briefing refreshed");
    } catch (err) {
      console.warn("[briefing] manual refresh failed", err);
      toast("Could not reach the briefing service");
      await this.probe();
    } finally {
      this.refreshing = false;
      this.render();
    }
  }

  private render(): void {
    const state = this.store.current;
    if (this.chip) this.paintChip(this.chip, state);
    if (this.panel) {
      this.panel.replaceChildren(
        renderBriefingCard({
          state,
          health: this.health,
          refreshing: this.refreshing,
          lookupLake: (id) => this.host.lookupLake(id),
          onSelectLake: (id) => this.host.selectLake(id),
          onSelectRegion: (id, lat, lon) => this.host.selectRegion(id, lat, lon),
          onOpenSettings: () => this.openSettings(),
          onRefresh: () => void this.runRefresh(),
        }),
      );
    }
    if (this.open) this.renderPeek();
  }

  openSettings(): void {
    openBriefingSettings({
      onBriefing: (briefing) => {
        this.store.adopt(briefing);
        void this.probe();
      },
    });
  }

  private paintChip(chip: HTMLButtonElement, state: BriefingState): void {
    chip.replaceChildren();
    const lead = leadLine(state);
    if (lead.score) chip.dataset["score"] = lead.score;
    else delete chip.dataset["score"];
    chip.append(el("span", "brief-chip-mark"));
    chip.append(el("span", "brief-chip-text", lead.text));
    if (lead.shortAge) {
      const age = el("span", "brief-chip-age", lead.shortAge);
      age.classList.toggle("is-stale", lead.stale);
      chip.append(age);
    }
    chip.append(spanHtml("brief-chip-caret", icon("chevron")));
  }

  private renderPeek(): void {
    const peek = this.peek;
    if (!peek || this.host.hasSelection()) return;
    const state = this.store.current;
    const lead = leadLine(state);

    peek.replaceChildren();
    delete peek.dataset["verdict"];
    const bar = el("div", "verdict-bar score-bar");
    if (lead.score) bar.dataset["score"] = lead.score;

    const text = el("div", "peek-text");
    text.append(el("div", "peek-title", lead.title));
    const sub = el("div", "peek-sub");
    sub.append(el("span", "peek-county", lead.sub));
    if (lead.age) {
      const age = el("span", "peek-score", lead.age);
      if (lead.score) age.dataset["score"] = lead.score;
      age.classList.toggle("is-stale", lead.stale);
      sub.append(age);
    }
    text.append(sub);

    const close = el("button", "icon-btn");
    close.type = "button";
    close.setAttribute("aria-label", "Close the briefing");
    close.innerHTML = icon("close");
    close.addEventListener("click", () => {
      this.open = false;
      this.host.hide();
    });

    peek.append(bar, text, close);
  }
}

interface LeadLine {
  score: Score | null;
  /** Chip text: "Tomorrow · Favorable 08:00-12:00". */
  text: string;
  /** Compact age for the chip, where the full phrase does not fit. */
  shortAge: string | null;
  /** Peek title: "Favorable 08:00-12:00". */
  title: string;
  /** Peek subtitle: "Tomorrow morning · KPTK". */
  sub: string;
  age: string | null;
  stale: boolean;
}

/**
 * The one line that represents the whole briefing, following the contract's ordering rule:
 * the outlook leads in the evening and while this morning's window is still open.
 */
function leadLine(state: BriefingState): LeadLine {
  if (state.kind === "loading") {
    return {
      score: null,
      text: "Briefing…",
      shortAge: null,
      title: "Briefing",
      sub: "loading",
      age: null,
      stale: false,
    };
  }
  if (state.kind === "empty") {
    return {
      score: null,
      text: "No briefing yet",
      shortAge: null,
      title: "No briefing yet",
      sub: "waiting for the first run",
      age: null,
      stale: false,
    };
  }
  if (state.kind === "unavailable") {
    return {
      score: null,
      text: "Briefing unavailable",
      shortAge: null,
      title: "Briefing unavailable",
      sub: "nothing stored on this device",
      age: null,
      stale: false,
    };
  }

  const b = state.briefing;
  const today = zoneNow(b.timezone).date;
  const leadsWithOutlook = b.outlook != null && outlookLeads(b.outlook, b.timezone);
  const lead = leadsWithOutlook && b.outlook ? b.outlook : b.days[0];
  const score: Score = lead?.score ?? "unfavorable";
  const window: Window | null = lead?.best_window ?? null;

  let when: string;
  let longWhen: string;
  if (leadsWithOutlook && b.outlook) {
    longWhen = morningHeading(b.outlook.target_date, today);
    when = relateDay(b.outlook.target_date, today) === "tomorrow" ? "Tomorrow" : "Morning";
  } else {
    longWhen = b.days[0] ? dayWord(b.days[0].date, today) : "Today";
    when = longWhen;
  }

  const mins = ageMinutes(b.generated_at);
  const headline = `${SCORE_WORD[score]} ${formatWindow(window)}`;
  return {
    score,
    text: `${when} · ${headline}`,
    shortAge: formatRelativeShort(b.generated_at),
    title: headline,
    sub: `${longWhen} · ${b.home_airport.id}`,
    age: formatRelative(b.generated_at),
    stale: mins != null && mins >= BRIEFING.staleMinutes,
  };
}

function dayWord(isoDate: string, today: string): string {
  const rel = relateDay(isoDate, today);
  return rel === "today" ? "Today" : rel === "tomorrow" ? "Tomorrow" : isoDate;
}

function spanHtml(className: string, html: string): HTMLElement {
  const node = el("span", className);
  node.innerHTML = html;
  node.setAttribute("aria-hidden", "true");
  return node;
}

/** `?briefing=1` opens the card on load; handy as a home-screen shortcut. */
export function readBriefingParam(): boolean {
  try {
    const raw = new URL(window.location.href).searchParams.get(BRIEFING.urlParam);
    return raw != null && raw !== "" && raw !== "0" && raw !== "false";
  } catch {
    return false;
  }
}
