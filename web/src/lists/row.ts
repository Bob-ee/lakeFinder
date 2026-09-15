import type { Lake, Verdict } from "../types";
import { el } from "../ui/format";

/**
 * One list row: verdict dot, name, county, township. Shared by search results and (from
 * phase 2) the Nearest and Saved lists, which add distance and bearing on the right.
 */
export function lakeRow(
  lake: Lake,
  opts: { onSelect: (id: number) => void; trailing?: string },
): HTMLElement {
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

  row.append(dot, text);
  if (opts.trailing) row.append(el("span", "row-trailing", opts.trailing));

  row.addEventListener("click", () => opts.onSelect(lake.id));
  return row;
}
