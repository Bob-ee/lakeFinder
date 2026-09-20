"""Stage 6 (`classify`): run the shared JS rules engine over every lake.

Input:  `lakes.parquet`, `matches.json`, `restrictions.jsonl`, `overlays.json`, the manual files.
Output: `data/work/verdicts.json` (keyed by lake id) and `data/work/synthetic_restrictions.json`
        (the `mac_*` / `federal_no_landing` records this stage invents, so `build` can publish them
        alongside the parsed ones).

The engine is `rules/engine/cli.js`, invoked **once** for the whole state over stdin/stdout, so the
pipeline and the client are running byte-identical logic (docs/design.md section 6). Manual verdict
overrides from `overrides.yaml` are applied afterwards, on top of the engine's answer.

A low-confidence match (0.5-0.8, see `match.py`) is applied but carries `match_confidence` and sets
`needs_review` on the restriction copy handed to the engine, so the lake surfaces in `review` and
gets the `needs_review` flag in the index.

The one synthetic record this stage withholds is `federal_no_landing` on a `great_lake` or
`connecting_water`. `overlay` sets `federal_unit` on those kinds by intersection, so it means "part
of this water is in the unit" -- the Michigan Islands refuge over a handful of islands in Lake
Michigan, the Detroit River International Wildlife Refuge over shoals in the Detroit River. Turning
that into a lakewide 36 CFR 2.17 record would mark a Great Lake restricted on a technicality. The
`federal_overlay` flag still shows, and the client words it "part of this water" off `kind`
(docs/data-contract.md, `index.json`).
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd

from . import manual, match
from .config import Config

log = logging.getLogger(__name__)


def add_args(sp) -> None:
    sp.add_argument("--rules", help="Override the rules.json path")
    sp.add_argument("--node", default="node", help="Node executable")


def _clean(value):
    """NaN/NaT/pandas-NA -> None, numpy scalars -> plain Python."""
    if value is None:
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):  # arrays and other non-scalars
        pass
    return value.item() if hasattr(value, "item") else value


def lake_inputs(lakes: gpd.GeoDataFrame, overlays: dict) -> list[dict]:
    """The `{id, name, chord_ft, area_acres, public_access, federal_unit}` shape the engine wants."""
    out = []
    for row in lakes.itertuples(index=False):
        ov = overlays.get(str(int(row.id))) or {}
        name = _clean(getattr(row, "name", None))
        out.append(
            {
                "id": int(row.id),
                "name": str(name) if name else None,
                "chord_ft": _clean(getattr(row, "chord_ft", None)),
                "area_acres": _clean(getattr(row, "area_acres", None)),
                "public_access": bool(ov.get("public_access", False)),
                "federal_unit": ov.get("federal_unit"),
            }
        )
    return out


def restrictions_by_lake(
    records: list[dict],
    matches: list[dict],
    synthetic: list[dict] | None = None,
    big_water_partial: set[str] | None = None,
) -> dict[str, list[dict]]:
    """Group active restriction records under the lake ids the matcher assigned.

    `big_water_partial` is the id set from `match.big_water_partial_ids`. It has to be stamped on
    the copies handed to the engine here as well as on the published records in `build`, or the
    prebaked verdict in `index.json` and the client's re-run of the engine would disagree on exactly
    the water bodies the cap exists for.
    """
    partial = big_water_partial or set()
    by_id = {r["restriction_id"]: r for r in records}
    grouped: dict[str, list[dict]] = {}
    for m in matches:
        rec = by_id.get(m["restriction_id"])
        if rec is None or rec.get("status", "active") != "active":
            continue
        copy = dict(rec)
        copy["match_confidence"] = m.get("score")
        copy["match_method"] = m.get("method")
        if m.get("needs_review"):
            copy["needs_review"] = True
        if str(m["restriction_id"]) in partial:
            copy["big_water_partial"] = True
        grouped.setdefault(str(int(m["lake_id"])), []).append(copy)
    for rec in synthetic or []:
        for lake_id in rec.get("lake_ids") or []:
            grouped.setdefault(str(int(lake_id)), []).append(rec)
    return grouped


def run_engine(
    rules_path: Path,
    lakes: list[dict],
    restrictions: dict[str, list[dict]],
    mac_loaded: bool | None = None,
    node: str = "node",
    cli_js: Path | None = None,
) -> list[dict]:
    """Run `rules/engine/cli.js` once over everything."""
    cli_js = cli_js or Path(rules_path).parent / "engine" / "cli.js"
    cmd = [node, str(cli_js), "--rules", str(rules_path)]
    if mac_loaded is not None:
        cmd += ["--mac-loaded", "true" if mac_loaded else "false"]
    payload = json.dumps({"lakes": lakes, "restrictions": restrictions})
    proc = subprocess.run(cmd, input=payload, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"rules engine failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def apply_reach_flags(results: list[dict], matches: list[dict]) -> int:
    """Add the `reach_unresolved` flag to lakes carrying an unnarrowed river rule.

    Display only, which is why it is bolted on here instead of taught to the rules engine: the
    verdict for a river reach is whatever the DNR rule says, exactly as for a lake. The flag tells
    the client to say "this rule covers part of this river" next to the restriction list.
    """
    flagged = {int(m["lake_id"]) for m in matches if m.get("reach_unresolved")}
    applied = 0
    for result in results:
        if int(result["id"]) in flagged and "reach_unresolved" not in result.get("flags", []):
            result.setdefault("flags", []).append("reach_unresolved")
            applied += 1
    return applied


def drop_big_water_federal(records: list[dict], lakes) -> list[dict]:
    """Withhold `federal_no_landing` on `great_lake` / `connecting_water`; see the module docstring.

    A federal record carries every water body that hashed to its id (`manual.federal_restrictions`),
    so big water is withheld id by id *and* lake by lake: a record covering both keeps only its
    inland ids, and is dropped entirely when nothing inland is left.
    """
    kinds = match.kinds_by_lake(lakes)
    kept, dropped = [], 0
    for rec in records:
        ids = [int(i) for i in rec.get("lake_ids") or []]
        inland = [i for i in ids if kinds.get(i) not in match.BIG_WATER_KINDS]
        if ids and not inland:
            dropped += 1
            continue
        if len(inland) != len(ids):
            rec = {**rec, "lake_ids": inland}
        kept.append(rec)
    if dropped:
        log.info("withheld %d federal_no_landing records on big water (flag only, no verdict)", dropped)
    return kept


def apply_verdict_overrides(results: list[dict], overrides: dict[int, dict]) -> int:
    applied = 0
    for result in results:
        override = overrides.get(int(result["id"]))
        if not override:
            continue
        result["verdict"] = override["verdict"]
        result.setdefault("reasons", []).append(
            {
                "restriction_id": None,
                "rule_id": None,
                "matched_rule": "manual-override",
                "verdict": override["verdict"],
                "note": override.get("note") or "Manual verdict override (data/manual/overrides.yaml).",
            }
        )
        applied += 1
    return applied


def run(cfg: Config, args) -> int:
    started = time.monotonic()
    lakes_path = cfg.work_dir / "lakes.parquet"
    if not lakes_path.exists():
        log.error("%s not found; run `seaplane geometry` first", lakes_path)
        return 2
    lakes = gpd.read_parquet(lakes_path)

    overlays_path = cfg.work_dir / "overlays.json"
    overlays = json.loads(overlays_path.read_text()) if overlays_path.exists() else {}
    if not overlays:
        log.warning("overlays.json missing; public_access/federal_unit will be empty")

    matches_path = cfg.work_dir / "matches.json"
    matches = json.loads(matches_path.read_text()) if matches_path.exists() else []
    rpath = cfg.work_dir / "restrictions.jsonl"
    records = match.read_restrictions(rpath) if rpath.exists() else []
    if not records:
        log.warning("%s missing or empty; classifying with no parsed restrictions", rpath)

    matcher = match.Matcher(lakes) if len(lakes) else None
    mac_records, mac_loaded = manual.mac_restrictions(cfg, matcher)
    federal_records = drop_big_water_federal(manual.federal_restrictions(lakes, overlays), lakes)

    synthetic = mac_records + federal_records
    (cfg.work_dir / "synthetic_restrictions.json").write_text(
        json.dumps(synthetic, indent=1), encoding="utf-8"
    )

    kinds = match.kinds_by_lake(lakes)
    grouped = restrictions_by_lake(
        records, matches, synthetic, match.big_water_partial_ids(matches, kinds)
    )
    rules_path = Path(getattr(args, "rules", None) or (cfg.rules_dir / "rules.json"))
    results = run_engine(
        rules_path,
        lake_inputs(lakes, overlays),
        grouped,
        mac_loaded=mac_loaded,
        node=getattr(args, "node", "node"),
    )

    reaches = apply_reach_flags(results, matches)
    overrides = manual.verdict_overrides(manual.load_overrides(cfg))
    applied = apply_verdict_overrides(results, overrides)

    verdicts = {str(r["id"]): r for r in results}
    (cfg.work_dir / "verdicts.json").write_text(json.dumps(verdicts, indent=1), encoding="utf-8")

    hist: dict[str, int] = {}
    for r in results:
        hist[r["verdict"]] = hist.get(r["verdict"], 0) + 1
    log.info(
        "classified %d waterbodies in %.1fs: %s (%d MAC + %d federal synthetic records, "
        "%d verdict overrides, %d reach_unresolved)",
        len(results),
        time.monotonic() - started,
        ", ".join(f"{k}={v}" for k, v in sorted(hist.items())),
        len(mac_records),
        len(federal_records),
        applied,
        reaches,
    )
    return 0
