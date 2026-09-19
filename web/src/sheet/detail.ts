import {
  KIND_LABEL,
  MAC_NOT_LOADED_NOTICE,
  RESTRICTION_LABEL,
  VERDICT_PHRASE,
  flagLabel,
  reachUnresolvedNotice,
} from "../config";
import { homeWater } from "../briefing/home-water";
import { describeRestriction } from "../pack/rules";
import type { Evaluation, Lake, Pack, Restriction } from "../types";
import { isBigWater } from "../types";
import {
  el,
  formatAcres,
  formatChordBearings,
  formatDate,
  formatDistanceFt,
} from "../ui/format";
import { icon } from "../ui/icons";
import { toast } from "../ui/toast";

export interface DetailInput {
  lake: Lake;
  evaluation: Evaluation;
  restrictions: Restriction[];
  pack: Pack | null;
}

/** The detail panel plus the slot the wave-field section is mounted into when there is one. */
export interface DetailRender {
  element: HTMLElement;
  water: HTMLElement;
}

/** Peek row (design.md 7.4): colour bar, name, county, one-line phrase, star. No sentences. */
export function renderPeek(input: DetailInput, into: HTMLElement): void {
  const { lake, evaluation } = input;
  into.replaceChildren();
  into.dataset["verdict"] = evaluation.verdict;

  const bar = el("div", "verdict-bar");
  bar.dataset["verdict"] = evaluation.verdict;

  const text = el("div", "peek-text");
  const title = el("div", "peek-title", lake.name ?? "Unnamed waterbody");
  const sub = el("div", "peek-sub");
  sub.append(
    el("span", "peek-county", [placeLabel(lake), lake.township].filter(Boolean).join(" · ")),
  );
  const phrase = el("span", "peek-verdict", VERDICT_PHRASE[evaluation.verdict]);
  phrase.dataset["verdict"] = evaluation.verdict;
  sub.append(phrase);
  text.append(title, sub);

  // Phase 4 owns saved lakes; the slot is here so the layout does not move later.
  const star = el("button", "icon-btn star-btn");
  star.type = "button";
  star.innerHTML = icon("star");
  star.title = "Saved lakes arrive in phase 4";
  star.setAttribute("aria-label", "Save lake (phase 4)");
  star.addEventListener("click", (e) => {
    e.stopPropagation();
    toast("Saved lakes arrive in phase 4");
  });

  into.append(bar, text, star);
}

/** Half and full content. CSS reveals the `--full` sections only at the full snap point. */
export function renderDetail(input: DetailInput): DetailRender {
  const { lake, evaluation, restrictions, pack } = input;
  const wrap = el("div", "detail");

  // -- half ---------------------------------------------------------------
  const facts = el("dl", "fact-grid");
  // On a river or a connecting water the chord is the longest straight reach of open water,
  // so say so: "chord" on a 30-mile river reads as the whole river.
  addFact(facts, reachLike(lake) ? "Longest reach" : "Longest chord", chordText(lake));
  addFact(facts, "Area", formatAcres(lake.area_acres));
  // On big water a launch is one launch on a hundred miles of shore, and the absence of one
  // in the data says nothing about the rest of it. Both directions need different words.
  if (isBigWater(lake.kind)) {
    addFact(facts, "Launch on this water", lake.access ?? "None on record");
  } else {
    addFact(facts, "Public access", lake.access ?? "No known public access");
  }
  if (evaluation.flags.includes("federal_overlay")) {
    addFact(facts, "Federal", flagLabel("federal_overlay", lake.kind, lake.federal_unit));
  }
  wrap.append(facts);

  if (evaluation.flags.length > 0) {
    const chips = el("div", "chips");
    for (const f of evaluation.flags) {
      const chip = el("span", "chip", flagLabel(f, lake.kind, lake.federal_unit));
      chip.dataset["flag"] = f;
      chips.append(chip);
    }
    wrap.append(chips);
  }

  wrap.append(homeWaterRow(lake));

  // Filled asynchronously once wave_points.bin has answered; empty and invisible when this
  // water body has no wave field, which is most of them.
  const water = el("div", "water-slot");
  wrap.append(water);

  const restrictionSection = el("section", "detail-section");
  restrictionSection.append(el("h3", "detail-h", "Restrictions"));
  if (restrictions.some((r) => r.reach_unresolved)) {
    const notice = el("div", "notice notice--warn", reachUnresolvedNotice(lake.kind));
    restrictionSection.append(notice);
  }
  if (restrictions.length === 0) {
    restrictionSection.append(
      el("p", "muted", `No DNR watercraft control matched to this ${nounFor(lake)}.`),
    );
  } else {
    const list = el("ul", "restriction-list");
    for (const r of restrictions) {
      list.append(restrictionRow(r, evaluation));
    }
    restrictionSection.append(list);
  }
  wrap.append(restrictionSection);

  if (!evaluation.fromEngine) {
    wrap.append(
      el(
        "p",
        "muted small",
        "Verdict shown as built by the pipeline; the in-browser rules engine is not loaded.",
      ),
    );
  }

  // -- full ---------------------------------------------------------------
  const full = el("div", "detail-section detail-section--full");

  const mac = el("div", "notice");
  if (pack && pack.mac_record_loaded === false) {
    mac.classList.add("notice--warn");
    mac.textContent = MAC_NOT_LOADED_NOTICE;
  } else {
    const macReason = evaluation.reasons.find((r) => r.matched_rule.startsWith("mac"));
    mac.textContent = macReason
      ? `MAC record: ${macReason.note}`
      : "No MAC record entry for this lake.";
  }
  full.append(el("h3", "detail-h", "MAC record"), mac);

  full.append(el("h3", "detail-h", "Source text"));
  if (restrictions.length === 0) {
    full.append(el("p", "muted", "Nothing to cite."));
  } else {
    for (const r of restrictions) full.append(sourceBlock(r));
  }

  const meta = el("dl", "fact-grid");
  addFact(meta, "Data build", pack ? formatDate(pack.version) : "unknown");
  addFact(meta, "Rules version", pack?.rules_version ?? "unknown");
  addFact(meta, "Lake id", String(lake.id));
  full.append(el("h3", "detail-h", "Data"), meta);

  const copy = el("button", "btn", "Copy report");
  copy.type = "button";
  copy.innerHTML = `${icon("copy")}<span>Copy report</span>`;
  copy.addEventListener("click", () => {
    void copyReport(input);
  });
  full.append(copy);

  wrap.append(full);
  return { element: wrap, water };
}

