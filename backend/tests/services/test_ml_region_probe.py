"""Pick the region by asking the portal, and pay for it only once.

Each candidate is fetched with the REAL search URL — price filter included —
so the winner's response IS page 1 and nothing is downloaded twice. The result
is cached: probing five regions costs five ~2 MB pages through a metered
residential proxy, and that is a price worth paying once per zona, not once
per search.
"""
from typing import Any

import httpx
import pytest

from app.models.property import ScrapingFilters
from app.services import apify
from app.services.apify import _ml_search_urls


def _page(n: int, *, total: int | None = None, tag: str = 'x') -> str:
    counter = (f'<span class="ui-search-search-result__quantity-results">'
               f'{total if total is not None else n} resultados</span>')
    cards = ''.join(f'''
      <li class="ui-search-layout__item"><div class="poly-card">
        <a class="poly-component__title" href="https://m/MLA-{tag}{i}">C</a>
        <span class="poly-component__headline">Casa en venta</span>
        <span class="andes-money-amount__currency-symbol">US$</span>
        <span class="andes-money-amount__fraction">120.000</span>
        <span class="poly-component__location">C. 7 500, La Plata, Buenos Aires</span>
      </div></li>''' for i in range(n))
    return f'<html>{counter}<ol class="ui-search-layout">{cards}</ol></html>'


def _filters() -> ScrapingFilters:
    return ScrapingFilters(zona='La Plata', zona_pedida='La Plata',
                           tipo_operacion='venta', tipos_propiedad=['casa'],
                           precio_min=99_000, precio_max=150_000)


async def _noop(source: str, status: str, count: int) -> None:
    return None


def _serve(monkeypatch: pytest.MonkeyPatch, by_region: dict[str, str]) -> list[str]:
    asked: list[str] = []

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None: pass
        async def __aenter__(self) -> '_Client': return self
        async def __aexit__(self, *a: Any) -> None: return None

        async def get(self, url: str, *a: Any, **k: Any) -> httpx.Response:
            asked.append(url)
            for region, body in by_region.items():
                if f'/{region}/' in url:
                    return httpx.Response(200, text=body,
                                          request=httpx.Request('GET', url))
            return httpx.Response(200, text=_page(0),
                                  request=httpx.Request('GET', url))

    monkeypatch.setattr(httpx, 'AsyncClient', _Client)
    return asked


async def test_the_region_with_the_most_listings_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The measured case: buenos-aires-interior has 58, bsas-gba-sur has 455."""
    _serve(monkeypatch, {
        'buenos-aires-interior': _page(5, total=58, tag='interior'),
        'bsas-gba-sur': _page(5, total=455, tag='sur'),
    })

    results = await apify._scrape_mercadolibre(_filters(), _noop)

    assert results
    assert all('MLA-sur' in (p.url_origen or '') for p in results)


async def test_the_search_url_then_uses_that_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(monkeypatch, {'bsas-gba-sur': _page(5, total=455)})

    await apify._scrape_mercadolibre(_filters(), _noop)
    urls = list(_ml_search_urls(_filters(), max_pages=2))

    assert all('/bsas-gba-sur/' in u for u in urls)
    assert urls[1].endswith('_Desde_49_PriceRange_99000USD-150000USD_NoIndex_True')


async def test_a_second_search_does_not_probe_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = _serve(monkeypatch, {'bsas-gba-sur': _page(2, total=455)})

    await apify._scrape_mercadolibre(_filters(), _noop)
    probes = sum(1 for u in asked if '_Desde_' not in u)
    asked.clear()
    await apify._scrape_mercadolibre(_filters(), _noop)

    assert probes > 1, 'la primera busqueda tiene que sondear las regiones'
    assert sum(1 for u in asked if '_Desde_' not in u) == 1, 'la segunda, no'


async def test_a_zona_no_region_knows_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every probe empty: keep the flat slug rather than invent a region."""
    _serve(monkeypatch, {})

    await apify._scrape_mercadolibre(_filters(), _noop)
    urls = list(_ml_search_urls(_filters(), max_pages=1))

    assert urls[0].endswith('/la-plata/_PriceRange_99000USD-150000USD')
