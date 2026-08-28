"""Two attempts is not enough from every network.

Local: the first request often draws a 403 and the second goes through — one
retry covers it. Production (Railway) exhausts BOTH and the search ends with
zero, on the same credential and the same `country-AR` residential proxy that
answers 200 twice in a row from a laptop. The variable is which exit IP the
pool hands out, so the fix is more chances at a good one.

Retrying a block is cheap: a 403 body is a few KB, against ~1.3 MB for a real
listing page. Retrying a 404 or a parse failure is not, and is not done.
"""
from typing import Any

import httpx
import pytest

from app.models.property import ScrapingFilters
from app.services.apify import _ZP_BLOCK_ATTEMPTS, _scrape_zonaprop_direct

_URL = 'https://www.zonaprop.com.ar/casas-venta-city-bell-450000-500000-dolar.html'


def _ok_page(n: int = 2) -> str:
    import json
    state = {'listStore': {
        'paging': {'total': n, 'totalPages': 1, 'currentPage': 1},
        'appliedFilters': [{'type': 'location', 'options': [
            {'label': 'City Bell', 'type': 'zone', 'min': '1001379'}]}],
        'listPostings': [{
            'postingId': str(i), 'url': f'/p/{i}.html', 'title': f'Casa {i}',
            'realEstateType': {'name': 'Casas'},
            'priceOperationTypes': [{'prices': [{'amount': 460000, 'currency': 'USD'}]}],
            'postingLocation': {'address': {'name': f'Calle {i}'},
                                'location': {'locationId': 'V1-D-1001379', 'name': 'City Bell'}},
            'mainFeatures': {}, 'visiblePictures': {'pictures': []},
        } for i in range(n)],
    }}
    return ('<html><script>window.__PRELOADED_STATE__ = '
            + json.dumps(state) + ';window.x={};</script></html>')


def _filters() -> ScrapingFilters:
    return ScrapingFilters(zona='City Bell', zona_pedida='City Bell',
                           tipo_operacion='venta', tipos_propiedad=['casa'],
                           precio_min=450_000, precio_max=500_000)


async def _noop(source: str, status: str, count: int) -> None:
    return None


def _serve(monkeypatch: pytest.MonkeyPatch, statuses: list[int]) -> list[str | None]:
    proxies: list[str | None] = []
    calls = {'n': 0}

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            proxies.append(k.get('proxy'))
        async def __aenter__(self) -> '_Client': return self
        async def __aexit__(self, *a: Any) -> None: return None

        async def get(self, url: str, *a: Any, **k: Any) -> httpx.Response:
            i = calls['n']
            calls['n'] += 1
            code = statuses[i] if i < len(statuses) else 200
            body = _ok_page() if code == 200 else '<html>denegado</html>'
            return httpx.Response(code, text=body, request=httpx.Request('GET', url))

    monkeypatch.setattr(httpx, 'AsyncClient', _Client)
    return proxies


@pytest.fixture(autouse=True)
def _proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    monkeypatch.setattr(
        settings, 'SCRAPER_PROXY_URL',
        'http://groups-RESIDENTIAL,country-AR:secret@proxy.apify.com:8000')
    monkeypatch.setattr('app.services.apify._ZP_BLOCK_BACKOFF', 0.0)


def test_more_than_two_attempts_are_allowed() -> None:
    """Two was the ceiling production kept hitting."""
    assert _ZP_BLOCK_ATTEMPTS > 2


async def test_it_survives_three_consecutive_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production's exact shape: block, block, block, then a good IP."""
    proxies = _serve(monkeypatch, [403, 403, 403, 200])

    results = await _scrape_zonaprop_direct(_filters(), _noop)

    assert len(results) == 2
    assert len(set(proxies)) == len(proxies), 'cada intento necesita su propia sesion'


async def test_it_still_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    """A wall is a wall — bounded, not infinite."""
    proxies = _serve(monkeypatch, [403] * 20)

    results = await _scrape_zonaprop_direct(_filters(), _noop)

    assert results == []
    assert len(proxies) == _ZP_BLOCK_ATTEMPTS


async def test_a_clean_first_try_still_costs_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxies = _serve(monkeypatch, [200])

    assert len(await _scrape_zonaprop_direct(_filters(), _noop)) == 2
    assert len(proxies) == 1


async def test_a_404_is_still_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown slug is an answer. Burning four attempts on it is waste."""
    proxies = _serve(monkeypatch, [404, 200])

    assert await _scrape_zonaprop_direct(_filters(), _noop) == []
    assert len(proxies) == 1
