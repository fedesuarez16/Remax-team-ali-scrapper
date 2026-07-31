"""Argenprop pagination: unlike ZonaProp (one actor run per page — the
crawlerbros browser dies after page 1), the generic `website-content-crawler`
actor can crawl multiple `startUrls` in a single run, which also avoids paying
for N separate AWS WAF challenges. `_scrape_argenprop` must therefore launch
exactly ONE actor run with every paginated URL, capped at
`settings.ARGENPROP_MAX_PAGES` and hard-capped at 10 (robots.txt only
`Allow`s `pagina-1` through `pagina-10`).
"""
from typing import Any

import pytest

from app.models.property import ScrapingFilters
from app.services import apify
from app.services.apify import ApifyService, _argenprop_search_urls

_HTML_NO_LISTINGS = '<html><body></body></html>'


def _filters() -> ScrapingFilters:
    return ScrapingFilters(zona='Palermo', tipo_operacion='venta', tipos_propiedad=['departamento'])


async def _noop_progress(source: str, status: str, count: int) -> None:
    return None


@pytest.fixture()
def service() -> ApifyService:
    return ApifyService(api_token='dummy-token')


@pytest.fixture(autouse=True)
def _no_zona_resolver(monkeypatch: pytest.MonkeyPatch):
    """Keep pagination tests off the network: the zona resolver (portal
    autocomplete API) answers None here — URL building falls back to
    `_slugify`, which is exactly the pre-resolver behavior these tests pin."""
    async def _none(zona: str) -> None:
        return None
    monkeypatch.setattr(apify, '_argenprop_resolve_zona_slug', _none)


def test_search_urls_base_page_has_no_query_string() -> None:
    urls = _argenprop_search_urls(_filters(), max_pages=3)
    assert urls[0] == 'https://www.argenprop.com/departamentos/venta/palermo'


def test_search_urls_paginate_without_equals_sign() -> None:
    urls = _argenprop_search_urls(_filters(), max_pages=3)
    assert urls[1] == 'https://www.argenprop.com/departamentos/venta/palermo?pagina-2'
    assert urls[2] == 'https://www.argenprop.com/departamentos/venta/palermo?pagina-3'


def test_search_urls_hard_capped_at_ten_pages() -> None:
    urls = _argenprop_search_urls(_filters(), max_pages=999)
    assert len(urls) == 10


async def test_single_actor_run_carries_all_start_urls(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, 'ARGENPROP_MAX_PAGES', 3)

    calls: list[dict[str, Any]] = []

    async def fake_run(src: str, actor: str, input_data: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append(input_data)
        assert actor == 'apify~website-content-crawler'
        return [{'url': u['url'], 'html': _HTML_NO_LISTINGS} for u in input_data['startUrls']]

    monkeypatch.setattr(service, '_run_actor', fake_run)
    await service.scrape_source('argenprop', _filters(), _noop_progress)

    assert len(calls) == 1
    assert len(calls[0]['startUrls']) == 3
    assert calls[0]['maxCrawlPages'] == 3
    assert calls[0]['saveHtml'] is True


async def test_run_requests_raw_html_so_the_photo_carousel_survives(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `saveHtml: true` alone returns Readability-transformed DOM, which strips
    # the whole `ul.card__photos` carousel — every Argenprop result then reaches
    # the UI photoless. `htmlTransformer: 'none'` keeps the raw server HTML,
    # where the gallery is already fully rendered (no JS needed).
    from app.core.config import settings
    monkeypatch.setattr(settings, 'ARGENPROP_MAX_PAGES', 1)

    calls: list[dict[str, Any]] = []

    async def fake_run(src: str, actor: str, input_data: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append(input_data)
        return []

    monkeypatch.setattr(service, '_run_actor', fake_run)
    await service.scrape_source('argenprop', _filters(), _noop_progress)

    assert calls[0]['htmlTransformer'] == 'none'


async def test_resolved_portal_slug_overrides_naive_slugify(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # "Gonnet" naively slugifies to /gonnet — which 301s to the nationwide
    # listing. The portal-autocomplete resolver answers "manuel-gonnet"
    # (Argenprop's real slug, verified live) and the run must use THAT.
    from app.core.config import settings
    monkeypatch.setattr(settings, 'ARGENPROP_MAX_PAGES', 1)

    async def fake_resolve(zona: str) -> str:
        assert zona == 'Gonnet'
        return 'manuel-gonnet'

    monkeypatch.setattr(apify, '_argenprop_resolve_zona_slug', fake_resolve)

    calls: list[dict[str, Any]] = []

    async def fake_run(src: str, actor: str, input_data: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append(input_data)
        return []

    monkeypatch.setattr(service, '_run_actor', fake_run)
    filters = ScrapingFilters(zona='Gonnet', tipo_operacion='venta', tipos_propiedad=['departamento'])
    await service.scrape_source('argenprop', filters, _noop_progress)

    assert calls[0]['startUrls'][0]['url'] == 'https://www.argenprop.com/departamentos/venta/manuel-gonnet'


async def test_pages_without_html_are_skipped_not_fatal(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run(src: str, actor: str, input_data: dict[str, Any]) -> list[dict[str, Any]]:
        return [{'url': 'https://www.argenprop.com/departamentos/venta/palermo', 'html': None}]

    monkeypatch.setattr(service, '_run_actor', fake_run)
    result = await service.scrape_source('argenprop', _filters(), _noop_progress)
    assert result == []
