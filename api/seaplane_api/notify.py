"""Optional ntfy push of the outlook line.

Off unless `settings.notify.ntfy_url` is set, so nothing leaves the tailnet by default (design 3.4).
The body is the already-rendered `outlook.summary`; a failure is logged and swallowed, because a
push that did not go out must never cost the briefing that was already written.

No email address, and nothing else identifying, is sent -- only the topic URL the operator chose and
the briefing line itself.
"""
from __future__ import annotations

import logging

from .fetch.http import TIMEOUT_S, USER_AGENT
from .settings import Settings

log = logging.getLogger(__name__)


async def push_outlook(settings: Settings, message: str) -> bool:
    """POST `message` to the configured ntfy topic. Returns whether it went out."""
    url = settings.notify.ntfy_url
    if not url or not message:
        return False
    import httpx

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT}) as c:
            r = await c.post(
                url,
                content=message.encode("utf-8"),
                headers={"Title": "SeaRey outlook", "Tags": "small_airplane"},
            )
            r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001 - a push that did not go out must not cost the briefing
        log.warning("ntfy push failed: %s", e)
        return False
