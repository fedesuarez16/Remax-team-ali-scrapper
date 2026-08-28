"""Suite-wide guards.

`no_real_network` exists because of a concrete near-miss: ZonaProp moved from
the Apify actor to direct HTTP, and every test that had patched
`_scrape_zonaprop_paginated` silently started making REAL requests to
zonaprop.com.ar through the production residential proxy. The suite did not
fail — it hung, and it was spending billed bandwidth while it did.

A unit test must never reach the network. Patch the client, or use a fixture.
"""
import httpx
import pytest


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly on any outbound request that escapes the test's fakes.

    Hooks the TRANSPORT, not the client, so the many tests that legitimately
    build an `httpx.AsyncClient` and stub `.get`/`.post` are untouched — only
    traffic that would actually leave the machine is stopped.
    """
    async def _blocked(self: object, request: httpx.Request) -> httpx.Response:
        raise RuntimeError(
            f'Un test intentó salir a la red: {request.method} {request.url}\n'
            'Los tests no hacen requests reales — parcheá el cliente o usá un fixture.'
        )

    def _blocked_sync(self: object, request: httpx.Request) -> httpx.Response:
        raise RuntimeError(
            f'Un test intentó salir a la red: {request.method} {request.url}\n'
            'Los tests no hacen requests reales — parcheá el cliente o usá un fixture.'
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', _blocked)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', _blocked_sync)


@pytest.fixture()
def apify_zonaprop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route ZonaProp back through the Apify actor.

    For the tests that are ABOUT the actor path — pagination by URL, the
    funnel, the degraded-page retry. Production defaults to direct scraping
    (`ZONAPROP_USE_APIFY=False`); see docs/zonaprop-scraping.md.
    """
    from app.core.config import settings
    monkeypatch.setattr(settings, 'ZONAPROP_USE_APIFY', True)
