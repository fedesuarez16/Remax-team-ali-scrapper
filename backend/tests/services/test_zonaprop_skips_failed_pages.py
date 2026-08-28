"""One blocked page must not cost the rest of the listing.

Production, `departamentos-venta-la-plata-la-plata` (`pagina=1/165`):

    p1: 403,403,403 → ok    p2..p5: ok    p6: 403,403,403 → ok
    p7: 403,403,403,403 → gave up → BREAK

Six pages of 165 survived. On another search page 2 blocked and 66 pages went
with it. ZonaProp's WAF blocks a page, not a listing — the very next page
usually goes through, as p6 shows.

So a failed page is SKIPPED, not fatal. A real wall still stops the crawl:
consecutive failures are counted and enough of them ends it.
"""
from typing import Any

import httpx
import pytest

from app.models.property import ScrapingFilters
from app.services.apify import _ZP_MAX_PAGE_FAILURES, _scrape_zonaprop_direct

_BASE = 'https://www.zonaprop.com.ar/departamentos-venta-la-plata-la-plata'


def _state(total_pages: int, n: int, start: int) -> str:
    import json
    st = {'listStore': {
        'paging': {'total': 30 * total_pages, 'totalPages': total_pages, 'currentPage': 1},
        'appliedFilters': [{'type': 'location', 'options': [
            {'label': 'La Plata', 'type': 'city', 'min': '1001361'}]}],
        'listPostings': [{
            'postingId': str(start + i), 'url': f'/p/{start + i}.html',
            'title': 'Depto', 'realEstateType': {'name': 'Departamentos'},
            'priceOperationTypes': [{'prices': [{'amount': 70000, 'currency': 'USD'}]}],
            'postingLocation': {'address': {'name': f'Calle {start + i}'},
                                'location': {'locationId': 'V1-C-1001361', 'name': 'La Plata'}},
            'mainFeatures': {}, 'visiblePictures': {'pictures': []},
        } for i in range(n)],
    }}
    return ('<html><script>window.__PRELOADED_STATE__ = '
            + json.dumps(st) + ';window.x={};</script></html>')


def _filters() -> ScrapingFilters:
    return ScrapingFilters(zona='La Plata, La Plata', zona_pedida='La Plata, La Plata',
                           tipo_operacion='venta', tipos_propiedad=['departamento'])


async def _noop(source: str, status: str, count: int) -> None:
    return None


def _serve(monkeypatch: pytest.MonkeyPatch, blocked: set[int], total_pages: int = 5) -> list[int]:
    """Pages in `blocked` always 403; the rest serve 30 listings."""
    asked: list[int] = []

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None: pass
        async def __aenter__(self) -> '_Client': return self
        async def __aexit__(self, *a: Any) -> None: return None

        async def get(self, url: str, *a: Any, **k: Any) -> httpx.Response:
            n = int(url.split('-pagina-')[1].split('.')[0]) if '-pagina-' in url else 1
            asked.append(n)
            if n in blocked:
                return httpx.Response(403, text='no', request=httpx.Request('GET', url))
            return httpx.Response(200, text=_state(total_pages, 30, n * 1000),
                                  request=httpx.Request('GET', url))

    monkeypatch.setattr(httpx, 'AsyncClient', _Client)
    monkeypatch.setattr('app.services.apify._ZP_BLOCK_BACKOFF', 0.0)
    return asked


async def test_a_blocked_page_does_not_end_the_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE production case: page 2 blocked, pages 3-5 still fetched."""
    asked = _serve(monkeypatch, blocked={2}, total_pages=5)

    results = await _scrape_zonaprop_direct(_filters(), _noop)

    assert sorted(set(asked)) == [1, 2, 3, 4, 5]
    assert len(results) == 4 * 30


async def test_scattered_blocks_are_all_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = _serve(monkeypatch, blocked={2, 4}, total_pages=5)

    results = await _scrape_zonaprop_direct(_filters(), _noop)

    assert 5 in asked
    assert len(results) == 3 * 30


async def test_a_real_wall_still_stops_the_crawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Everything from page 2 on is blocked — that is a wall, not a hiccup,
    and paying for all 165 pages of it would be waste."""
    asked = _serve(monkeypatch, blocked=set(range(2, 200)), total_pages=165)

    results = await _scrape_zonaprop_direct(_filters(), _noop)

    assert len(results) == 30
    attempted = len(set(asked))
    assert attempted <= 1 + _ZP_MAX_PAGE_FAILURES


async def test_a_success_resets_the_failure_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Consecutive is what counts: p2 and p4 blocked with p3 fine in between
    must not add up to a wall."""
    asked = _serve(monkeypatch, blocked={2, 4}, total_pages=6)

    await _scrape_zonaprop_direct(_filters(), _noop)

    assert 6 in asked
