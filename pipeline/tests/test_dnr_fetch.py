"""Index parsing and the crawl cache. No network: the index fixture stands in for it."""

from __future__ import annotations

import json

import httpx
import pytest

from seaplane_pipeline import dnr_fetch
from seaplane_pipeline.config import Config

EXPECTED_OAKLAND = (
    "https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/oakland/"
    "local-watercraft-controls"
)
EXPECTED_EMMET = (
    "https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/emmet/"
    "local-watercraft-controls"
)


@pytest.fixture
def index_html(fixtures_dir) -> str:
    return (fixtures_dir / "dnr_pages" / "_index.html").read_text(encoding="utf-8")


def test_parse_index_finds_all_83_counties(index_html: str) -> None:
    counties = dnr_fetch.parse_index(index_html)
    assert len(counties) == dnr_fetch.EXPECTED_COUNTIES == 83
    slugs = [slug for _, slug, _ in counties]
    assert len(set(slugs)) == 83


def test_parse_index_urls_are_absolute_and_watercraft_only(index_html: str) -> None:
    for name, slug, url in dnr_fetch.parse_index(index_html):
        assert url.startswith("https://www.michigan.gov/dnr/"), (name, url)
        assert "/localcontrols/" in url
        assert not url.endswith("/hunting")
        assert slug and slug.islower()


def test_parse_index_handles_the_two_url_anomalies(index_html: str) -> None:
    """Oakland and Emmet end in /local-watercraft-controls, the other 81 in /watercraft."""
    by_slug = {slug: url for _, slug, url in dnr_fetch.parse_index(index_html)}
    assert by_slug["oakland"] == EXPECTED_OAKLAND
    assert by_slug["emmet"] == EXPECTED_EMMET
    others = [url for slug, url in by_slug.items() if slug not in ("oakland", "emmet")]
    assert len(others) == 81
    assert all(url.endswith("/watercraft") for url in others)


def test_parse_index_keeps_multiword_county_names(index_html: str) -> None:
    by_slug = {slug: name for name, slug, _ in dnr_fetch.parse_index(index_html)}
    assert by_slug["grandtraverse"] == "Grand Traverse"
    assert by_slug["presqueisle"] == "Presque Isle"
    assert by_slug["stclair"] == "St. Clair"
    assert by_slug["stjoseph"] == "St. Joseph"
    assert by_slug["vanburen"] == "Van Buren"


def test_parse_index_on_junk_returns_nothing() -> None:
    assert dnr_fetch.parse_index("<html><body><p>nope</p></body></html>") == []


def test_crawl_uses_the_cache_and_writes_a_manifest(tmp_path, monkeypatch, fixtures_dir) -> None:
    """A cached index plus a cached county page means zero requests."""
    monkeypatch.setenv("SEAPLANE_DATA_DIR", str(tmp_path))
    cfg = Config(counties=["oakland"])
    cfg.ensure_dirs()
    pages = fixtures_dir / "dnr_pages"
    dnr_fetch.dnr_dir(cfg).mkdir(parents=True, exist_ok=True)
    dnr_fetch.index_path(cfg).write_text(
        (pages / "_index.html").read_text(encoding="utf-8"), encoding="utf-8"
    )
    dnr_fetch.page_path(cfg, "oakland").write_text(
        (pages / "oakland.html").read_text(encoding="utf-8"), encoding="utf-8"
    )
    dnr_fetch.manifest_path(cfg).write_text(
        json.dumps({"oakland": {"name": "Oakland", "url": EXPECTED_OAKLAND, "fetched_at": "x", "sha256": "y"}}),
        encoding="utf-8",
    )

    def explode(*_args, **_kwargs):  # pragma: no cover - the test fails if it runs
        raise AssertionError("crawl hit the network despite a warm cache")

    monkeypatch.setattr(dnr_fetch, "_get", explode)
    manifest = dnr_fetch.crawl(cfg)
    assert set(manifest) == {"oakland"}
    assert manifest["oakland"]["url"] == EXPECTED_OAKLAND
    assert json.loads(dnr_fetch.manifest_path(cfg).read_text(encoding="utf-8")) == manifest


def test_crawl_fetches_only_the_selected_county(tmp_path, monkeypatch, fixtures_dir) -> None:
    monkeypatch.setenv("SEAPLANE_DATA_DIR", str(tmp_path))
    cfg = Config(counties=["cheboygan"])
    cfg.ensure_dirs()
    pages = fixtures_dir / "dnr_pages"
    index_html = (pages / "_index.html").read_text(encoding="utf-8")
    county_html = (pages / "cheboygan.html").read_text(encoding="utf-8")
    requested: list[str] = []

    def fake_get(_client, url: str) -> str:
        requested.append(url)
        return index_html if url == dnr_fetch.INDEX_URL else county_html

    monkeypatch.setattr(dnr_fetch, "_get", fake_get)
    monkeypatch.setattr(dnr_fetch.time, "sleep", lambda *_a: None)
    manifest = dnr_fetch.crawl(cfg)

    assert requested[0] == dnr_fetch.INDEX_URL
    assert len(requested) == 2
    assert requested[1].endswith("/cheboygan/watercraft")
    assert set(manifest) == {"cheboygan"}
    entry = manifest["cheboygan"]
    assert entry["name"] == "Cheboygan"
    assert len(entry["sha256"]) == 64
    assert entry["fetched_at"].endswith("Z")
    assert dnr_fetch.page_path(cfg, "cheboygan").read_text(encoding="utf-8") == county_html


def test_crawl_retries_then_gives_up(monkeypatch) -> None:
    calls = {"n": 0}

    class FakeClient:
        def get(self, _url):
            calls["n"] += 1
            raise httpx.ConnectError("connection reset")

    monkeypatch.setattr(dnr_fetch.time, "sleep", lambda *_a: None)
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        dnr_fetch._get(FakeClient(), "https://example.invalid/x")
    assert calls["n"] == dnr_fetch.MAX_ATTEMPTS == 3
