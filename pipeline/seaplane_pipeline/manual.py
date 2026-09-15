"""Hand-maintained inputs: `data/manual/overrides.yaml` and `data/manual/mac_record.yaml`.

Also builds the two families of *synthetic* restriction records the contract calls for
(`parser: "manual"`), which the rules engine then treats exactly like a parsed DNR record:

- `mac_ordinance` / `mac_conditional` from the MAC seaplane record (R 259.401(13)), matched to a
  lake by name + county through the stage-3 matcher unless the entry already carries a `lake_id`.
- `federal_no_landing` for every lake whose centroid falls inside an NPS or USFWS unit
  (36 CFR 2.17), from the `overlay` stage output.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

import pandas as pd
import yaml

from . import ids
from .config import Config

log = logging.getLogger(__name__)

FEDERAL_SOURCE = "https://www.ecfr.gov/current/title-36/chapter-I/part-2/section-2.17"
MAC_KINDS_RESTRICTED = {"ordinance", "interim_order"}
MAC_STATUS_RESTRICTED = {"approved", "active", "in_effect", "in effect"}


def _scalar(value):
    """NaN/pandas-NA -> None, everything else to a plain Python string/scalar."""
    if value is None:
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):  # pragma: no cover - non-scalar
        pass
    return str(value) if isinstance(value, str) else (value.item() if hasattr(value, "item") else value)


def _load_yaml(path) -> dict:
    if not path.exists():
        log.info("%s not found; using empty defaults", path)
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def load_overrides(cfg: Config) -> dict:
    data = _load_yaml(cfg.manual_dir / "overrides.yaml")
    return {
        "matches": data.get("matches") or [],
        "unmatch": data.get("unmatch") or [],
        "verdicts": data.get("verdicts") or [],
    }


def load_mac_record(cfg: Config) -> dict:
    data = _load_yaml(cfg.manual_dir / "mac_record.yaml")
    return {"loaded": bool(data.get("loaded", False)), "entries": data.get("entries") or []}


def verdict_overrides(overrides: dict) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for row in overrides.get("verdicts") or []:
        if row.get("lake_id") is None or not row.get("verdict"):
            continue
        out[int(row["lake_id"])] = {"verdict": str(row["verdict"]), "note": row.get("note")}
    return out


def _record(
    *,
    county: str | None,
    lake_name_raw: str | None,
    township: str | None,
    raw_text: str,
    restriction_type: str,
    source_url: str | None,
    lake_ids: list[int],
    rule_id: str | None = None,
) -> dict:
    return {
        "restriction_id": ids.restriction_id(county, lake_name_raw, township, raw_text),
        "rule_id": rule_id,
        "county": county,
        "township": township,
        "lake_name_raw": lake_name_raw,
        "lake_name_norm": ids.normalize_name(lake_name_raw),
        "plss": [],
        "restriction_type": restriction_type,
        "scope": "lakewide",
        "scope_description": None,
        "hours": None,
        "season": None,
        "speed_mph": None,
        "status": "active",
        "clause": None,
        "signage_required": False,
        "related_rule_ids": [],
        "raw_text": raw_text,
        "source_url": source_url,
        "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "parser": "manual",
        "needs_review": False,
        "lake_ids": lake_ids,
    }


def mac_restriction_type(entry: dict) -> str:
    kind = str(entry.get("kind") or "").strip().lower()
    status = str(entry.get("status") or "").strip().lower()
    if kind in MAC_KINDS_RESTRICTED and status in MAC_STATUS_RESTRICTED:
        return "mac_ordinance"
    return "mac_conditional"


def mac_restrictions(cfg: Config, matcher=None) -> tuple[list[dict], bool]:
    """Synthetic restrictions from `mac_record.yaml`. Returns (records, loaded_flag)."""
    record = load_mac_record(cfg)
    out: list[dict] = []
    for entry in record["entries"]:
        lake_id = entry.get("lake_id")
        score = 1.0 if lake_id is not None else 0.0
        if lake_id is None and matcher is not None:
            lake_id, score = matcher.match_name_county(entry.get("lake_name"), entry.get("county"))
        if lake_id is None:
            log.warning(
                "MAC entry %r (%s County) did not match a lake; set lake_id by hand in mac_record.yaml",
                entry.get("lake_name"), entry.get("county"),
            )
            continue
        note = entry.get("note") or entry.get("citation") or "MAC seaplane record entry."
        rec = _record(
            county=entry.get("county"),
            lake_name_raw=entry.get("lake_name"),
            township=None,
            raw_text=note,
            restriction_type=mac_restriction_type(entry),
            source_url=entry.get("source_url"),
            lake_ids=[int(lake_id)],
        )
        rec["citation"] = entry.get("citation")
        rec["match_confidence"] = score
        out.append(rec)
    return out, record["loaded"]


def federal_restrictions(lakes, overlays: dict) -> list[dict]:
    """A `federal_no_landing` record per lake whose centroid sits inside an NPS/FWS unit."""
    by_id: dict[int, dict] = {}
    if lakes is not None:
        for row in lakes.itertuples(index=False):
            by_id[int(row.id)] = {
                "name": _scalar(getattr(row, "name", None)),
                "county": _scalar(getattr(row, "county", None)),
                "township": _scalar(getattr(row, "township", None)),
            }
    out: list[dict] = []
    for key, ov in overlays.items():
        unit = ov.get("federal_unit")
        if not unit:
            continue
        lake = by_id.get(int(key), {})
        name = lake.get("name")
        raw = (
            f"{unit}: 36 CFR 2.17 prohibits operating or using aircraft on lands or waters "
            "other than at locations designated for that purpose; no designated seaplane area is on file."
        )
        out.append(
            _record(
                county=lake.get("county"),
                lake_name_raw=name,
                township=lake.get("township"),
                raw_text=raw,
                restriction_type="federal_no_landing",
                source_url=FEDERAL_SOURCE,
                lake_ids=[int(key)],
            )
        )
    return out
