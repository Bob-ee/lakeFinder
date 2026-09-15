import {
  FLAG_LABEL,
  MAC_NOT_LOADED_NOTICE,
  RESTRICTION_LABEL,
  VERDICT_PHRASE,
} from "../config";
import { describeRestriction } from "../pack/rules";
import type { Evaluation, Lake, Pack, Restriction } from "../types";
import { el, formatAcres, formatChordBearings, formatDate, formatFeet } from "../ui/format";
import { icon } from "../ui/icons";
import { toast } from "../ui/toast";

export interface DetailInput {
  lake: Lake;
  evaluation: Evaluation;
  restrictions: Restriction[];
  pack: Pack | null;
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
  sub.append(el("span", "peek-county", [lake.county, lake.township].filter(Boolean).join(" · ")));
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
export function renderDetail(input: DetailInput): HTMLElement {
  const { lake, evaluation, restrictions, pack } = input;
  const wrap = el("div", "detail");

  // -- half ---------------------------------------------------------------
  const facts = el("dl", "fact-grid");
  addFact(facts, "Longest chord", chordText(lake));
  addFact(facts, "Area", formatAcres(lake.area_acres));
  addFact(facts, "Public access", lake.access ?? "No known public access");
  if (evaluation.flags.includes("federal_overlay")) {
    addFact(facts, "Federal", "Inside a federal unit");
  }
  wrap.append(facts);

  if (evaluation.flags.length > 0) {
    const chips = el("div", "chips");
    for (const f of evaluation.flags) {
      const chip = el("span", "chip", FLAG_LABEL[f] ?? f);
      chip.dataset["flag"] = f;
      chips.append(chip);
    }
    wrap.append(chips);
  }

  const restrictionSection = el("section", "detail-section");
  restrictionSection.append(el("h3", "detail-h", "Restrictions"));
  if (restrictions.length === 0) {
    restrictionSection.append(
      el("p", "muted", "No DNR watercraft control matched to this lake."),
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
  return wrap;
}

function chordText(lake: Lake): string {
  const bearings = formatChordBearings(lake.chord_bearing_deg);
  return bearings ? `${formatFeet(lake.chord_ft)} · ${bearings}` : formatFeet(lake.chord_ft);
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
    `${lake.county}${lake.township ? ` · ${lake.township}` : ""}`,
    `Verdict: ${VERDICT_PHRASE[evaluation.verdict]} (${evaluation.verdict})`,
    `Chord ${chordText(lake)} · ${formatAcres(lake.area_acres)}`,
  ];
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
