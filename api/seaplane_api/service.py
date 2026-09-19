"""One briefing run: read the lake index, fetch, build, write, push.

The only place that mixes the pure algorithm with the filesystem and the network, which keeps
`briefing/*` testable on fixtures.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from .briefing import lakes as lakes_mod
from .briefing.generate import build_briefing, collect_feeds
from .fetch.http import client
from .notify import push_outlook
from .paths import briefing_path, index_path, lake_extents_path, read_json, write_json_atomic
from .settings import Settings

log = logging.getLogger(__name__)

MISSING_EXTENTS_ERROR = "lake_extents.json missing; using longest chord"


def load_candidates(settings: Settings) -> tuple[list[lakes_mod.Candidate], list[str]]:
    """Candidate lakes from `data/out/index.json` (+ `lake_extents.json` when the pipeline has it)."""
    errors: list[str] = []
    index = read_json(index_path())
    if not isinstance(index, list):
        return [], [f"index.json: unreadable at {index_path()}"]

    extents = read_json(lake_extents_path())
    if not isinstance(extents, dict):
        extents = None
        errors.append(MISSING_EXTENTS_ERROR)

    home = settings.home_airport
    candidates = lakes_mod.select_candidates(
        index,
        home_lat=home.lat,
        home_lon=home.lon,
        radius_nm=settings.radius_nm,
        min_run_ft=settings.limits.min_run_ft,
        public_access_only=settings.public_access_only,
        extents=extents,
    )
    return candidates, errors


async def run_briefing(settings: Settings, *, run_kind: str = "manual", run_at: str | None = None) -> dict:
    """Fetch everything, build the briefing, write it atomically, and push when configured."""
    started = datetime.now(UTC)
    candidates, errors = load_candidates(settings)
    log.info("briefing run (%s): %d candidate lakes", run_kind, len(candidates))

    async with client() as c:
        feeds = await collect_feeds(settings, candidates, client=c)

    previous = read_json(briefing_path())
    briefing = build_briefing(
        settings,
        feeds,
        candidates,
        now_utc=started,
        run_kind=run_kind,
        run_at=run_at,
        previous=previous if isinstance(previous, dict) else None,
        extra_errors=errors,
    )
    write_json_atomic(briefing_path(), briefing)
    log.info(
        "briefing written in %.1fs: %s",
        (datetime.now(UTC) - started).total_seconds(),
        briefing["summary"][:120],
    )

    if run_kind == "outlook" and briefing.get("outlook"):
        await push_outlook(settings, briefing["outlook"].get("summary") or "")
    return briefing
