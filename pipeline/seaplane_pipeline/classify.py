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
    records: list[dict], matches: list[dict], synthetic: list[dict] | None = None
) -> dict[str, list[dict]]:
    """Group active restriction records under the lake ids the matcher assigned."""
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
    federal_records = manual.federal_restrictions(lakes, overlays)
    synthetic = mac_records + federal_records
    (cfg.work_dir / "synthetic_restrictions.json").write_text(
        json.dumps(synthetic, indent=1), encoding="utf-8"
    )

    grouped = restrictions_by_lake(records, matches, synthetic)
    rules_path = Path(getattr(args, "rules", None) or (cfg.rules_dir / "rules.json"))
    results = run_engine(
        rules_path,
        lake_inputs(lakes, overlays),
        grouped,
        mac_loaded=mac_loaded,
        node=getattr(args, "node", "node"),
    )

    overrides = manual.verdict_overrides(manual.load_overrides(cfg))
    applied = apply_verdict_overrides(results, overrides)

    verdicts = {str(r["id"]): r for r in results}
    (cfg.work_dir / "verdicts.json").write_text(json.dumps(verdicts, indent=1), encoding="utf-8")

    hist: dict[str, int] = {}
    for r in results:
        hist[r["verdict"]] = hist.get(r["verdict"], 0) + 1
    log.info(
        "classified %d lakes in %.1fs: %s (%d MAC + %d federal synthetic records, %d verdict overrides)",
        len(results),
        time.monotonic() - started,
        ", ".join(f"{k}={v}" for k, v in sorted(hist.items())),
        len(mac_records),
        len(federal_records),
        applied,
    )
    return 0
