import * as engine from "virtual:rules-engine";
import { RESTRICTION_LABEL } from "../config";
import type { Evaluation, Lake, Reason, Restriction, RulesFile, Verdict } from "../types";

/**
 * Client-side verdict. The pipeline prebakes `verdict` into index.json, but the client
 * re-runs the same shared engine against /data/rules.json so a rules change ships without
 * a tile rebuild (design.md section 5 stage 6). If the engine is not importable, or it
 * throws on this input, the prebaked verdict stands.
 */
export class RulesRunner {
  private compiled: unknown = null;
  private ready = false;
  /** From pack.json. The engine adds the `mac_pending` flag when this is false. */
  private macRecordLoaded = false;

  get available(): boolean {
    return this.ready;
  }

  get version(): string | null {
    return this.rulesFile?.version ?? null;
  }

  private rulesFile: RulesFile | null = null;

  init(rules: RulesFile | null, macRecordLoaded: boolean): void {
    this.rulesFile = rules;
    this.macRecordLoaded = macRecordLoaded;
    if (!rules) return;
    if (!engine.engineAvailable || typeof engine.evaluateLake !== "function") {
      console.warn(
        "[rules] shared engine (rules/engine/index.js) unavailable; using prebaked verdicts from index.json",
      );
      return;
    }
    try {
      this.compiled = typeof engine.loadRules === "function" ? engine.loadRules(rules) : rules;
      // loadRules may be a file loader in some builds; fall back to the parsed object.
      if (this.compiled == null) this.compiled = rules;
      this.ready = true;
    } catch (err) {
      console.warn("[rules] loadRules failed; using prebaked verdicts", err);
      this.compiled = null;
      this.ready = false;
    }
  }

  evaluate(lake: Lake, restrictions: Restriction[]): Evaluation {
    if (this.ready && typeof engine.evaluateLake === "function") {
      try {
        const result = engine.evaluateLake(
          {
            id: lake.id,
            name: lake.name,
            chord_ft: lake.chord_ft,
            area_acres: lake.area_acres,
            public_access: lake.access != null,
            federal_unit: lake.flags.includes("federal_overlay") ? lake.county : null,
          },
          restrictions,
          this.compiled,
          { mac_record_loaded: this.macRecordLoaded },
        ) as Partial<Evaluation> | null;
        if (result && typeof result.verdict === "string") {
          return {
            id: lake.id,
            verdict: result.verdict,
            reasons: Array.isArray(result.reasons) ? result.reasons : [],
            flags: Array.isArray(result.flags) ? result.flags : [...lake.flags],
            fromEngine: true,
          };
        }
        console.warn("[rules] engine returned an unusable result; using prebaked verdict", result);
      } catch (err) {
        console.warn(`[rules] evaluateLake threw for lake ${lake.id}; using prebaked verdict`, err);
      }
    }
    return this.fallback(lake, restrictions);
  }

  /**
   * No engine: trust the prebaked verdict and describe each restriction from its own
   * fields rather than inventing rule notes.
   */
  private fallback(lake: Lake, restrictions: Restriction[]): Evaluation {
    const reasons: Reason[] = restrictions.map((r) => ({
      restriction_id: r.restriction_id,
      rule_id: r.rule_id,
      matched_rule: "",
      verdict: "unknown" as Verdict,
      note: describeRestriction(r),
    }));
    return {
      id: lake.id,
      verdict: lake.verdict,
      reasons,
      flags: [...lake.flags],
      fromEngine: false,
    };
  }
}

/** A plain-language line built from the restriction record itself, no rules involved. */
export function describeRestriction(r: Restriction): string {
  const parts = [RESTRICTION_LABEL[r.restriction_type] ?? r.restriction_type];
  if (r.restriction_type === "speed_limit" && r.speed_mph != null) {
    parts[0] = `${r.speed_mph} mph limit`;
  }
  parts.push(r.scope === "zone" ? (r.scope_description ?? "in a marked zone") : "lakewide");
  if (r.hours) parts.push(r.hours.text);
  if (r.season) parts.push(r.season.text);
  return parts.join(" · ");
}
