"""Ficha Propio must not download one listing three times or wait for an actor."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import settings
from app.services import apify, ficha, importer

URL = ('https://www.zonaprop.com.ar/propiedades/clasificado/'
       'veclphin-duplex-en-venta-en-la-plata-2-dorm.-58532575.html')
PHOTOS = [f'https://img.zonapropcdn.com/test-property/photo-{i}.jpg' for i in range(27)]
HTML = (
    '<html><body><h1>Dúplex en La Plata</h1><p>' + 'Tres ambientes con patio. ' * 60
    + '</p><img src="https://example.com/recommended-house.jpg">'
    + '<script>var listing = {"pictures": '
    + json.dumps([{'resizeUrl1200x1200': url} for url in PHOTOS]) + '};</script></body></html>'
)


class Database:
    def __init__(self, existing=None):
        self.rows = existing or []
        self.result = []

    def table(self, name):
        assert name == 'properties'
        return self

    def select(self, *args):
        self.result = list(self.rows)
        return self

    def eq(self, *args):
        return self

    def limit(self, *args):
        return self

    def insert(self, row):
        saved = {**row, 'id': 'property-1'}
        self.rows.append(saved)
        self.result = [saved]
        return self

    async def execute(self):
        return SimpleNamespace(data=self.result)


async def test_import_reuses_the_downloaded_html_for_the_entire_gallery(monkeypatch):
    fetch = AsyncMock(return_value=HTML)
    redundant = AsyncMock(side_effect=AssertionError('The listing was already fetched'))
    monkeypatch.setattr(importer, '_fetch_html_httpx', fetch)
    monkeypatch.setattr(importer, 'portal_gallery_from_url', redundant)
    monkeypatch.setattr(importer, 'harvest_page_images', redundant)
    monkeypatch.setattr(importer, 'render_page_html', redundant)
    monkeypatch.setattr(importer, 'fetch_page_html_via_actor', redundant)
    monkeypatch.setattr(importer, '_extract_llm', AsyncMock(return_value=({
        'titulo': 'Dúplex en La Plata', 'tipo_propiedad': 'ph', 'tipo_operacion': 'venta',
        'direccion': 'Calle de prueba, La Plata', 'ambientes': 3,
    }, None)))
    monkeypatch.setattr(importer, 'record_llm_usage', AsyncMock())
    result = await importer.import_property_from_url(Database(), URL)
    assert result['property']['imagenes'] == PHOTOS
    assert result['created'] is True
    fetch.assert_awaited_once_with(URL)
    redundant.assert_not_called()


async def test_a_short_real_gallery_also_skips_the_second_fetch(monkeypatch):
    fetch = AsyncMock(return_value=HTML.replace(
        json.dumps([{'resizeUrl1200x1200': url} for url in PHOTOS]),
        json.dumps([{'resizeUrl1200x1200': PHOTOS[0]}]),
    ))
    monkeypatch.setattr(importer, '_fetch_html', fetch)
    _, photos = await importer._fetch_page(URL)
    assert photos == PHOTOS[:1]


async def test_initial_fetch_uses_the_configured_proxy(monkeypatch):
    seen = {}
    original = httpx.AsyncClient

    def client(**kwargs):
        seen.update(kwargs)
        return original(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=HTML)))

    monkeypatch.setattr(settings, 'SCRAPER_PROXY_URL', 'http://proxy.example:8000')
    monkeypatch.setattr(importer.httpx, 'AsyncClient', client)
    assert await importer._fetch_html_httpx(URL) == HTML
    assert seen['proxy'] == 'http://proxy.example:8000'
    assert seen['timeout'] == 8


async def test_proxy_connection_failure_tries_direct_http_within_the_import_budget(monkeypatch):
    fetch = AsyncMock(side_effect=[httpx.ProxyError('403 Forbidden'), HTML])
    browser = AsyncMock(side_effect=AssertionError('Direct HTTP already read the gallery'))
    monkeypatch.setattr(importer, '_fetch_html_httpx', fetch)
    monkeypatch.setattr(importer, 'render_page_html', browser)
    assert await importer._fetch_html(URL) == HTML
    assert fetch.await_args_list[1].kwargs == {'use_proxy': False}
    browser.assert_not_called()


async def test_direct_fallback_does_not_reuse_the_broken_environment_proxy(monkeypatch):
    seen = {}
    original = httpx.AsyncClient

    def client(**kwargs):
        seen.update(kwargs)
        return original(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=HTML)))

    monkeypatch.setattr(importer.httpx, 'AsyncClient', client)
    assert await importer._fetch_html_httpx(URL, use_proxy=False) == HTML
    assert seen['proxy'] is None
    assert seen['trust_env'] is False


async def test_blocked_zonaprop_uses_the_proxy_in_the_browser_too(monkeypatch):
    monkeypatch.setattr(settings, 'SCRAPER_PROXY_URL', 'http://proxy.example:8000')
    monkeypatch.setattr(importer, '_fetch_html_httpx', AsyncMock(side_effect=importer.PortalBlocked))
    browser = AsyncMock(return_value=HTML)
    actor = AsyncMock()
    monkeypatch.setattr(importer, 'render_page_html', browser)
    monkeypatch.setattr(importer, 'fetch_page_html_via_actor', actor)
    assert await importer._fetch_html(URL) == HTML
    browser.assert_awaited_once_with(URL, proxy={'server': 'http://proxy.example:8000'})
    actor.assert_not_called()


async def test_zonaprop_never_starts_the_300_second_actor(monkeypatch):
    monkeypatch.setattr(importer, '_fetch_html_httpx', AsyncMock(side_effect=importer.PortalBlocked))
    monkeypatch.setattr(importer, 'render_page_html', AsyncMock(return_value=None))
    actor = AsyncMock()
    monkeypatch.setattr(importer, 'fetch_page_html_via_actor', actor)
    with pytest.raises(importer.PortalBlocked, match='ZonaProp'):
        await importer._fetch_html(URL)
    actor.assert_not_called()


async def test_a_stuck_fetch_has_a_total_deadline_and_is_cancelled(monkeypatch):
    cancelled = asyncio.Event()

    async def stuck(url):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(importer, '_ZONAPROP_FETCH_TIMEOUT', 0.01)
    monkeypatch.setattr(importer, '_fetch_html_httpx', stuck)
    with pytest.raises(importer.PortalBlocked, match='tiempo de espera'):
        await asyncio.wait_for(importer._fetch_html(URL), timeout=1)
    assert cancelled.is_set()


async def test_fresh_import_enrichment_does_not_repeat_photo_recovery(monkeypatch):
    recover = AsyncMock(side_effect=AssertionError('Import already recovered the gallery'))
    monkeypatch.setattr(ficha, '_enrich_gallery', recover)
    monkeypatch.setattr(ficha, '_persist', AsyncMock())
    prop = {'id': 'p1', 'url_origen': URL, 'imagenes': PHOTOS[:1], 'descripcion': ''}
    result = await ficha.enrich_ficha(prop, None, refresh_gallery=False)
    assert result['ficha_enriched'] is True
    assert result['imagenes'] == PHOTOS[:1]
    recover.assert_not_called()


async def test_existing_ficha_keeps_its_photos_when_the_recovery_times_out(monkeypatch):
    async def stuck(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(ficha, '_ZONAPROP_GALLERY_TIMEOUT', 0.01)
    monkeypatch.setattr(ficha, '_fetch_listing_html', stuck)
    prop = {'url_origen': URL, 'fuente': 'manual', 'ficha_enriched': True, 'imagenes': PHOTOS[:1]}
    result = await asyncio.wait_for(ficha.enrich_ficha(prop, None), timeout=1)
    assert result['imagenes'] == PHOTOS[:1]


async def test_existing_ficha_does_not_start_browser_or_actor_for_photos(monkeypatch):
    monkeypatch.setattr(ficha, '_fetch_listing_html', AsyncMock(return_value=(False, None)))
    expensive = AsyncMock(side_effect=AssertionError('Photo recovery must stay interactive'))
    monkeypatch.setattr(apify, 'render_page_html', expensive)
    monkeypatch.setattr(apify, 'fetch_page_html_via_actor', expensive)
    prop = {'url_origen': URL, 'fuente': 'manual', 'ficha_enriched': True, 'imagenes': PHOTOS[:1]}
    result = await ficha.enrich_ficha(prop, None)
    assert result['imagenes'] == PHOTOS[:1]
    expensive.assert_not_called()


@pytest.mark.parametrize('refresh', [True, False])
async def test_enrich_endpoint_passes_the_gallery_preference(monkeypatch, refresh):
    from app.api.v1 import properties

    prop = {'id': 'p1', 'url_origen': URL}
    enrich = AsyncMock(return_value=prop)
    monkeypatch.setattr(properties, '_enrich_ficha', enrich)
    app = FastAPI()
    app.include_router(properties.router, prefix='/properties')
    app.state.supabase = Database([prop])
    query = '' if refresh else '?refresh_gallery=false'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
        response = await c.post(f'/properties/p1/enrich{query}')
    assert response.status_code == 200
    enrich.assert_awaited_once_with(prop, app.state.supabase, refresh_gallery=refresh)
