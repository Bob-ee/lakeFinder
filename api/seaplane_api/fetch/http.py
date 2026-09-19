"""Shared httpx wrapper: one identifying User-Agent, a 10 s timeout, one retry, no exceptions out.

api.weather.gov rejects requests without a User-Agent, and NOAA asks that it identify the client.
`lakeFinder (https://github.com/Bob-ee/lakeFinder)` is that identifier, used for every feed so the
service is equally identifiable to all of them. **No email address goes in a header, URL, or body**
anywhere in this package.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "lakeFinder (https://github.com/Bob-ee/lakeFinder)"
TIMEOUT_S = 10.0
RETRIES = 1


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=TIMEOUT_S,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    )


async def _get(c: httpx.AsyncClient, url: str, params: dict | None, label: str) -> tuple[httpx.Response | None, str | None]:
    last: str | None = None
    for attempt in range(RETRIES + 1):
        try:
            r = await c.get(url, params=params)
            r.raise_for_status()
            return r, None
        except httpx.HTTPStatusError as e:
            last = f"{label}: HTTP {e.response.status_code}"
            if 400 <= e.response.status_code < 500:
                break  # a 404 will not get better on a retry
        except (TimeoutError, httpx.HTTPError) as e:
            last = f"{label}: {type(e).__name__}"
        if attempt < RETRIES:
            await asyncio.sleep(0.5)
    log.warning("fetch failed: %s", last)
    return None, last


async def get_json(c: httpx.AsyncClient, url: str, params: dict | None = None, *, label: str) -> tuple[Any | None, str | None]:
    r, err = await _get(c, url, params, label)
    if r is None:
        return None, err
    try:
        return r.json(), None
    except ValueError:
        return None, f"{label}: bad JSON"


async def get_text(c: httpx.AsyncClient, url: str, params: dict | None = None, *, label: str) -> tuple[str | None, str | None]:
    r, err = await _get(c, url, params, label)
    return (None, err) if r is None else (r.text, None)
