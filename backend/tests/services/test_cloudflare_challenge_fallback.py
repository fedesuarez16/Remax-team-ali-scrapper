"""A Cloudflare challenge is a browser problem, not an IP problem.

The 403s that emptied production are not a ban. The body is Cloudflare's JS
challenge — `<title>Just a moment...</title>`, `server: cloudflare` — and
Cloudflare decides who to challenge partly from the TLS handshake. Same code,
same proxy config (`[groups-RESIDENTIAL,country-AR]`, confirmed from the
production log), same account: a macOS laptop mostly sails through and a
`python:3.12-slim` container is challenged every time. Different TLS stack,
different fingerprint.

Rotating exit IPs cannot fix that, which is why eight attempts still returned
zero. A real browser can: it solves the challenge. Playwright is already a
dependency and already used for WAF-ish pages — it just has to go out through
the SAME residential proxy, or it swaps a challenged residential IP for a
datacenter one and does worse.

It stays a last resort: a browser page costs seconds and hundreds of MB of
RAM against a ~1 s httpx fetch.
"""
from typing import Any

import httpx
import pytest

from app.models.property import ScrapingFilters
from app.services import apify
from app.services.apify import _is_cloudflare_challenge, _playwright_proxy


class TestRecognisingTheChallenge:
    def test_the_real_challenge_body(self):
        body = ('<!DOCTYPE html><html lang="en-US"><head>'
                '<title>Just a moment...</title></head></html>')
        assert _is_cloudflare_challenge(body)

    def test_a_real_listing_is_not_a_challenge(self):
        assert not _is_cloudflare_challenge(
            '<html><script>window.__PRELOADED_STATE__ = {}</script></html>')

    def test_an_empty_body_is_not(self):
        assert not _is_cloudflare_challenge('')


class TestTheBrowserUsesTheSameProxy:
    def test_the_url_is_split_into_playwright_fields(self):
        got = _playwright_proxy(
            'http://groups-RESIDENTIAL,country-AR:s3cr3t@proxy.apify.com:8000')
        assert got == {
            'server': 'http://proxy.apify.com:8000',
            'username': 'groups-RESIDENTIAL,country-AR',
            'password': 's3cr3t',
        }

    def test_no_proxy_means_no_proxy(self):
        assert _playwright_proxy('') is None
        assert _playwright_proxy(None) is None

    def test_a_proxy_without_credentials_still_works(self):
        assert _playwright_proxy('http://proxy.local:3128') == {
            'server': 'http://proxy.local:3128'}


def _filters() -> ScrapingFilters:
    return ScrapingFilters(zona='La Plata', zona_pedida='La Plata',
                           tipo_operacion='venta', tipos_propiedad=['departamento'])


async def _noop(*_a: Any) -> None:
    return None


def _state_page() -> str:
    import json
    st = {'listStore': {
        'paging': {'total': 1, 'totalPages': 1, 'currentPage': 1},
        'appliedFilters': [{'type': 'location', 'options': [
            {'label': 'La Plata', 'type': 'city', 'min': '1001361'}]}],
        'listPostings': [{
            'postingId': '1', 'url': '/p/1.html', 'title': 'D',
            'realEstateType': {'name': 'Departamentos'},
            'priceOperationTypes': [{'prices': [{'amount': 95000, 'currency': 'USD'}]}],
            'postingLocation': {'address': {'name': 'C 1'},
                                'location': {'locationId': 'V1-C-1001361', 'name': 'La Plata'}},
            'mainFeatures': {}, 'visiblePictures': {'pictures': []},
        }],
    }}
    return ('<html><script>window.__PRELOADED_STATE__ = '
            + json.dumps(st) + ';window.x={};</script></html>')


def _all_challenged(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None: pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a: Any): return None
        async def get(self, url: str, *a: Any, **k: Any) -> httpx.Response:
            return httpx.Response(403, text='<title>Just a moment...</title>',
                                  request=httpx.Request('GET', url))

    monkeypatch.setattr(httpx, 'AsyncClient', _Client)


class TestTheFallbackFires:
    async def test_the_browser_rescues_a_challenged_page(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _all_challenged(monkeypatch)
        calls: list[str] = []

        async def fake_render(url: str, **kw: Any) -> str:
            calls.append(url)
            return _state_page()

        monkeypatch.setattr(apify, 'render_page_html', fake_render)

        results = await apify._scrape_zonaprop_direct(_filters(), _noop)

        assert calls, 'el navegador tiene que intentarlo cuando httpx no pasa'
        assert len(results) == 1

    async def test_it_is_a_last_resort_not_a_first_choice(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A browser page costs seconds; httpx costs one. Never launch it for
        a page plain HTTP already served."""
        class _Client:
            def __init__(self, *a: Any, **k: Any) -> None: pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a: Any): return None
            async def get(self, url: str, *a: Any, **k: Any) -> httpx.Response:
                return httpx.Response(200, text=_state_page(),
                                      request=httpx.Request('GET', url))

        monkeypatch.setattr(httpx, 'AsyncClient', _Client)
        calls: list[str] = []

        async def fake_render(url: str, **kw: Any) -> str | None:
            calls.append(url)
            return None

        monkeypatch.setattr(apify, 'render_page_html', fake_render)

        await apify._scrape_zonaprop_direct(_filters(), _noop)

        assert calls == []

    async def test_a_browser_that_also_fails_is_not_fatal(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _all_challenged(monkeypatch)

        async def fake_render(url: str, **kw: Any) -> None:
            return None

        monkeypatch.setattr(apify, 'render_page_html', fake_render)

        assert await apify._scrape_zonaprop_direct(_filters(), _noop) == []
