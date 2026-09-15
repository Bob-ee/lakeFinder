import { loadData } from "../pack";
import { RulesRunner } from "../pack/rules";
import { SearchIndex } from "../search";
import { normalizeName } from "../search/normalize";
import { pushRecent } from "../search/recent";
import type { Evaluation, Lake, Pack, Restriction, RestrictionIndex } from "../types";

export type SelectionSource = "search" | "map" | "url" | "list";

export interface Selection {
  lake: Lake;
  evaluation: Evaluation;
  restrictions: Restriction[];
  source: SelectionSource;
}

type SelectionListener = (selection: Selection | null) => void;

/**
 * The one place a lake becomes "the selected lake". Search rows, map taps, list rows and
 * the `?lake=<id>` URL param all come through `selectLake` (design.md 7.3).
 */
export class AppState {
  readonly search = new SearchIndex();
  readonly rules = new RulesRunner();
  private restrictions: RestrictionIndex = {};
  private packManifest: Pack | null = null;
  private missingFiles: string[] = [];
  private selection: Selection | null = null;
  private listeners = new Set<SelectionListener>();

  async load(): Promise<void> {
    const data = await loadData();
    this.packManifest = data.pack;
    this.restrictions = data.restrictions;
    this.missingFiles = data.missing;
    this.search.load(data.lakes);
    this.rules.init(data.rules, data.pack?.mac_record_loaded === true);
  }

  get pack(): Pack | null {
    return this.packManifest;
  }

  get missing(): readonly string[] {
    return this.missingFiles;
  }

  get current(): Selection | null {
    return this.selection;
  }

  onSelection(fn: SelectionListener): void {
    this.listeners.add(fn);
  }

  /**
   * Resolves a map tap whose tile feature carried no `id`, by normalized name and county.
   * Ambiguous names (Long, Mud, Round, Silver) can collide inside one county; the first
   * match wins, which is the best that can be done without the id the contract asks for.
   */
  resolveHit(name: string | null, county: string | null): number | null {
    if (!name) return null;
    const norm = normalizeName(name);
    if (norm === "") return null;
    const matches = this.search
      .all()
      .filter((l) => l.name_norm === norm && (county == null || l.county === county));
    if (matches.length === 0) return null;
    if (matches.length > 1) {
      console.warn(`[state] ${matches.length} lakes match "${name}" in ${county}; taking the first`);
    }
    return matches[0]!.id;
  }

  restrictionsFor(lake: Lake): Restriction[] {
    const out: Restriction[] = [];
    for (const id of lake.restriction_ids) {
      const record = this.restrictions[id];
      // Rescinded rows should already be dropped at build; skip them if one slips through.
      if (record && record.status !== "rescinded") out.push(record);
      else if (!record) console.warn(`[state] lake ${lake.id} references unknown restriction ${id}`);
    }
    return out;
  }

  /** Returns the selection, or null when the id is not in index.json. */
  selectLake(id: number, source: SelectionSource = "search"): Selection | null {
    const lake = this.search.get(id);
    if (!lake) {
      console.warn(`[state] no lake with id ${id} in index.json`);
      return null;
    }
    const restrictions = this.restrictionsFor(lake);
    const evaluation = this.rules.evaluate(lake, restrictions);
    this.selection = { lake, evaluation, restrictions, source };
    writeUrlParam(lake.id);
    if (source !== "url") void pushRecent(lake);
    this.emit();
    return this.selection;
  }

  clearSelection(): void {
    if (!this.selection) return;
    this.selection = null;
    writeUrlParam(null);
    this.emit();
  }

  private emit(): void {
    for (const fn of this.listeners) fn(this.selection);
  }
}

/** `?lake=<id>` read on load (design.md 7.3 / data-contract "Client URL and storage"). */
export function readUrlParam(): number | null {
  try {
    const raw = new URL(window.location.href).searchParams.get("lake");
    if (!raw) return null;
    const id = Number.parseInt(raw, 10);
    return Number.isFinite(id) && id > 0 ? id : null;
  } catch {
    return null;
  }
}

function writeUrlParam(id: number | null): void {
  try {
    const url = new URL(window.location.href);
    if (id == null) url.searchParams.delete("lake");
    else url.searchParams.set("lake", String(id));
    history.replaceState(history.state, "", url.toString());
  } catch (err) {
    console.warn("[state] could not update the URL", err);
  }
}