/**
 * "Oakland" for a lake, "River · Oakland" for a river, "Great Lakes water" for a Great
 * Lake. `kind` leads because it is the surprise. Big water has no single county, so it
 * shows the counties it touches when the pipeline listed them and nothing when it did not.
 */
export function placeLabel(lake: Lake): string {
  return [KIND_LABEL[lake.kind], countyLabel(lake)].filter(Boolean).join(" · ");
}

/** `county`, or the first few of `counties` on a water body that spans many. */
export function countyLabel(lake: Lake): string {
  if (lake.county) return lake.county;
  const counties = lake.counties ?? [];
  if (counties.length === 0) return "";
  if (counties.length <= 3) return counties.join(", ");
  return `${counties.slice(0, 3).join(", ")} +${counties.length - 3}`;
}

/** The word for this water body in a sentence. */
function nounFor(lake: Lake): string {
  if (lake.kind === "river") return "river";
  if (isBigWater(lake.kind)) return "water";
  return "lake";
}

/** True where the long dimension is a reach along the water rather than a chord across it. */
function reachLike(lake: Lake): boolean {
  return lake.kind === "river" || lake.kind === "connecting_water";
}

/**
 * "Make this my home water": one water body that is always briefed, whatever the search
 * radius says (contract, `settings.home_water`). It writes the whole settings object
 * through `/api/settings`, so it only appears when the briefing service answered; with no
 * service there is nothing to write to and the row stays out of the way.
 */
function homeWaterRow(lake: Lake): HTMLElement {
  const row = el("div", "detail-actions");
  row.hidden = true;
  const btn = el("button", "btn home-water-btn");
  btn.type = "button";
  row.append(btn);

  let busy = false;
  const paint = (): void => {
    const current = homeWater.current;
    if (homeWater.serviceUp === false) {
      row.hidden = true;
      return;
    }
    if (current === undefined) return; // still probing; stay hidden rather than flicker
    row.hidden = false;
    const isHome = current != null && current.id === lake.id;
    btn.classList.toggle("is-on", isHome);
    btn.textContent = busy
      ? "Saving…"
      : isHome
        ? "Home water ✓ (remove)"
        : "Make this my home water";
    btn.disabled = busy;
    btn.title = isHome
      ? "This water body is in every briefing, whatever the search radius says"
      : "Keep this water body in every briefing, whatever the search radius says";
  };

  const unsubscribe = homeWater.onChange(() => {
    if (!row.isConnected && !btn.isConnected) {
      unsubscribe();
      return;
    }
    paint();
  });
  void homeWater.load().then(paint);

  btn.addEventListener("click", () => {
    void (async () => {
      const current = homeWater.current;
      const isHome = current != null && current.id === lake.id;
      busy = true;
      paint();
      try {
        await homeWater.set(isHome ? null : { id: lake.id, name: lake.name ?? `Water ${lake.id}` });
        toast(isHome ? "Home water cleared" : `${lake.name ?? "This water"} is now your home water`);
      } catch (err) {
        console.warn("[home water] could not save", err);
        toast("Could not reach the briefing service");
      } finally {
        busy = false;
        paint();
      }
    })();
  });

  paint();
  return row;
}

