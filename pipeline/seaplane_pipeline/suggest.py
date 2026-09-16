"""Stage `suggest`: turn the review queue into ready-to-paste `overrides.yaml` entries.

Writes `data/manual/overrides.suggested.yaml` with, for every lake-named unmatched restriction, the best candidate
lakes (near the cited PLSS section, same name elsewhere in the county, or the section with a flipped township/range
direction, which catches typos on the DNR pages), and for every low-confidence match a confirm entry. Bobby copies
what he agrees with into `overrides.yaml` and reruns from `match`.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field

import yaml

from . import ids
from .config import Config
from .match import ACCEPT, Matcher, load_plss, name_variants, plss_keys, read_restrictions, score_name

log = logging.getLogger(__name__)

_DIRECTION_FLIP = {"N": "S", "S": "N", "E": "W", "W": "E"}


def add_args(sp) -> None:
    sp.add_argument("--top", type=int, default=3, help="Candidates to list per restriction")
    sp.add_argument("--out", help="Output path (default data/manual/overrides.suggested.yaml)")


@dataclass
class Candidate:
    lake_id: int
    name: str | None
    county: str | None
    township: str | None
    area_acres: float | None
    score: float
    how: str
    km_from_section: float | None = None


@dataclass
class Suggestion:
    restriction_id: str
    lake_name_raw: str
    county: str | None
    township: str | None
    rule_id: str | None
    restriction_type: str | None
    plss: str | None
    hints: list[str] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    paste: str | None = None


def plss_text(plss: list[dict] | None) -> str | None:
    if not plss:
        return None
    parts = []
    for g in plss:
        secs = ",".join(str(s) for s in g.get("sections") or [])
        parts.append(f"T{g.get('township')} R{g.get('range')} s{secs}")
    return "; ".join(parts)


def flipped_plss(plss: list[dict] | None):
    """Yield (label, plss) with the range direction flipped, then the township direction flipped."""
    for key, what in (("range", "range"), ("township", "township")):
        out = []
        changed = False
        for g in plss or []:
            g2 = dict(g)
            val = str(g.get(key) or "")
            if val and val[-1].upper() in _DIRECTION_FLIP:
                g2[key] = val[:-1] + _DIRECTION_FLIP[val[-1].upper()]
                changed = True
            out.append(g2)
        if changed:
            yield f"{what} direction flipped ({plss_text(plss)} -> {plss_text(out)})", out


class Suggester:
    def __init__(self, matcher: Matcher, top: int = 3) -> None:
        self.m = matcher
        self.top = top
        self._centroids = matcher._proj.centroid

    def _lake(self, row: int, score: float, how: str, section_geom=None) -> Candidate:
        lakes = self.m.lakes
        km = None
        if section_geom is not None:
            km = round(float(self._centroids.iloc[row].distance(section_geom.centroid)) / 1000.0, 1)
        area = lakes["area_acres"].iat[row] if "area_acres" in lakes.columns else None
        return Candidate(
            lake_id=int(lakes["id"].iat[row]),
            name=(lakes["name"].iat[row] or None) if "name" in lakes.columns else None,
            county=lakes["county"].iat[row] if "county" in lakes.columns else None,
            township=lakes["township"].iat[row] if "township" in lakes.columns else None,
            area_acres=round(float(area), 1) if area is not None and not math.isnan(float(area)) else None,
            score=round(float(score), 3),
            how=how,
            km_from_section=km,
        )

    def _scored(self, names: list[str], county: str | None, rows: list[int], inside: set[int]) -> list[tuple[float, int]]:
        out = []
        for row in rows:
            lake_norm = str(self.m.lakes["name_norm"].iat[row] or "")
            base = max((score_name(n, lake_norm)[0] for n in names), default=0.0)
            if base <= 0:
                continue
            score = base + (0.2 if row in inside else 0.0)
            if county and self.m._county_lower.iat[row] == str(county).strip().lower():
                score += 0.1
            out.append((min(score, 1.0), row))
        return sorted(out, reverse=True)

    def suggest(self, rec: dict) -> Suggestion:
        raw = rec.get("lake_name_raw") or ""
        names = [rec.get("lake_name_norm") or ids.normalize_name(raw), *name_variants(raw)]
        names = [n for i, n in enumerate(names) if n and n not in names[:i]]
        county = rec.get("county")
        s = Suggestion(
            restriction_id=rec["restriction_id"], lake_name_raw=raw, county=county, township=rec.get("township"),
            rule_id=rec.get("rule_id"), restriction_type=rec.get("restriction_type"), plss=plss_text(rec.get("plss")),
        )
        found: dict[int, Candidate] = {}

        def add(row: int, score: float, how: str, geom=None) -> None:
            c = self._lake(row, score, how, geom)
            if c.lake_id not in found or found[c.lake_id].score < c.score:
                found[c.lake_id] = c

        keys = plss_keys(rec.get("plss"))
        section_geom = self.m.section_geometry(keys) if keys else None
        if section_geom is not None:
            rows, inside = self.m.candidates_for_geometry(section_geom)
            for score, row in self._scored(names, county, rows, inside)[: self.top]:
                add(row, score, "name match near the cited section", section_geom)
            if not found:
                # Nothing shares the name; list what the section actually holds ("Unnamed Lake", "Lake 16").
                named = [r for r in inside if self.m.lakes["name_norm"].iat[r]]
                for row in sorted(named, key=lambda r: -float(self.m.lakes["area_acres"].iat[r] or 0))[: self.top]:
                    add(row, 0.0, "lake inside the cited section (no name match)", section_geom)
                if not named and inside:
                    s.hints.append("The cited section holds only unnamed polygons; pick by shape on the map.")
        elif keys:
            s.hints.append("The cited township/range/section does not exist in the PLSS layer.")

        for label, flipped in flipped_plss(rec.get("plss")):
            geom = self.m.section_geometry(plss_keys(flipped))
            if geom is None:
                continue
            rows, inside = self.m.candidates_for_geometry(geom)
            scored = self._scored(names, county, rows, inside)
            if scored and scored[0][0] >= ACCEPT:
                score, row = scored[0]
                add(row, score, f"PLSS {label}", geom)
                s.hints.append(f"With the {label} the section contains {self.m.lakes['name'].iat[row]}; likely a typo on the DNR page.")

        for score, row in self._scored(names, county, self.m.candidates_for_county(county), set())[: self.top]:
            if score >= 0.6:
                add(row, score, "same or similar name elsewhere in the county", section_geom)

        s.candidates = sorted(found.values(), key=lambda c: (-c.score, c.km_from_section or 0))[: max(self.top, 1) + 1]
        if s.candidates and s.candidates[0].score > 0:
            best = s.candidates[0]
            s.paste = f"- {{restriction_id: {s.restriction_id}, lake_id: {best.lake_id}, note: \"{raw} -> {best.name} ({best.how})\"}}"
        return s


def build(cfg: Config, top: int = 3) -> dict:
    work = cfg.work_dir
    unmatched = json.loads((work / "unmatched.json").read_text()) if (work / "unmatched.json").exists() else []
    matches = json.loads((work / "matches.json").read_text()) if (work / "matches.json").exists() else []
    records = {r["restriction_id"]: r for r in read_restrictions(work / "restrictions.jsonl")}
    import geopandas as gpd

    lakes = gpd.read_parquet(work / "lakes.parquet")
    matcher = Matcher(lakes, load_plss(cfg))
    suggester = Suggester(matcher, top=top)
    lake_by_id = {int(i): row for row, i in enumerate(lakes["id"].to_numpy())}

    suggestions = []
    for u in sorted(unmatched, key=lambda u: (u.get("county") or "", u.get("lake_name_raw") or "")):
        if u.get("kind") == "waterway":
            continue
        rec = records.get(u["restriction_id"])
        if rec:
            suggestions.append(asdict(suggester.suggest(rec)))

    confirm = []
    for m in sorted(matches, key=lambda m: m["score"]):
        if not m.get("needs_review"):
            continue
        rec = records.get(m["restriction_id"], {})
        row = lake_by_id.get(int(m["lake_id"]))
        matched = suggester._lake(row, m["score"], m["method"]) if row is not None else None
        confirm.append({
            "restriction_id": m["restriction_id"], "lake_name_raw": rec.get("lake_name_raw"), "county": rec.get("county"),
            "township": rec.get("township"), "rule_id": rec.get("rule_id"), "plss": plss_text(rec.get("plss")),
            "matched": asdict(matched) if matched else {"lake_id": m["lake_id"]},
            "reject": f"- {{restriction_id: {m['restriction_id']}, lake_id: {m['lake_id']}}}",
        })
    return {"suggested_matches": suggestions, "confirm_low_confidence": confirm}


HEADER = """# Generated by `seaplane suggest`; regenerate after every pipeline run. Do not edit this file.
#
# suggested_matches: lake-named restrictions the matcher could not place. Each lists candidate lakes with a
#   score (1.0 = exact name in the cited section) and how it was found. To accept one, copy its `paste` line
#   under `matches:` in overrides.yaml (or write your own with a different lake_id).
# confirm_low_confidence: matches the pipeline applied with a score under 0.8. They are live already; to
#   reject one, copy its `reject` line under `unmatch:` in overrides.yaml.
#
# Rivers, creeks, channels, and bays are not listed: they have no lake polygon.
"""


def run(cfg: Config, args) -> int:
    top = getattr(args, "top", 3)
    data = build(cfg, top=top)
    out = cfg.manual_dir / "overrides.suggested.yaml"
    if getattr(args, "out", None):
        from pathlib import Path

        out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(HEADER + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120))
    with_cand = sum(1 for s in data["suggested_matches"] if s["candidates"])
    hinted = sum(1 for s in data["suggested_matches"] if s["hints"])
    log.info(
        "wrote %s: %d unmatched lake restrictions (%d with candidates, %d with hints), %d low-confidence matches to confirm",
        out, len(data["suggested_matches"]), with_cand, hinted, len(data["confirm_low_confidence"]),
    )
    return 0
