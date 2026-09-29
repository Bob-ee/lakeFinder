"""FastAPI app: the five endpoints in the data contract's api table.

Single user, tailnet only, no auth (design 8). The client reads the briefing as the static file
`/data/briefing.json` and never through `/api`; these endpoints exist for settings, airport lookup,
health, and a manual refresh.

The scheduler lives in the app state so `PUT /api/settings` can swap the settings it uses and kick
off a `manual` run without restarting anything.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from . import service
from .fetch import aviationweather
from .fetch.http import client
from .forecast import router as forecast_router
from .paths import briefing_path, read_json
from .scheduler import Scheduler
from .settings import Settings, save_settings
from .wind import router as wind_router

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.scheduler = Scheduler()
    await app.state.scheduler.start()
    try:
        yield
    finally:
        await app.state.scheduler.stop()


def create_app(*, scheduler: Scheduler | None = None) -> FastAPI:
    """Build the app. Passing a `scheduler` skips the lifespan, which is what the tests want."""
    if scheduler is not None:
        app = FastAPI(title="seaplane-api", version="0.1.0")
        app.state.scheduler = scheduler
    else:
        app = FastAPI(title="seaplane-api", version="0.1.0", lifespan=lifespan)

    app.include_router(wind_router)
    app.include_router(forecast_router)

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        briefing = read_json(briefing_path())
        sched: Scheduler = app.state.scheduler
        return {
            "ok": True,
            "briefing_generated_at": (briefing or {}).get("generated_at"),
            "next_run_local": sched.next_run_local(datetime.now(UTC)),
        }

    @app.get("/api/settings")
    async def get_settings() -> dict[str, Any]:
        sched: Scheduler = app.state.scheduler
        return sched.settings.model_dump(mode="json")

    @app.put("/api/settings")
    async def put_settings(payload: dict[str, Any]) -> JSONResponse:
        try:
            new = Settings.model_validate(payload)
        except Exception as e:  # pydantic ValidationError, and anything else a bad body causes
            raise HTTPException(status_code=422, detail=str(e)) from e
        # `home_water.id` has to exist in `index.json`. That is a filesystem question, so it cannot
        # live in the pydantic model; a client that typed an id would otherwise get a briefing with
        # a quiet error in it instead of a 422 on the form.
        if new.home_water is not None and not service.index_has(new.home_water.id):
            raise HTTPException(
                status_code=422, detail=f"home_water: id {new.home_water.id} is not in index.json"
            )
        sched: Scheduler = app.state.scheduler
        sched.settings = new
        save_settings(new)
        # Design 4: settings changes run the briefing immediately, but the PUT returns right away.
        asyncio.create_task(_background_run(sched))
        return JSONResponse(new.model_dump(mode="json"))

    @app.get("/api/airports/{ident}")
    async def get_airport(ident: str) -> dict[str, Any]:
        async with client() as c:
            raw, err = await aviationweather.fetch_airport(c, ident.upper())
        if raw is None:
            raise HTTPException(status_code=404, detail=err or f"{ident} not found")
        return aviationweather.to_home_airport(raw)

    @app.post("/api/briefing/refresh")
    async def refresh() -> dict[str, Any]:
        sched: Scheduler = app.state.scheduler
        return await sched.run(run_kind="manual")

    return app


async def _background_run(sched: Scheduler) -> None:
    try:
        await sched.run(run_kind="manual")
    except Exception:  # a failed settings-triggered run must not surface as a 500 on the PUT
        log.exception("settings-triggered briefing failed")


app = create_app()
