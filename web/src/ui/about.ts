import { DISCLAIMER, STORAGE_KEYS } from "../config";
import type { AppState } from "../state";
import { openDialog } from "./dialog";
import { el, formatDate } from "./format";
import { readLocal, writeLocal, type Theme, type ThemeChoice } from "./theme";

/** design.md 13, with `{version}` from pack.json. */
export function disclaimerText(version: string | null): string {
  return DISCLAIMER.replace("{version}", version ?? "not loaded");
}

/**
 * First-run dialog. Acknowledgement is keyed to the disclaimer's own text, so a wording
 * change re-prompts rather than silently passing as already read.
 */
export function maybeShowFirstRun(state: AppState): void {
  const version = state.pack?.version ?? null;
  const stamp = `v1:${version ?? "none"}`;
  if (readLocal(STORAGE_KEYS.disclaimerAck) === stamp) return;

  const body = el("div", "prose");
  body.append(el("p", undefined, disclaimerText(version)));
  body.append(
    el(
      "p",
      "muted",
      "Add this page to your home screen to keep it available as an app. Offline data " +
        "packs arrive in phase 3.",
    ),
  );
  openDialog({
    title: "Before you use this",
    body,
    dismissable: false,
    primary: {
      label: "I understand",
      onClick: () => writeLocal(STORAGE_KEYS.disclaimerAck, stamp),
    },
  });
}

/** Settings and About: theme override, data pack facts, the same disclaimer. */
export function openAbout(state: AppState, theme: Theme): void {
  const body = el("div", "prose");

  const themeSection = el("section", "about-section");
  themeSection.append(el("h3", "detail-h", "Appearance"));
  const group = el("div", "segmented");
  group.setAttribute("role", "radiogroup");
  group.setAttribute("aria-label", "Theme");
  const choices: Array<[ThemeChoice, string]> = [
    ["system", "System"],
    ["light", "Light"],
    ["dark", "Dark"],
  ];
  for (const [value, label] of choices) {
    const btn = el("button", "segment", label);
    btn.type = "button";
    btn.setAttribute("role", "radio");
    btn.setAttribute("aria-checked", String(theme.current === value));
    btn.classList.toggle("is-active", theme.current === value);
    btn.addEventListener("click", () => {
      theme.set(value);
      for (const other of group.querySelectorAll<HTMLElement>(".segment")) {
        const on = other === btn;
        other.classList.toggle("is-active", on);
        other.setAttribute("aria-checked", String(on));
      }
    });
    group.append(btn);
  }
  themeSection.append(group);
  body.append(themeSection);

  const dataSection = el("section", "about-section");
  dataSection.append(el("h3", "detail-h", "Data pack"));
  const facts = el("dl", "fact-grid");
  const pack = state.pack;
  fact(facts, "Build", pack ? formatDate(pack.version) : "not loaded");
  fact(facts, "Rules", pack?.rules_version ?? "not loaded");
  fact(facts, "Lakes", pack ? String(pack.counts.lakes) : String(state.search.size));
  fact(facts, "Restrictions", pack ? String(pack.counts.restrictions) : "unknown");
  fact(
    facts,
    "MAC record",
    pack?.mac_record_loaded ? "loaded" : "MAC-approved seaplane ordinances not yet loaded",
  );
  fact(facts, "Rules engine", state.rules.available ? "running in browser" : "prebaked verdicts");
  dataSection.append(facts);
  if (state.missing.length > 0) {
    dataSection.append(
      el("p", "muted small", `Not found under /data: ${state.missing.join(", ")}`),
    );
  }
  body.append(dataSection);

  const legal = el("section", "about-section");
  legal.append(el("h3", "detail-h", "Disclaimer"));
  legal.append(el("p", undefined, disclaimerText(pack?.version ?? null)));
  body.append(legal);

  const version = el("p", "muted small", `App ${__APP_VERSION__} · phase 1, static map`);
  body.append(version);

  openDialog({ title: "Settings and about", body });
}

function fact(list: HTMLElement, label: string, value: string): void {
  list.append(el("dt", undefined, label), el("dd", undefined, value));
}
