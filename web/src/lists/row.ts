import type { Lake, Verdict } from "../types";
import { el } from "../ui/format";

export interface LakeRowOptions {
  onSelect: (id: number) => void;
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
  text.append(el("span", "row-name", lake.name ?? "Unnamed waterbody"));
  const place = [lake.county, lake.township].filter(Boolean).join(" · ");
  text.append(el("span", "row-place", place));
  if (opts.note != null) text.append(slot("row-note", opts.note));

  row.append(dot, text);
  if (opts.trailing != null) row.append(slot("row-trailing", opts.trailing));

  row.addEventListener("click", () => opts.onSelect(lake.id));
  return row;
}

function slot(className: string, content: string | HTMLElement): HTMLElement {
  if (typeof content === "string") return el("span", className, content);
  content.classList.add(className);
  return content;
}
