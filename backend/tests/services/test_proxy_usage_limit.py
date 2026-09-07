"""The production 403 is an exhausted Apify account, before any portal request."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import settings
from app.services import apify, ficha, importer, proxy_access

PROXY = 'http://groups-RESIDENTIAL,country-AR:private-password@proxy.apify.com:8000'
URL = 'https://www.zonaprop.com.ar/propiedades/clasificado/casa-12345678.html'
LIMIT = {'connected': False, 'connectionError': 'Monthly usage hard limit exceeded'}


def mock_proxy(monkeypatch, payload):
    requests = []
    original = httpx.AsyncClient

    def transport(request):
        requests.append(request)
        if request.url.host == 'proxy.apify.com':
            return httpx.Response(200, json=payload)
        raise httpx.ProxyError('403 Forbidden')

    def client(**kwargs):
        assert kwargs['proxy'] == PROXY
        return original(transport=httpx.MockTransport(transport))

    monkeypatch.setattr(settings, 'SCRAPER_PROXY_URL', PROXY)
    monkeypatch.setattr(proxy_access.httpx, 'AsyncClient', client)
    return requests


async def test_status_query_distinguishes_account_quota_from_portal_block(monkeypatch):
    requests = mock_proxy(monkeypatch, LIMIT)
    with pytest.raises(proxy_access.ProxyAccessError, match='límite mensual') as exc:
        await proxy_access.check_apify_proxy_limit(PROXY)
    assert 'private-password' not in str(exc.value)
    assert len(requests) == 1
    assert str(requests[0].url) == 'http://proxy.apify.com/?format=json'


@pytest.mark.parametrize('payload', [
    {'connected': True}, {'connected': False, 'connectionError': 'Unknown failure'},
    {}, [], {'connected': False, 'connectionError': None},
])
async def test_unknown_proxy_status_does_not_invent_a_quota_failure(monkeypatch, payload):
    mock_proxy(monkeypatch, payload)
    await proxy_access.check_apify_proxy_limit(PROXY)


@pytest.mark.parametrize('url', [None, '', 'http://user:pw@proxy.example:8000',
                                     'http://proxy.apify.com.example:8000'])
async def test_only_apify_proxies_receive_the_apify_status_probe(url):
    await proxy_access.check_apify_proxy_limit(url)


async def test_status_probe_timeout_preserves_normal_proxy_error_handling(monkeypatch):
    original = httpx.AsyncClient
    def timeout(request):
        raise httpx.ReadTimeout('status unavailable')
    monkeypatch.setattr(proxy_access.httpx, 'AsyncClient', lambda **kw: original(
        transport=httpx.MockTransport(timeout),
    ))
    await proxy_access.check_apify_proxy_limit(PROXY)


async def test_import_does_not_retry_an_exhausted_account_as_a_browser(monkeypatch):
    mock_proxy(monkeypatch, LIMIT)
    first = AsyncMock(side_effect=httpx.ProxyError('403 Forbidden'))
    monkeypatch.setattr(importer, '_fetch_html_httpx', first)
    expensive = AsyncMock(side_effect=AssertionError('Quota failures cannot be fixed by retries'))
    monkeypatch.setattr(importer, 'render_page_html', expensive)
    monkeypatch.setattr(importer, 'fetch_page_html_via_actor', expensive)
    with pytest.raises(proxy_access.ProxyAccessError, match='límite mensual'):
        await importer._fetch_html(URL)
    first.assert_awaited_once_with(URL)
    expensive.assert_not_called()


async def test_public_gallery_ladder_does_not_start_paid_actor_on_quota_failure(monkeypatch):
    requests = mock_proxy(monkeypatch, LIMIT)
    expensive = AsyncMock(side_effect=AssertionError('No actor or browser against exhausted proxy'))
    monkeypatch.setattr(apify, 'render_page_html', expensive)
    monkeypatch.setattr(apify, 'fetch_page_html_via_actor', expensive)
    with pytest.raises(proxy_access.ProxyAccessError, match='límite mensual'):
        await ficha._zonaprop_gallery(URL)
    assert len(requests) == 2
    expensive.assert_not_called()


@pytest.mark.parametrize('url', [URL, 'https://www.mercadolibre.com.ar/p/MLA123'])
async def test_optional_enrichment_keeps_existing_photos_on_quota_failure(monkeypatch, url):
    mock_proxy(monkeypatch, LIMIT)
    prop = {'id': 'p1', 'url_origen': url, 'imagenes': ['old.jpg'], 'ficha_enriched': True}
    result = await ficha.enrich_ficha(prop, None)
    assert result['imagenes'] == ['old.jpg']


async def test_import_api_explains_quota_instead_of_telling_user_to_retry(monkeypatch):
    from app.api.v1 import properties

    class Database:
        def table(self, name): return self
        def select(self, *args): return self
        def eq(self, *args): return self
        def limit(self, *args): return self
        async def execute(self): return SimpleNamespace(data=[])

    app = FastAPI()
    app.include_router(properties.router, prefix='/properties')
    app.state.supabase = Database()
    api_client = httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test')
    mock_proxy(monkeypatch, LIMIT)
    monkeypatch.setattr(importer, '_fetch_html_httpx', AsyncMock(
        side_effect=httpx.ProxyError('403 Forbidden'),
    ))
    async with api_client as client:
        response = await client.post('/properties/import', json={'urls': [URL]})
    result = response.json()['results'][0]
    assert result['status'] == 'error'
    assert 'límite mensual' in result['error']
    assert 'tiempo de espera' not in result['error']
    assert 'private-password' not in response.text