function chordText(lake: Lake): string {
  const bearings = formatChordBearings(lake.chord_bearing_deg);
  const length = formatDistanceFt(lake.chord_ft);
  return bearings ? `${length} · ${bearings}` : length;
}

function addFact(list: HTMLElement, label: string, value: string): void {
  list.append(el("dt", undefined, label), el("dd", undefined, value));
}

function restrictionRow(r: Restriction, evaluation: Evaluation): HTMLElement {
  const li = el("li", "restriction");
  const reason = evaluation.reasons.find((x) => x.restriction_id === r.restriction_id);
  if (reason && reason.verdict !== "unknown") li.dataset["verdict"] = reason.verdict;

  li.append(el("div", "restriction-type", RESTRICTION_LABEL[r.restriction_type] ?? r.restriction_type));

  const scope = el("div", "restriction-scope");
  scope.append(
    el("span", "tag", r.scope === "lakewide" ? "Lakewide" : "Zone"),
  );
  if (r.scope === "zone" && r.scope_description) {
    scope.append(el("span", undefined, r.scope_description));
  }
  li.append(scope);

  if (r.hours) {
    const days = r.hours.days ? ` (${r.hours.days})` : "";
    li.append(el("div", "restriction-when", `Hours: ${r.hours.text}${days}`));
  }
  if (r.season) li.append(el("div", "restriction-when", `Season: ${r.season.text}`));
  if (r.speed_mph != null) li.append(el("div", "restriction-when", `Limit: ${r.speed_mph} mph`));
  if (r.signage_required) {
    li.append(el("div", "restriction-when", "Enforceable only where signed or buoyed"));
  }
  if (reason?.note) li.append(el("div", "restriction-note", reason.note));
  else li.append(el("div", "restriction-note", describeRestriction(r)));
  return li;
}

function sourceBlock(r: Restriction): HTMLElement {
  const box = el("article", "source-block");
  const head = el("div", "source-head");
  const ruleId = `${r.rule_id ?? "no rule number"}${r.clause ? ` ${r.clause}` : ""}`;
  head.append(el("span", "rule-id", ruleId));
  const link = el("a", "source-link");
  link.href = r.source_url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.innerHTML = `<span>DNR page</span>${icon("external")}`;
  head.append(link);
  box.append(head);
  box.append(el("p", "raw-text", r.raw_text));
  if (r.related_rule_ids.length > 0) {
    box.append(el("div", "muted small", `See also ${r.related_rule_ids.join(", ")}`));
  }
  box.append(el("div", "muted small", `restriction ${r.restriction_id}`));
  return box;
}

/** design.md 7.4: copies lake id and rule ids. Plain text, so it pastes into anything. */
async function copyReport(input: DetailInput): Promise<void> {
  const { lake, evaluation, restrictions } = input;
  const lines = [
    `${lake.name ?? "Unnamed waterbody"} (id ${lake.id})`,
    `${placeLabel(lake)}${lake.township ? ` · ${lake.township}` : ""}`,
    `Verdict: ${VERDICT_PHRASE[evaluation.verdict]} (${evaluation.verdict})`,
    `${reachLike(lake) ? "Reach" : "Chord"} ${chordText(lake)} · ${formatAcres(lake.area_acres)}`,
  ];
  if (restrictions.some((r) => r.reach_unresolved)) lines.push(reachUnresolvedNotice(lake.kind));
  if (restrictions.length > 0) {
    lines.push("Restrictions:");
    for (const r of restrictions) {
      lines.push(
        `  ${r.restriction_id}${r.rule_id ? ` (${r.rule_id}${r.clause ? ` ${r.clause}` : ""})` : ""} - ${RESTRICTION_LABEL[r.restriction_type] ?? r.restriction_type}`,
      );
    }
  } else {
    lines.push("Restrictions: none matched");
  }
  lines.push("Not legal advice. Verify before operating.");
  const text = lines.join("\n");
  try {
    await navigator.clipboard.writeText(text);
    toast("Report copied");
  } catch {
    // Clipboard API needs a secure context; fall back to a selectable prompt.
    const ta = el("textarea", "copy-fallback");
    ta.value = text;
    document.body.append(ta);
    ta.select();
    const ok = document.execCommand?.("copy") ?? false;
    ta.remove();
    toast(ok ? "Report copied" : "Copy failed; use HTTPS or select the text manually");
  }
}
