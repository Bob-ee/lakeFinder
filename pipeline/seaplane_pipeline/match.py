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
| qualifier-stripped names equal but the qualifiers conflict (Little vs Big) | 0.15 |
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

Lakes and waterways never cross
-------------------------------
`geometry` now keeps river polygons too (`kind`), so a header is first sorted into one of two
tracks by `restriction_kind`: a header carrying a river/creek/channel/canal/drain/bayou/harbor/bay
word is a **waterway** unless it ends in a lake generic, which keeps "Stoney Creek Lake" and
"Middle Straits Lake" on the lake track where they belong.

- **Lake track** -- everything above, unchanged, over `kind == "lake"` polygons only. One match.
- **Waterway track** (`match_waterway`) -- over `kind == "river"` polygons plus the river-named
  `kind == "lake"` polygons (the impoundments the layer names "Au Sable River" or "Cornwall Creek
  Flooding"). Candidates are the same-name polygons intersecting the rule's county -- the
  polygon's `counties` list, not its centroid `county`, because a river runs through several. All
  polygons tied at the best score are kept, because one river is digitized as several polygons
  sharing one name (Grand River: 19, Sturgeon River: 29) and a rule really does cover several.

That set is then narrowed to a reach, in order: the buffered PLSS section union, else the rule's
township / city labels against the polygon's `townships` list. When neither narrows it, every
same-name polygon in the county is matched and each match carries `reach_unresolved`, which the
client turns into "this rule covers part of this river -- read the rule text". Narrowing only ever
applies when it leaves at least one polygon; a DNR section list that misses the digitized channel
falls through to the next test rather than dropping the rule.

This is deliberately the one place the matcher emits several rows for one restriction. A creek,
channel or canal with no polygon in any water layer still ends up in `unmatched.json` as
`kind: "waterway"`.

Big water
---------
`geometry` also supplies `kind == "great_lake"` and `kind == "connecting_water"` polygons, and they
join the **waterway track only**. That single gate is what keeps a lake-named inland rule off a
Great Lake: the 25-acre "Lake Erie" in Monroe, "Saint Clair Lake" in Antrim, "Huron Lake" in
Houghton and "Superior Lakes" in Marquette are all lake headers scoring against lake polygons, while
"Detroit River", "St. Clair River" and "Lake St. Clair, Certain Creeks" are waterway headers that
reach the big-water polygons by name (the last through the `head.split(",")[0]` variant).

Two rules are specific to big water:

- **Every big-water match is `reach_unresolved`.** A township or a section never covers a 430 sq mi
  lake or a 20 mile river, so the rule is about a reach whatever the narrowing found.
- **Bay and harbor fallback** (`match_great_lake_bay`): a header *ending* in Bay or Harbor that
  matched no polygon by name attaches to the one Great Lake its county touches, but only when the
  restriction's own PLSS sections or township/city labels touch that lake too. Both tests are
  required and both reuse machinery the waterway track already has -- the polygon's `counties` and
  `townships` intersection lists, and the buffered section union. The county test alone would be a
  guess: "Pine Creek Bay" (Ottawa) is on Lake Macatawa, and a county on two Great Lakes (Chippewa,
  Mackinac, St. Clair, Wayne) is ambiguous by construction.
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
import numpy as np
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


WATERWAY_RE = re.compile(
    r"\b(rivers?|creeks?|channels?|canals?|drains?|bayous?|harbou?rs?|straits?|dams?|tributar(y|ies)|inlets?|"
    r"outlets?|cuts?|sloughs?|marsh(es)?|swamps?|floodings?|backwaters?|bays?)\b",
    re.IGNORECASE,
)
NAME_SUFFIX_RE = re.compile(r"\s+(-|–|—|including|incl\.?|and channels?|and tributaries|and the|near|at|in)\s+.*$", re.IGNORECASE)


LAKE_GENERIC_RE = re.compile(
    r"\b(lakes?|ponds?|reservoirs?|flowages?|impoundments?|basins?)\s*$", re.IGNORECASE
)
_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*")

#: Contract "Waterway matching": kinds the inland hydrography layer does not contain.
BIG_WATER_KINDS = ("great_lake", "connecting_water")
#: A header that *ends* in a bay or harbor word. "Bay of Lake Nettie" and "Indian Pete's Bayou" are
#: deliberately not bay headers; "Sandy Creek Bay and North" is, once its trailing clause is trimmed.
BAY_HARBOR_RE = re.compile(r"\b(bays?|harbou?rs?)(\s+of\s+refuge)?\s*$", re.IGNORECASE)


def is_waterway_name(raw_name: str) -> bool:
    """True for DNR entries that describe a river, creek, channel, harbor, or bay."""
    head = NAME_SUFFIX_RE.sub("", raw_name or "")
    return bool(WATERWAY_RE.search(head))


def restriction_kind(raw_name: str) -> str:
    """`"waterway"` or `"lake"` for a DNR header -- which polygon kinds it is allowed to match.

    A waterway word is not enough on its own: "Stoney Creek Lake", "Middle Straits Lake" and
    "Tubbs Lake (canals)" all carry one and are all lakes. What decides it is whether the head of
    the name, parentheticals removed, *ends* in a lake generic.
    """
    head = NAME_SUFFIX_RE.sub("", raw_name or "")
    if LAKE_GENERIC_RE.search(_PAREN_RE.sub(" ", head).strip()):
        return "lake"
    return "waterway" if is_waterway_name(raw_name) else "lake"



def name_variants(raw_name: str) -> list[str]:
    """Normalized alternates for a raw DNR header, most specific first (never includes the empty string)."""
    out: list[str] = []
    head = NAME_SUFFIX_RE.sub("", raw_name or "").strip()
    for cand in (head, re.sub(r"\s*\(.*?\)\s*", " ", head)):
        norm = ids.normalize_name(cand)
        if norm and norm not in out:
            out.append(norm)
    return out


#: A river header's tail is a place along the river, not part of its name: "Grand River, Dick's
#: Landing", "Black River adjacent to highway 94 bridge", "Fox River and Tributaries".
TRAILING_CLAUSE_RE = re.compile(r"\s*(?:,\s*)?\b(?:and|adjacent|including|from|between)\b.*$", re.IGNORECASE)


def waterway_name_variants(raw_name: str) -> list[str]:
    """Progressively shorter forms of a river header, **most specific first**.

    `match_waterway` takes the first form that scores at all, not the best-scoring one, so
    "Fox River, East Branch and Tributaries" lands on East Branch Fox River rather than being
    trimmed all the way to the main stem.
    """
    head = NAME_SUFFIX_RE.sub("", raw_name or "").strip()
    forms = [
        raw_name or "",
        head,
        _PAREN_RE.sub(" ", head),
        TRAILING_CLAUSE_RE.sub("", head),
        head.split(",")[0],
    ]
    out: list[str] = []
    for form in forms:
        norm = ids.normalize_name(form)
        if norm and norm not in out:
            out.append(norm)
    return out


def is_bay_or_harbor_name(raw_name: str) -> bool:
    """True when the header's head *ends* in a bay or harbor word (see `BAY_HARBOR_RE`)."""
    head = NAME_SUFFIX_RE.sub("", raw_name or "").strip()
    head = TRAILING_CLAUSE_RE.sub("", _PAREN_RE.sub(" ", head)).strip()
    return bool(BAY_HARBOR_RE.search(head))


_AREA_PUNCT_RE = re.compile(r"[^a-z0-9]+")
_TWP_RE = re.compile(r"\btwp\b")


def _area_key(value) -> str:
    """Comparison key for a county or minor-civil-division label: `"St. Clair Twp."` -> `"st clair township"`."""
    text = _AREA_PUNCT_RE.sub(" ", str(value or "").lower()).strip()
    return _TWP_RE.sub("township", text)


def area_keys(value) -> set[str]:
    """The restriction's `county` / `township` field (comma-joined when several) as a key set."""
    if not value:
        return set()
    return {k for k in (_area_key(part) for part in str(value).split(",")) if k}


_QUALIFIER_WORDS = frozenset({"big", "little", "north", "south", "east", "west", "upper", "lower", "middle"})


def _qualifiers(norm: str) -> frozenset[str]:
    return frozenset(t for t in norm.split() if t in _QUALIFIER_WORDS)


def score_name(restriction_norm: str, lake_norm: str) -> tuple[float, str]:
    """Name-only score and the method that produced it."""
    if not restriction_norm or not lake_norm:
        return 0.0, "none"
    if restriction_norm == lake_norm:
        return 1.0, "exact"
    if ids.strip_qualifiers(restriction_norm) == ids.strip_qualifiers(lake_norm):
        # "Little School Lot" vs "Big School Lot" are different lakes: keep the pair below the review floor
        # even with the section and county bonuses, so it surfaces in the review queue instead of matching.
        if _qualifiers(restriction_norm) and _qualifiers(lake_norm) and _qualifiers(restriction_norm) != _qualifiers(lake_norm):
            return 0.15, "qualifier-conflict"
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
    #: Waterway track only: the rule names a river but could not be narrowed below the county, so it
    #: covers some reach of this polygon rather than all of it. Display only; no verdict changes.
    reach_unresolved: bool = False

    def as_dict(self) -> dict:
        out = {
            "restriction_id": self.restriction_id,
            "lake_id": int(self.lake_id),
            "score": round(float(self.score), 3),
            "method": self.method,
            "needs_review": bool(self.needs_review),
        }
        if self.reach_unresolved:
            out["reach_unresolved"] = True
        return out


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
        # `kind`, `counties` and `townships` arrive with the river work; a lakes.parquet written
        # before it (or a hand-built test frame) is read as an all-lake table with one county each.
        if "kind" in self.lakes:
            self._kind = self.lakes["kind"].astype("string").fillna("lake").to_numpy()
        else:
            self._kind = np.array(["lake"] * len(self.lakes), dtype=object)
        self._is_river = self._kind == "river"
        self._is_big_water = np.isin(self._kind, BIG_WATER_KINDS)
        self._river_named = np.array(
            [is_waterway_name(str(n)) if isinstance(n, str) else False for n in self.lakes["name"]]
        )
        self._counties = self._area_sets("counties", "county")
        self._townships = self._area_sets("townships", "township")
        self._waterway_rows = np.flatnonzero(
            (self._is_river | self._river_named | self._is_big_water)
            & self.lakes["name_norm"].astype(bool).to_numpy()
        )
        self._great_lake_rows = np.flatnonzero(self._kind == "great_lake")
        # The lake track is `kind == "lake"` exactly, never "not a river": a Great Lake must not be
        # reachable from a lake-named header (contract, "Waterway matching").
        self._lake_rows_mask = self._kind == "lake"
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

    def _area_sets(self, column: str, fallback: str) -> list[set[str]]:
        """Normalized `{county}` / `{township}` sets per polygon, from the list column when present."""
        source = self.lakes[column] if column in self.lakes else self.lakes[fallback]
        out: list[set[str]] = []
        for value in source:
            if value is None or isinstance(value, float):
                out.append(set())
            elif isinstance(value, str):
                out.append({_area_key(value)} - {""})
            else:
                out.append({_area_key(v) for v in value} - {""})
        return out

    # -- candidate sets --

    def section_geometry(self, keys: set[str]):
        rows = [pos for key in keys for pos in self._plss_by_key.get(key, ())]
        if not rows or self._plss_proj is None:
            return None
        return shapely.union_all(self._plss_proj.iloc[rows].to_numpy())

    def candidates_for_geometry(self, section_geom) -> tuple[list[int], set[int]]:
        buffered = section_geom.buffer(self.buffer_m)
        # Sorted, because the spatial index's own order depends on what else is in the tree and
        # `best` keeps the first of two equally-scoring lakes. Row order is the stable tie-break.
        idx = sorted(self._sindex.query(buffered, predicate="intersects"))
        hits = [i for i in idx if self._proj.iloc[i].intersects(buffered)]
        inside = {i for i in hits if self._proj.iloc[i].intersects(section_geom)}
        return hits, inside

    def candidates_for_county(self, county: str | None) -> list[int]:
        """Lake track: every named `kind == "lake"` polygon whose centroid county is `county`."""
        if not county:
            return []
        mask = (self._county_lower == str(county).strip().lower()) & self.lakes["name_norm"].astype(bool)
        return list((mask.to_numpy() & self._lake_rows_mask).nonzero()[0])

    def waterway_candidates(self, county: str | None) -> list[int]:
        """Waterway track: named river polygons and river-named lake polygons touching `county`.

        Membership is the polygon's `counties` list (every county it intersects), because a river
        polygon's centroid can sit in a county the rule never names.
        """
        keys = area_keys(county)
        if not keys:
            return []
        return [row for row in self._waterway_rows if self._counties[row] & keys]

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
        """One restriction -> at most one lake match, or an `unmatched.json` row. Lake track."""
        matches, miss = self.match_record(rec)
        return (matches[0] if matches else None), miss

    def match_record(self, rec: dict) -> tuple[list[Match], dict | None]:
        """One restriction -> every polygon it matches. Only the waterway track returns several."""
        raw_name = rec.get("lake_name_raw") or ""
        if restriction_kind(raw_name) == "waterway":
            return self.match_waterway(rec)
        match, miss = self._match_lake(rec)
        return ([match] if match else []), miss

    def _match_lake(self, rec: dict) -> tuple[Match | None, dict | None]:
        raw_name = rec.get("lake_name_raw") or ""
        name_norm = rec.get("lake_name_norm") or ids.normalize_name(raw_name)
        county = rec.get("county")
        keys = plss_keys(rec.get("plss"))
        section_geom = self.section_geometry(keys) if keys else None
        if section_geom is not None:
            rows, inside = self.candidates_for_geometry(section_geom)
            rows = [r for r in rows if self._lake_rows_mask[r]]
            inside = {r for r in inside if self._lake_rows_mask[r]}
            source = "plss"
        else:
            rows, inside = self.candidates_for_county(county), set()
            source = "county" if not keys else "county-plss-miss"

        score, row, method = self.best(name_norm, county, rows, inside)
        # Headers like "Spring Lake - Spring Lake Township" or "Lake Leelanau Including Carp River"
        # carry a suffix the hydrography layer never has; retry on the trimmed name.
        for variant in name_variants(raw_name):
            if variant == name_norm or score >= ACCEPT:
                break
            v_score, v_row, v_method = self.best(variant, county, rows, inside)
            if v_score > score:
                score, row, method = v_score, v_row, f"{v_method}-trimmed"
        if row is None or score < REVIEW_FLOOR:
            reason = "no candidate scored 0.5 or better" if rows else f"no candidate lakes ({source})"
            return None, self._miss(rec, name_norm, "lake", len(rows), score, row, reason)
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

    def _miss(self, rec, name_norm, kind, candidates, score, row, reason) -> dict:
        return {
            "restriction_id": rec.get("restriction_id"),
            "lake_name_raw": rec.get("lake_name_raw") or "",
            "lake_name_norm": name_norm,
            "county": rec.get("county"),
            "township": rec.get("township"),
            "candidates": int(candidates),
            "best_score": round(float(score), 3),
            "best_lake_id": int(self.lakes["id"].iat[row]) if row is not None else None,
            "kind": kind,
            "reason": reason,
        }

    # -- waterway track --

    def _score_rows(self, name_norm: str, county: str | None, rows: list[int]):
        """`[(score, row, method)]` for every row that scores at all. County bonus only; the section
        bonus is deliberately absent, because narrowing to a reach is a separate, later step."""
        county_keys = area_keys(county)
        scored = []
        for row in rows:
            base, method = score_name(name_norm, str(self.lakes["name_norm"].iat[row] or ""))
            if base <= 0:
                continue
            scored.append((base + (0.1 if self._counties[row] & county_keys else 0.0), row, method))
        return scored

    def match_waterway(self, rec: dict) -> tuple[list[Match], dict | None]:
        """A river/creek header -> every same-name polygon it covers in the county, narrowed to a reach."""
        raw_name = rec.get("lake_name_raw") or ""
        name_norm = rec.get("lake_name_norm") or ids.normalize_name(raw_name)
        county = rec.get("county")
        rows = self.waterway_candidates(county)

        scored: list = []
        method_suffix = ""
        for i, variant in enumerate([name_norm, *waterway_name_variants(raw_name)]):
            if i and variant == name_norm:
                continue
            hits = [s for s in self._score_rows(variant, county, rows) if s[0] >= REVIEW_FLOOR]
            if hits:
                scored, method_suffix = hits, ("-trimmed" if i else "")
                break
        if not scored:
            bay = self.match_great_lake_bay(rec)
            if bay is not None:
                return [bay], None
            reason = (
                "no river polygon scored 0.5 or better"
                if rows
                else "no waterbody of that name in the county (creek/channel/canal, or a bay the "
                "Great Lake fallback could not place)"
            )
            best = max((s for s, _, _ in self._score_rows(name_norm, county, rows)), default=0.0)
            return [], self._miss(rec, name_norm, "waterway", len(rows), best, None, reason)

        top = max(s for s, _, _ in scored)
        keep = [(s, row, m) for s, row, m in scored if s >= top - 1e-9]
        keep, narrowed = self._narrow_reach(rec, keep)
        score = min(top, 1.0)
        method = f"{keep[0][2]}{method_suffix}+river-{narrowed}"
        return [
            Match(
                restriction_id=rec["restriction_id"],
                lake_id=int(self.lakes["id"].iat[row]),
                score=score,
                method=method,
                needs_review=score < ACCEPT,
                # Big water is always a reach: no township or section covers a whole Great Lake.
                reach_unresolved=narrowed == "county" or bool(self._is_big_water[row]),
            )
            for _, row, _ in keep
        ], None

    def match_great_lake_bay(self, rec: dict) -> Match | None:
        """A bay or harbor of a Great Lake -> that lake, `reach_unresolved`. `None` when unsure.

        Requires (1) the restriction's county to touch exactly one `great_lake` polygon and (2) the
        restriction's own PLSS sections (buffered) or township/city labels to touch that same lake.
        Both, because either alone is a guess -- see the module docstring.
        """
        if not is_bay_or_harbor_name(rec.get("lake_name_raw") or ""):
            return None
        keys = area_keys(rec.get("county"))
        if not keys:
            return None
        rows = [row for row in self._great_lake_rows if self._counties[row] & keys]
        if len(rows) != 1:
            return None
        row = rows[0]

        section_geom = self.section_geometry(plss_keys(rec.get("plss")))
        if section_geom is not None and self._proj.iloc[row].intersects(section_geom.buffer(self.buffer_m)):
            narrowed = "plss"
        elif area_keys(rec.get("township")) & self._townships[row]:
            narrowed = "township"
        else:
            return None
        return Match(
            restriction_id=rec["restriction_id"],
            lake_id=int(self.lakes["id"].iat[row]),
            score=1.0,
            method=f"bay+great-lake-{narrowed}",
            needs_review=False,
            reach_unresolved=True,
        )

    def _narrow_reach(self, rec: dict, keep: list) -> tuple[list, str]:
        """PLSS sections first, then township / city labels. Either is skipped when it empties the set."""
        section_geom = self.section_geometry(plss_keys(rec.get("plss")))
        if section_geom is not None:
            buffered = section_geom.buffer(self.buffer_m)
            hit = [c for c in keep if self._proj.iloc[c[1]].intersects(buffered)]
            if hit:
                return hit, "plss"
        wanted = area_keys(rec.get("township"))
        if wanted:
            hit = [c for c in keep if self._townships[c[1]] & wanted]
            if hit:
                return hit, "township"
        return keep, "county"

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
        found, miss = matcher.match_record(rec)
        matches.extend(found)
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
    unresolved = {m.restriction_id for m in matches if m.reach_unresolved}
    log.info(
        "matched %d/%d active restrictions (%d rows, %d low confidence, %d unmatched) to %d waterbodies in %.1fs",
        len({m.restriction_id for m in matches}), len(active), len(matches), low, len(unmatched),
        len({m.lake_id for m in matches}), time.monotonic() - started,
    )
    big_ids = set(matcher.lakes["id"].to_numpy()[matcher._is_big_water])
    big = [m for m in matches if m.lake_id in big_ids]
    log.info(
        "  waterway track: %d restrictions on %d polygons, %d with reach_unresolved; %d unmatched waterways",
        len({m.restriction_id for m in matches if "+river-" in m.method or m.method.startswith("bay+")}),
        len({m.lake_id for m in matches if "+river-" in m.method or m.method.startswith("bay+")}),
        len(unresolved),
        sum(1 for u in unmatched if u["kind"] == "waterway"),
    )
    if big:
        by_name = {}
        for m in big:
            name = str(matcher.lakes.loc[matcher.lakes["id"] == m.lake_id, "name"].iat[0])
            by_name[name] = by_name.get(name, 0) + 1
        log.info(
            "  big water: %d restrictions on %d water bodies (%s)",
            len({m.restriction_id for m in big}), len(by_name),
            ", ".join(f"{k} x{v}" for k, v in sorted(by_name.items())),
        )
    return 0


def kinds_by_lake(lakes) -> dict[int, str]:
    """`{lake_id: kind}` from a `lakes.parquet` frame; `"lake"` for a frame written before `kind`."""
    if lakes is None or not len(lakes):
        return {}
    if "kind" not in lakes:
        return {int(i): "lake" for i in lakes["id"]}
    kinds = lakes["kind"].astype("string").fillna("lake")
    return {int(i): str(k) for i, k in zip(lakes["id"], kinds, strict=True)}


def big_water_partial_ids(matches: list[dict], kinds: dict[int, str]) -> set[str]:
    """Restriction ids to publish as `big_water_partial` (docs/data-contract.md).

    A restriction qualifies when every water body it matched is a `great_lake` or
    `connecting_water`, so a rule that also lands on an inland polygon is never capped. `classify`
    and `build` both call this, which is what keeps the pipeline's prebaked verdict and the client's
    re-run of the engine on the published record in agreement.
    """
    if not kinds:
        return set()
    by_rid: dict[str, list[int]] = {}
    for m in matches:
        by_rid.setdefault(str(m["restriction_id"]), []).append(int(m["lake_id"]))
    return {
        rid for rid, ids in by_rid.items()
        if ids and all(kinds.get(i) in BIG_WATER_KINDS for i in ids)
    }


def matches_by_lake(matches: list[dict]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for m in matches:
        out.setdefault(int(m["lake_id"]), []).append(m)
    return out
