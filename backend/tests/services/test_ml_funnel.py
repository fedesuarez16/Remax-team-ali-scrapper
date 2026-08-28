"""MercadoLibre has to say what it did, not only when it breaks.

It logged on failure only, so a successful-but-thin run said nothing at all
and "trae muy pocos resultados" could not be attributed: too few pages walked,
the zona guard discarding, the price filter too narrow, or the portal simply
having little. Those need opposite fixes and the counts separate them — the
same reasoning that made ZonaProp's funnel worth having.
"""
from typing import Any

import httpx
import pytest

from app.models.property import ScrapingFilters
from app.services.apify import _scrape_mercadolibre

_BASE = 'https://inmuebles.mercadolibre.com.ar/casas/venta/la-plata'


def _card(href: str, *, location: str = 'C. 56 720, La Plata, Buenos Aires') -> str:
    return f'''
    <li class="ui-search-layout__item"><div class="poly-card">
      <a class="poly-component__title" href="{href}">Depto</a>
      <span class="poly-component__headline">Casa en venta</span>
      <span class="andes-money-amount__currency-symbol">US$</span>
      <span class="andes-money-amount__fraction">120.000</span>
      <span class="poly-component__location">{location}</span>
      <ul class="poly-attributes_list"><li class="poly-attributes_list__item">3 ambs.</li></ul>
    </div></li>'''


def _page(*cards: str) -> str:
    return f'<html><body><ol class="ui-search-layout">{"".join(cards)}</ol></body></html>'


def _filters() -> ScrapingFilters:
    return ScrapingFilters(zona='La Plata', zona_pedida='La Plata',
                           tipo_operacion='venta', tipos_propiedad=['casa'],
                           precio_min=99_000, precio_max=150_000)


async def _noop(source: str, status: str, count: int) -> None:
    return None


def _serve(monkeypatch: pytest.MonkeyPatch, pages: dict[str, str]) -> None:
    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None: pass
        async def __aenter__(self) -> '_Client': return self
        async def __aexit__(self, *a: Any) -> None: return None

        async def get(self, url: str, *a: Any, **k: Any) -> httpx.Response:
            return httpx.Response(200, text=pages.get(url, _page()),
                                  request=httpx.Request('GET', url))

    monkeypatch.setattr(httpx, 'AsyncClient', _Client)


async def _run(monkeypatch: pytest.MonkeyPatch, pages: dict[str, str],
               caplog: pytest.LogCaptureFixture) -> str:
    _serve(monkeypatch, pages)
    with caplog.at_level('INFO', logger='app.services.apify'):
        await _scrape_mercadolibre(_filters(), _noop)
    return ' '.join(r.getMessage() for r in caplog.records)


async def test_a_successful_run_reports_its_numbers(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    url = f'{_BASE}/_PriceRange_99000USD-150000USD'
    blob = await _run(monkeypatch, {
        url: _page(_card('https://x/MLA-1'), _card('https://x/MLA-2')),
    }, caplog)

    assert 'mercadolibre funnel' in blob
    assert 'kept=2' in blob


async def test_it_names_what_the_zona_guard_dropped(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """The count alone cannot separate "the guard ate them" from "the portal
    had few"."""
    url = f'{_BASE}/_PriceRange_99000USD-150000USD'
    blob = await _run(monkeypatch, {
        url: _page(
            _card('https://x/MLA-1'),
            _card('https://x/MLA-2', location='Av. Colón 100, Córdoba, Córdoba'),
        ),
    }, caplog)

    assert 'fuera_de_zona=1' in blob
    assert 'kept=1' in blob


async def test_the_url_is_in_the_line(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Without it the numbers cannot be checked against the portal by hand —
    and the price filter is part of that URL."""
    url = f'{_BASE}/_PriceRange_99000USD-150000USD'
    blob = await _run(monkeypatch, {url: _page(_card('https://x/MLA-1'))}, caplog)

    assert '_PriceRange_99000USD-150000USD' in blob


async def test_an_empty_search_still_reports(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Zero is the number most worth explaining."""
    blob = await _run(monkeypatch, {}, caplog)

    assert 'mercadolibre funnel' in blob
    assert 'kept=0' in blob
