"""Crawl the DNR Special Local Watercraft Controls pages (pipeline stage 1).

`fetch.py` calls `crawl(cfg)`; nothing else here touches the network.

The county -> URL map is always read off the index accordion's hrefs, never
constructed from a slug template: Oakland and Emmet end in
`/local-watercraft-controls` while the other 81 counties end in `/watercraft`
(docs/dnr-pages.md section 1).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

from .config import USER_AGENT, Config

log = logging.getLogger(__name__)

INDEX_URL = "https://www.michigan.gov/dnr/managing-resources/laws/controls"
SITE_ROOT = "https://www.michigan.gov"
EXPECTED_COUNTIES = 83

REQUEST_TIMEOUT = 30.0
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 2.0
POLITE_DELAY = 0.5


def _slug_from_url(url: str) -> str:
    parts = [p for p in url.split("?")[0].split("/") if p]
    if "localcontrols" in parts:
        i = parts.index("localcontrols")
        if i + 1 < len(parts):
            return parts[i + 1].lower()
    return parts[-1].lower() if parts else ""


def parse_index(html: str) -> list[tuple[str, str, str]]:
    """Return `[(county_name, slug, url)]` for every accordion item on the index page.

    County name is the accordion heading as printed ("Grand Traverse",
    "St. Clair"); slug is the URL's own path segment, not a derived one.
    """
    soup = BeautifulSoup(html, "lxml")
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for item in soup.select("li.accordion-item"):
        heading = item.select_one(".accordion-item__heading")
        if heading is None:
            continue
        name = " ".join(heading.get_text(" ", strip=True).split())
        if not name:
            continue
        href = None
        for a in item.select("a[href]"):
            candidate = str(a.get("href", ""))
            if "localcontrols" not in candidate:
                continue
            if candidate.rstrip("/").endswith("/hunting"):
                continue
            href = candidate
            break
        if href is None:
            log.warning("no watercraft link for county %s", name)
            continue
        url = href if href.startswith("http") else SITE_ROOT + href
        slug = _slug_from_url(url)
        if slug in seen:
            continue
        seen.add(slug)
        out.append((name, slug, url))
    return out


def _get(client: httpx.Client, url: str) -> str:
    last: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.text
        except (httpx.HTTPError, OSError) as exc:  # network error or non-2xx
            last = exc
            if attempt == MAX_ATTEMPTS:
                break
            wait = BACKOFF_SECONDS * (2 ** (attempt - 1))
            log.warning("fetch %s failed (attempt %d/%d): %s; retrying in %.1fs",
                        url, attempt, MAX_ATTEMPTS, exc, wait)
            time.sleep(wait)
    raise RuntimeError(f"failed to fetch {url} after {MAX_ATTEMPTS} attempts: {last}")


def dnr_dir(cfg: Config) -> Path:
    return cfg.raw_dir / "dnr"


def manifest_path(cfg: Config) -> Path:
    return dnr_dir(cfg) / "counties.json"


def index_path(cfg: Config) -> Path:
    return dnr_dir(cfg) / "_index.html"


def page_path(cfg: Config, slug: str) -> Path:
    return dnr_dir(cfg) / f"{slug}.html"


def load_manifest(cfg: Config) -> dict[str, dict[str, Any]]:
    path = manifest_path(cfg)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def crawl(cfg: Config) -> dict[str, dict[str, Any]]:
    """Fetch the index and every (selected) county page into `raw_dir/dnr/`.

    Returns the manifest `{slug: {name, url, fetched_at, sha256}}`, which is
    also written to `raw_dir/dnr/counties.json`. Cached pages are reused unless
    `cfg.force`.
    """
    out_dir = dnr_dir(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(cfg)

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    with httpx.Client(headers=headers, timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
        idx_file = index_path(cfg)
        if idx_file.exists() and not cfg.force:
            log.info("index: using cached %s", idx_file)
            index_html = idx_file.read_text(encoding="utf-8")
        else:
            log.info("index: fetching %s", INDEX_URL)
            index_html = _get(client, INDEX_URL)
            idx_file.write_text(index_html, encoding="utf-8")
            time.sleep(POLITE_DELAY)

        counties = parse_index(index_html)
        if len(counties) != EXPECTED_COUNTIES:
            log.warning("index listed %d counties, expected %d", len(counties), EXPECTED_COUNTIES)
        if not counties:
            raise RuntimeError("index page yielded no counties; markup may have changed")

        wanted = set(cfg.counties) if cfg.counties else None
        selected = [c for c in counties if wanted is None or c[1] in wanted]
        if wanted:
            missing = wanted - {c[1] for c in selected}
            for slug in sorted(missing):
                log.warning("county %r is not on the DNR index page", slug)

        for i, (name, slug, url) in enumerate(selected):
            path = page_path(cfg, slug)
            if path.exists() and not cfg.force and slug in manifest:
                log.debug("%s: cached", slug)
                continue
            log.info("[%d/%d] %s: %s", i + 1, len(selected), slug, url)
            html = _get(client, url)
            path.write_text(html, encoding="utf-8")
            manifest[slug] = {
                "name": name,
                "url": url,
                "fetched_at": _now(),
                "sha256": _sha256(html),
            }
            manifest_path(cfg).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
            time.sleep(POLITE_DELAY)

    manifest_path(cfg).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    log.info("dnr crawl: %d county pages in %s", len(manifest), out_dir)
    return manifest
