"""Stage 3 (`match`): join parsed restrictions to lake polygons.

Input:  `data/work/restrictions.jsonl` (stage `parse-dnr`), `data/work/lakes.parquet` (stage
        `geometry`), `data/cache/plss_sections.geojson`, `data/manual/overrides.yaml`.
Output: `data/work/matches.json` and `data/work/unmatched.json`.

Candidate selection
-------------------
- With PLSS: the union of the rule's sections, buffered by 1 km (`--buffer-km`); every lake polygon
  intersecting that buffer is a candidate. The contract stores township/range unpadded (`"4N"`,
  `"7E"`) while the PLSS layer pads to two digits (`"04N"`, `"07E"`), so both sides are padded onto
  the layer's `TWNRNGSEC` key before joining (`docs/gis-sources.md` section 2).
- Without PLSS (or when the sections are not in the layer): every named lake in the same county.

Scoring
-------
| signal | score |
|---|---|
| `name_norm` exact | 1.0 |
| qualifier-stripped names equal (big/little/north/south/upper/lower/east/west/middle) | 0.6 |
| token overlap | 0.4 x Jaccard |
| near-identical spelling (difflib ratio >= 0.85) | 0.5 x ratio |
| lake intersects the section itself, not just the 1 km buffer | +0.2 |
| county matches | +0.1 |

The spelling tier exists because the DNR's names and the hydrography layer's names disagree on real
lakes -- "Stoney Creek Lake" vs "Stony Creek Lake", "Darb Lake" vs "Darby Lake". A ratio of exactly
1.0 is impossible here (identical strings take the exact tier), so a fuzzy score is always below
0.5 and a fuzzy match is always below the 0.8 auto-accept even with both bonuses: it only ever
lands in the 0.5-0.8 review band, where a human confirms it or drops it in `overrides.yaml`.

Best candidate wins. >= 0.8 accepted; 0.5-0.8 accepted but flagged `needs_review` and carried into
the output as `match_confidence`; below 0.5 unmatched. `overrides.yaml` wins over all of it.

Two restrictions (e.g. the same rule text published by two counties for a lake that straddles the
line) can and do land on the same lake id; nothing here deduplicates by lake.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import shapely

from . import gis, ids, manual
from .config import Config

log = logging.getLogger(__name__)

MEASURE_CRS = "EPSG:3078"
WGS84 = "EPSG:4326"
ACCEPT = 0.8
REVIEW_FLOOR = 0.5
FUZZY_MIN_RATIO = 0.85
DEFAULT_BUFFER_M = 1000.0

_TR_RE = re.compile(r"^0*(\d+)\s*([NSEW])$", re.IGNORECASE)


def add_args(sp) -> None:
    sp.add_argument("--restrictions", help="Override the restrictions.jsonl path")
    sp.add_argument("--buffer-km", type=float, default=1.0, help="PLSS section buffer (km)")


# --- scoring -----------------------------------------------------------------


def pad_tr(value: str | None) -> str:
    """`"4N"` -> `"04N"`; leaves anything unrecognised alone."""
    if not value:
        return ""
    v = str(value).strip().upper()
    m = _TR_RE.match(v)
    return f"{int(m.group(1)):02d}{m.group(2)}" if m else v


def plss_keys(plss: list[dict] | None) -> set[str]:
    """`[{"township": "4N", "range": "7E", "sections": [16, 21]}]` -> `{"04N07E16", "04N07E21"}`."""
    keys: set[str] = set()
    for entry in plss or []:
        town = pad_tr(entry.get("township"))
        rng = pad_tr(entry.get("range"))
        if not town or not rng:
            continue
        for sec in entry.get("sections") or []:
            try:
                keys.add(f"{town}{rng}{int(sec):02d}")
            except (TypeError, ValueError):
                continue
    return keys


def score_name(restriction_norm: str, lake_norm: str) -> tuple[float, str]:
    """Name-only score and the method that produced it."""
    if not restriction_norm or not lake_norm:
        return 0.0, "none"
    if restriction_norm == lake_norm:
        return 1.0, "exact"
    if ids.strip_qualifiers(restriction_norm) == ids.strip_qualifiers(lake_norm):
        return 0.6, "qualifier"
    a, b = ids.name_tokens(restriction_norm), ids.name_tokens(lake_norm)
    union = a | b
    token_score = 0.4 * (len(a & b) / len(union)) if union else 0.0
    ratio = difflib.SequenceMatcher(None, restriction_norm, lake_norm).ratio()
    fuzzy_score = 0.5 * ratio if ratio >= FUZZY_MIN_RATIO else 0.0
    if fuzzy_score > token_score:
        return fuzzy_score, "fuzzy"
    return (token_score, "tokens") if token_score > 0 else (0.0, "none")


@dataclass
class Match:
    restriction_id: str
    lake_id: int
    score: float
    method: str
    needs_review: bool

    def as_dict(self) -> dict:
        return {
            "restriction_id": self.restriction_id,
            "lake_id": int(self.lake_id),
            "score": round(float(self.score), 3),
            "method": self.method,
            "needs_review": bool(self.needs_review),
        }


@dataclass
class Matcher:
    """Holds the projected lake index so a whole run reuses one spatial index."""

    lakes: gpd.GeoDataFrame
    plss: gpd.GeoDataFrame | None = None
    buffer_m: float = DEFAULT_BUFFER_M
    _proj: gpd.GeoSeries = field(init=False)
    _plss_by_key: dict[str, list[int]] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self.lakes = self.lakes.reset_index(drop=True)
        if self.lakes.crs is None:
            self.lakes = self.lakes.set_crs(WGS84)
        self._proj = self.lakes.geometry.to_crs(MEASURE_CRS)
        self._sindex = self._proj.sindex  # build the spatial index once
        self._county_lower = self.lakes["county"].astype("string").str.lower().fillna("")
        if self.plss is not None and len(self.plss):
            if self.plss.crs is None:
                self.plss = self.plss.set_crs(WGS84)
            self._plss_proj = self.plss.geometry.to_crs(MEASURE_CRS)
            keys = (
                self.plss["TOWN"].astype(str).str.strip()
                + self.plss["RANGE"].astype(str).str.strip()
                + self.plss["SECTION"].astype(str).str.strip().str.zfill(2)
            )
            for pos, key in enumerate(keys):
                self._plss_by_key.setdefault(key, []).append(pos)
        else:
            self._plss_proj = None

    # -- candidate sets --

    def section_geometry(self, keys: set[str]):
        rows = [pos for key in keys for pos in self._plss_by_key.get(key, ())]
        if not rows or self._plss_proj is None:
            return None
        return shapely.union_all(self._plss_proj.iloc[rows].to_numpy())

    def candidates_for_geometry(self, section_geom) -> tuple[list[int], set[int]]:
        buffered = section_geom.buffer(self.buffer_m)
        idx = list(self._sindex.query(buffered, predicate="intersects"))
        hits = [i for i in idx if self._proj.iloc[i].intersects(buffered)]
        inside = {i for i in hits if self._proj.iloc[i].intersects(section_geom)}
        return hits, inside

    def candidates_for_county(self, county: str | None) -> list[int]:
        if not county:
            return []
        mask = (self._county_lower == str(county).strip().lower()) & self.lakes["name_norm"].astype(bool)
        return list(mask.to_numpy().nonzero()[0])

    # -- scoring --

    def best(self, name_norm: str, county: str | None, rows: list[int], inside: set[int]):
        best_score, best_row, best_method = 0.0, None, "none"
        for row in rows:
            base, method = score_name(name_norm, str(self.lakes["name_norm"].iat[row] or ""))
            if base <= 0:
                continue
            score = base
            if row in inside:
                score += 0.2
            if county and self._county_lower.iat[row] == str(county).strip().lower():
                score += 0.1
            if score > best_score:
                best_score, best_row, best_method = score, row, method
        return best_score, best_row, best_method

    def match_one(self, rec: dict) -> tuple[Match | None, dict | None]:
        name_norm = rec.get("lake_name_norm") or ids.normalize_name(rec.get("lake_name_raw"))
        county = rec.get("county")
        keys = plss_keys(rec.get("plss"))
        section_geom = self.section_geometry(keys) if keys else None
        if section_geom is not None:
            rows, inside = self.candidates_for_geometry(section_geom)
            source = "plss"
        else:
            rows, inside = self.candidates_for_county(county), set()
            source = "county" if not keys else "county-plss-miss"

        score, row, method = self.best(name_norm, county, rows, inside)
        if row is None or score < REVIEW_FLOOR:
            return None, {
                "restriction_id": rec.get("restriction_id"),
                "lake_name_raw": rec.get("lake_name_raw"),
                "lake_name_norm": name_norm,
                "county": county,
                "township": rec.get("township"),
                "candidates": len(rows),
                "best_score": round(float(score), 3),
                "best_lake_id": int(self.lakes["id"].iat[row]) if row is not None else None,
                "reason": "no candidate scored 0.5 or better" if rows else f"no candidate lakes ({source})",
            }
        return (
            Match(
                restriction_id=rec["restriction_id"],
                lake_id=int(self.lakes["id"].iat[row]),
                score=min(score, 1.0),
                method=f"{method}+{source}",
                needs_review=score < ACCEPT,
            ),
            None,
        )

    def match_name_county(self, name: str, county: str | None) -> tuple[int | None, float]:
        """Name + county match used for MAC record entries (no PLSS available)."""
        name_norm = ids.normalize_name(name)
        rows = self.candidates_for_county(county) or list(range(len(self.lakes)))
        score, row, _ = self.best(name_norm, county, rows, set())
        if row is None or score < REVIEW_FLOOR:
            return None, round(float(score), 3)
        return int(self.lakes["id"].iat[row]), round(min(score, 1.0), 3)


# --- io ----------------------------------------------------------------------


def read_restrictions(path) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def load_plss(cfg: Config) -> gpd.GeoDataFrame | None:
    """Load the PLSS sections, caching a parquet copy so later runs skip the 75 MB GeoJSON parse."""
    parquet = cfg.cache_dir / "plss_sections.parquet"
    if parquet.exists() and not cfg.force:
        return gpd.read_parquet(parquet)
    path = gis.find_dataset(cfg, "plss")
    if path is None:
        log.warning("plss_sections.geojson missing; matching falls back to county-only candidates")
        return None
    t0 = time.monotonic()
    gdf = gpd.read_file(path, columns=["TOWN", "RANGE", "SECTION", "TWNRNGSEC", "COUNTY"])
    gdf.to_parquet(parquet, index=False)
    log.info("read %d PLSS sections in %.1fs (cached to %s)", len(gdf), time.monotonic() - t0, parquet.name)
    return gdf


def apply_overrides(matches: list[Match], overrides: dict) -> list[Match]:
    forced = overrides.get("matches") or []
    unmatch = {
        (str(u.get("restriction_id")), int(u.get("lake_id")))
        for u in (overrides.get("unmatch") or [])
        if u.get("restriction_id") is not None and u.get("lake_id") is not None
    }
    forced_ids = {str(f.get("restriction_id")) for f in forced if f.get("restriction_id")}
    kept = [m for m in matches if m.restriction_id not in forced_ids and (m.restriction_id, m.lake_id) not in unmatch]
    for f in forced:
        if f.get("restriction_id") is None or f.get("lake_id") is None:
            continue
        kept.append(
            Match(
                restriction_id=str(f["restriction_id"]),
                lake_id=int(f["lake_id"]),
                score=1.0,
                method="override",
                needs_review=False,
            )
        )
    return kept


def run(cfg: Config, args) -> int:
    started = time.monotonic()
    rpath = getattr(args, "restrictions", None) or (cfg.work_dir / "restrictions.jsonl")
    path = Path(rpath)
    if not path.exists():
        log.error("%s not found; run `seaplane parse-dnr` first", path)
        return 2
    lakes_path = cfg.work_dir / "lakes.parquet"
    if not lakes_path.exists():
        log.error("%s not found; run `seaplane geometry` first", lakes_path)
        return 2

    records = read_restrictions(path)
    active = [r for r in records if r.get("status", "active") == "active"]
    if cfg.counties:
        wanted = {c.lower() for c in cfg.counties}
        active = [r for r in active if str(r.get("county", "")).lower() in wanted]
    lakes = gpd.read_parquet(lakes_path)
    matcher = Matcher(lakes, load_plss(cfg), buffer_m=getattr(args, "buffer_km", 1.0) * 1000.0)

    matches: list[Match] = []
    unmatched: list[dict] = []
    for rec in active:
        m, miss = matcher.match_one(rec)
        if m:
            matches.append(m)
        if miss:
            unmatched.append(miss)

    overrides = manual.load_overrides(cfg)
    matches = apply_overrides(matches, overrides)

    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    (cfg.work_dir / "matches.json").write_text(
        json.dumps([m.as_dict() for m in matches], indent=1), encoding="utf-8"
    )
    (cfg.work_dir / "unmatched.json").write_text(json.dumps(unmatched, indent=1), encoding="utf-8")
    low = sum(1 for m in matches if m.needs_review)
    log.info(
        "matched %d/%d active restrictions (%d low confidence, %d unmatched) to %d lakes in %.1fs",
        len(matches), len(active), low, len(unmatched),
        len({m.lake_id for m in matches}), time.monotonic() - started,
    )
    return 0


def matches_by_lake(matches: list[dict]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for m in matches:
        out.setdefault(int(m["lake_id"]), []).append(m)
    return out
