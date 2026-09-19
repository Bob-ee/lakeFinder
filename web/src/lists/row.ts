import { KIND_LABEL } from "../config";
import type { Lake, Verdict } from "../types";
import { el } from "../ui/format";

export interface LakeRowOptions {
  onSelect: (id: number) => void;
  /**
   * Appended to the name in a quieter weight: the briefing uses it for the calmest region,
   * so a row reads "Cass Lake · west end" without the region looking like part of the name.
   */
  nameSuffix?: string;
  /**
   * Right-hand slot. A string is the usual one-liner (distance, bearing); the briefing
   * passes an element so it can stack wave height and run over distance and bearing.
   */
  trailing?: string | HTMLElement;
  /** Extra line under the county, e.g. the briefing's score and limiting factor. */
  note?: string | HTMLElement;
}

/**
 * One list row: verdict dot, name, county, township. Shared by search results, the
 * briefing's ranked lakes and (from phase 2) the Nearest and Saved lists.
 */
export function lakeRow(lake: Lake, opts: LakeRowOptions): HTMLElement {
  const row = el("button", "row");
  row.type = "button";
  row.dataset["lakeId"] = String(lake.id);
  row.setAttribute("role", "option");

  const dot = el("span", "verdict-dot");
  dot.dataset["verdict"] = lake.verdict satisfies Verdict;
  dot.setAttribute("aria-hidden", "true");

  const text = el("span", "row-text");
  const name = el("span", "row-name", lake.name ?? "Unnamed waterbody");
  if (opts.nameSuffix) {
    // The region is the point of a briefing row ("where on this lake"), so it is allowed to
    // wrap onto a second line rather than be the first thing an ellipsis eats.
    name.classList.add("has-suffix");
    name.append(el("span", "row-name-suffix", ` · ${opts.nameSuffix}`));
  }
  text.append(name);
  // "River · Ionia · Lyons Township": a river is digitized as several same-name polygons, so the
  // place line is what tells two search hits apart (the reach length is added by the caller when
  // even that is not enough). Big water has no single county and carries `counties` instead.
  const place = [KIND_LABEL[lake.kind], placeOf(lake), lake.township].filter(Boolean).join(" · ");
  text.append(el("span", "row-place", place));
  if (opts.note != null) text.append(slot("row-note", opts.note));

  row.append(dot, text);
  if (opts.trailing != null) row.append(slot("row-trailing", opts.trailing));

  row.addEventListener("click", () => opts.onSelect(lake.id));
  return row;
}

/** One county, or the first of the many a Great Lake touches. */
function placeOf(lake: Lake): string {
  if (lake.county) return lake.county;
  const counties = lake.counties ?? [];
  if (counties.length === 0) return "";
  return counties.length === 1 ? counties[0]! : `${counties[0]!} +${counties.length - 1}`;
}

function slot(className: string, content: string | HTMLElement): HTMLElement {
  if (typeof content === "string") return el("span", className, content);
  content.classList.add(className);
  return content;
}
