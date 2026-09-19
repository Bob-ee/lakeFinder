"""One briefing run: read the lake index, fetch, build, write, push.

The only place that mixes the pure algorithm with the filesystem and the network, which keeps
`briefing/*` testable on fixtures.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import wavefield
from .briefing import lakes as lakes_mod
from .briefing.generate import build_briefing, collect_feeds
from .fetch.http import client
from .notify import push_outlook
from .paths import briefing_path, index_path, lake_extents_path, read_json, write_json_atomic
from .settings import Settings

log = logging.getLogger(__name__)

MISSING_EXTENTS_ERROR = "lake_extents.json missing; using longest chord"
MISSING_WAVE_POINTS_ERROR = "wave_points missing; lake-level waves only"


@dataclass
class LakeInputs:
    """Everything `data/out` contributes to one run."""

    candidates: list[lakes_mod.Candidate] = field(default_factory=list)
    home_water: lakes_mod.Candidate | None = None
    errors: list[str] = field(default_factory=list)


def load_candidates(settings: Settings) -> LakeInputs:
    """Candidate water from `data/out`: `index.json`, `lake_extents.json`, and the wave field.

    Each of the two optional packs degrades on its own. Without `lake_extents.json` the run falls
    back to the longest chord; without `wave_points.*` every water body is scored at lake level,
    which is what the briefing did before the wave field existed.
    """
    errors: list[str] = []
    index = read_json(index_path())
    if not isinstance(index, list):
        return LakeInputs(errors=[f"index.json: unreadable at {index_path()}"])

    extents = read_json(lake_extents_path())
    if not isinstance(extents, dict):
        extents = None
        errors.append(MISSING_EXTENTS_ERROR)

    wave_field = wavefield.load()
    if wave_field is None:
        errors.append(MISSING_WAVE_POINTS_ERROR)

    home = settings.home_airport
    candidates = lakes_mod.select_candidates(
        index,
        home_lat=home.lat,
        home_lon=home.lon,
        radius_nm=settings.radius_nm,
        min_run_ft=settings.limits.min_run_ft,
        public_access_only=settings.public_access_only,
        extents=extents,
        wave_field=wave_field,
    )

    home_water = None
    if settings.home_water is not None:
        home_water = lakes_mod.find_candidate(
            index,
            settings.home_water.id,
            home_lat=home.lat,
            home_lon=home.lon,
            extents=extents,
            wave_field=wave_field,
        )
        if home_water is None:
            errors.append(f"home_water: id {settings.home_water.id} is not in index.json")
    return LakeInputs(candidates=candidates, home_water=home_water, errors=errors)


def index_has(lake_id: int) -> bool:
    """Does `index.json` carry this water body? (`PUT /api/settings` validates `home_water.id`.)"""
    index = read_json(index_path())
    if not isinstance(index, list):
        return False
    return any(str(lake.get("id")) == str(lake_id) for lake in index)


async def run_briefing(
    settings: Settings,
    *,
    run_kind: str = "manual",
    run_at: str | None = None,
    out_path: Path | None = None,
) -> dict:
    """Fetch everything, build the briefing, write it atomically, and push when configured."""
    started = datetime.now(UTC)
    inputs = load_candidates(settings)
    candidates, errors = inputs.candidates, inputs.errors
    log.info(
        "briefing run (%s): %d candidate water bodies, home water %s",
        run_kind,
        len(candidates),
        inputs.home_water.name if inputs.home_water else "none",
    )

    async with client() as c:
        feeds = await collect_feeds(settings, candidates, client=c, home_water=inputs.home_water)

    target = out_path or briefing_path()
    previous = read_json(target)
    briefing = build_briefing(
        settings,
        feeds,
        candidates,
        now_utc=started,
        run_kind=run_kind,
        run_at=run_at,
        previous=previous if isinstance(previous, dict) else None,
        extra_errors=errors,
        home_water=inputs.home_water,
    )
    write_json_atomic(target, briefing)
    log.info(
        "briefing written in %.1fs: %s",
        (datetime.now(UTC) - started).total_seconds(),
        briefing["summary"][:120],
    )

    if run_kind == "outlook" and briefing.get("outlook"):
        await push_outlook(settings, briefing["outlook"].get("summary") or "")
    return briefing
