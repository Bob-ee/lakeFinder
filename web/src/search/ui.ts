import { SEARCH } from "../config";
import { lakeRow } from "../lists/row";
import type { Lake } from "../types";
import { el } from "../ui/format";
import { icon } from "../ui/icons";
import type { SearchIndex } from "./index";
import { listRecent } from "./recent";

export interface SearchBoxOptions {
  index: SearchIndex;
  onSelect: (id: number) => void;
  onOpenAbout: () => void;
}

/**
 * Top search box with typeahead. Debounced 100 ms, at most 8 rows, recent searches on an
 * empty query, arrow keys and enter for keyboard selection.
 */
export class SearchBox {
  readonly root: HTMLElement;
  private input: HTMLInputElement;
  private results: HTMLElement;
  private rows: HTMLElement[] = [];
  private ids: number[] = [];
  private cursor = -1;
  private debounce: number | null = null;
  private open = false;

  constructor(parent: HTMLElement, private opts: SearchBoxOptions) {
    this.root = el("div", "searchbar");

    const field = el("div", "search-field");
    const glyph = el("span", "search-icon");
    glyph.innerHTML = icon("search");
    glyph.setAttribute("aria-hidden", "true");

    this.input = document.createElement("input");
    this.input.type = "search";
    this.input.className = "search-input";
    this.input.placeholder = "Search Michigan lakes";
    this.input.autocomplete = "off";
    this.input.autocapitalize = "off";
    this.input.spellcheck = false;
    this.input.setAttribute("role", "combobox");
    this.input.setAttribute("aria-expanded", "false");
    this.input.setAttribute("aria-controls", "search-results");
    this.input.setAttribute("aria-autocomplete", "list");

    const clear = el("button", "icon-btn search-clear");
    clear.type = "button";
    clear.setAttribute("aria-label", "Clear search");
    clear.innerHTML = icon("close");
    clear.addEventListener("click", () => {
      this.input.value = "";
      this.input.focus();
      void this.refresh();
    });

    const about = el("button", "icon-btn search-about");
    about.type = "button";
    about.setAttribute("aria-label", "Settings and about");
    about.title = "Settings and about";
    about.innerHTML = icon("info");
    about.addEventListener("click", () => {
      this.hide();
      this.opts.onOpenAbout();
    });

    field.append(glyph, this.input, clear, about);

    this.results = el("div", "search-results");
    this.results.id = "search-results";
    this.results.setAttribute("role", "listbox");
    this.results.hidden = true;

    this.root.append(field, this.results);
    parent.append(this.root);

    this.input.addEventListener("input", () => this.schedule());
    this.input.addEventListener("focus", () => void this.refresh());
    this.input.addEventListener("keydown", (e) => this.onKey(e));
    document.addEventListener("pointerdown", (e) => {
      if (!this.root.contains(e.target as Node)) this.hide();
    });
  }

  focus(): void {
    this.input.focus();
  }

  hide(): void {
    this.open = false;
    this.results.hidden = true;
    this.input.setAttribute("aria-expanded", "false");
    this.cursor = -1;
  }

  private schedule(): void {
    if (this.debounce != null) clearTimeout(this.debounce);
    this.debounce = window.setTimeout(() => void this.refresh(), SEARCH.debounceMs);
  }

  private async refresh(): Promise<void> {
    const q = this.input.value.trim();
    this.root.classList.toggle("has-query", q !== "");
    if (q === "") {
      await this.showRecent();
      return;
    }
    const hits = this.opts.index.search(q, SEARCH.maxRows);
    this.render(
      hits.map((h) => h.lake),
      hits.length === 0 ? "No match" : null,
    );
  }

  private async showRecent(): Promise<void> {
    const recent = await listRecent();
    const lakes: Lake[] = [];
    for (const r of recent) {
      const lake = this.opts.index.get(r.id);
      if (lake) lakes.push(lake);
    }
    this.render(lakes, lakes.length === 0 ? null : null, "Recent");
  }

  private render(lakes: Lake[], emptyMessage: string | null, heading?: string): void {
    this.results.replaceChildren();
    this.rows = [];
    this.ids = [];
    this.cursor = -1;

    if (lakes.length === 0) {
      if (!emptyMessage) {
        this.hide();
        return;
      }
      this.results.append(el("div", "search-empty", emptyMessage));
    } else {
      if (heading) this.results.append(el("div", "search-heading", heading));
      for (const lake of lakes) {
        const row = lakeRow(lake, { onSelect: (id) => this.choose(id) });
        this.results.append(row);
        this.rows.push(row);
        this.ids.push(lake.id);
      }
    }
    this.open = true;
    this.results.hidden = false;
    this.input.setAttribute("aria-expanded", "true");
  }

  private choose(id: number): void {
    this.hide();
    this.input.blur();
    this.opts.onSelect(id);
  }

  private onKey(e: KeyboardEvent): void {
    if (e.key === "Escape") {
      this.hide();
      return;
    }
    if (!this.open || this.rows.length === 0) {
      if (e.key === "ArrowDown") void this.refresh();
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const delta = e.key === "ArrowDown" ? 1 : -1;
      this.cursor = (this.cursor + delta + this.rows.length) % this.rows.length;
      this.highlight();
    } else if (e.key === "Enter") {
      e.preventDefault();
      const at = this.cursor >= 0 ? this.cursor : 0;
      const id = this.ids[at];
      if (id != null) this.choose(id);
    }
  }

  private highlight(): void {
    this.rows.forEach((row, i) => {
      const on = i === this.cursor;
      row.classList.toggle("is-cursor", on);
      row.setAttribute("aria-selected", String(on));
      if (on) row.scrollIntoView({ block: "nearest" });
    });
  }
}
