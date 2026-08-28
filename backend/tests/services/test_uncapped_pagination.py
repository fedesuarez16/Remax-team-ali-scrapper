"""Every portal scraper pages to exhaustion by default — `0` means "no cap".

The searches were topping out at exactly 100 RE/MAX items because the cap was
a *configured default* (`REMAX_MAX_PAGES=5` × `REMAX_PAGE_SIZE=20`), not a
portal constraint: the public API serves 3329 pages (66k listings) and accepts
`pageSize=200` and deep pages, verified live. The same shape of self-imposed
ceiling existed on every other source (Mudafy 4×25, InmoBusqueda 5×15,
MercadoLibre a hard-coded `_ML_MAX_PAGES=5`, ZonaProp a 20-page ceiling).

These tests pin the contract: the shipped defaults impose no SELF-IMPOSED
ceiling, and paging stops only on real exhaustion (empty page, `totalPages`, a
short page, or a page with nothing new).

Two portals are deliberate exceptions, and neither is a leftover default:

* Argenprop's cap is a robots.txt boundary (`Allow`s `pagina-1`..`pagina-10`),
  not a knob.
* ZonaProp is capped at 800 items (~27 pages) because it is the one source
  billed PER PAGE — a separate Apify actor run with a browser cold start each
  time. Uncapped, a single City Bell search was still paginating 21 minutes
  in with the UI showing only a spinner. Cost and wall-clock, not exhaustion.
"""
from typing import Any

import httpx
import pytest

from app.core.config import Settings, settings
from app.models.property import ScrapingFilters
from app.services import apify
from app.services.apify import (

    ApifyService,
    _argenprop_search_urls,
    _inmobusqueda_search_urls,
    _mudafy_search_urls,
    _scrape_remax_api,
)

# These exercise the Apify actor path, kept as the documented fallback
# (`ZONAPROP_USE_APIFY=true`). Production reads ZonaProp directly.
pytestmark = pytest.mark.usefixtures('apify_zonaprop')


async def _noop_progress(_src: str, _status: str, _count: int) -> None:
    return None


# ── shipped defaults ──────────────────────────────────────────────────────────

def test_shipped_defaults_impose_no_page_ceiling() -> None:
    """Lo que SE ENVÍA, no lo que esta máquina tiene configurado.

    Se leen los defaults declarados en la clase y no `settings.X`, porque
    `settings` ya viene resuelto contra el `.env` y el entorno: capear un portal
    en local o en Railway (p. ej. `MERCADOLIBRE_MAX_PAGES=5` para acotar el
    ancho de banda del proxy residencial) es una decisión de despliegue
    legítima, y ponía en rojo un test que habla del contrato del código.
    """
    defaults = {n: f.default for n, f in Settings.model_fields.items()}
    assert defaults['REMAX_MAX_PAGES'] == 0
    assert defaults['MERCADOLIBRE_MAX_PAGES'] == 0
    assert defaults['INMOBUSQUEDA_MAX_PAGES'] == 0
    assert defaults['MUDAFY_MAX_PAGES'] == 0
    assert defaults['ARGENPROP_MAX_PAGES'] == 0


def test_zonaprop_ships_capped_because_it_bills_per_page() -> None:
    """The one source where paging to exhaustion spends real money per page
    and per minute — see this module's docstring."""
    defaults = {n: f.default for n, f in Settings.model_fields.items()}
    assert defaults['ZONAPROP_MAX_RESULTS'] == 800


def test_remax_page_size_uses_the_api_maximum() -> None:
    # 200 is the largest `pageSize` the RE/MAX API honours (verified live:
    # page 0 at pageSize=200 returns 200 items). Bigger pages mean 10× fewer
    # round-trips for the same uncapped sweep.
    assert settings.REMAX_PAGE_SIZE == 200


# ── Argenprop: robots.txt ceiling, not a knob ─────────────────────────────────

def _argenprop_filters() -> ScrapingFilters:
    return ScrapingFilters(zona='Palermo', tipo_operacion='venta', tipos_propiedad=['departamento'])


def test_argenprop_zero_means_the_robots_txt_hard_cap() -> None:
    urls = _argenprop_search_urls(_argenprop_filters(), max_pages=0)
    assert len(urls) == 10


# ── InmoBusqueda / Mudafy: URL builders must be unbounded on 0 ────────────────

def _zona_filters() -> ScrapingFilters:
    return ScrapingFilters(zona='Villa Elisa', tipo_operacion='venta')


def test_inmobusqueda_zero_yields_pages_lazily_without_end() -> None:
    from itertools import islice
    urls = list(islice(_inmobusqueda_search_urls(_zona_filters(), 0, 'villa-elisa'), 40))
    assert len(urls) == 40
    assert urls[0].endswith('/propiedades-villa-elisa.html')
    assert urls[39].endswith('-pagina-40.html')


def test_mudafy_zero_yields_pages_lazily_without_end() -> None:
    from itertools import islice
    urls = list(islice(_mudafy_search_urls(_zona_filters(), 0), 40))
    assert len(urls) == 40
    assert urls[39].endswith('/40-p')


def test_explicit_page_caps_still_truncate() -> None:
    assert len(list(_inmobusqueda_search_urls(_zona_filters(), 3, 'villa-elisa'))) == 3
    assert len(list(_mudafy_search_urls(_zona_filters(), 3))) == 3


# ── RE/MAX: uncapped when the zona resolved; bounded sweep when it did not ───

_REMAX_TOTAL_PAGES = 40


@pytest.fixture()
def _mock_remax_api(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {'requests': []}

    def _item(id_: int) -> dict[str, Any]:
        return {
            'id': id_, 'operation': {'id': 1, 'value': 'sale'},
            'currency': {'id': 1, 'value': 'USD'},
            'type': {'id': 2, 'value': 'departamento_estandar'},
            'title': f'Depto {id_}', 'slug': f'depto-{id_}',
            'totalRooms': 3, 'bathrooms': 1, 'bedrooms': 2,
            'price': 100_000 + id_, 'displayAddress': f'Calle Falsa {id_}',
            'geoLabel': 'Palermo, Capital Federal',
            'dimensionTotalBuilt': 55.0, 'dimensionCovered': 50.0,
        }

    class _FakeResponse:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self._payload

    class _FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> '_FakeAsyncClient':
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get(self, url: str, params: dict[str, Any] | None = None, **kw: Any):
            params = params or {}
            captured['requests'].append(params)
            page = int(params.get('page', 0))
            size = int(params.get('pageSize', 20))
            items = [] if page >= _REMAX_TOTAL_PAGES else [
                _item(page * size + i) for i in range(size)
            ]
            return _FakeResponse({
                'data': {
                    'data': items, 'page': page, 'totalPages': _REMAX_TOTAL_PAGES,
                    'totalItems': _REMAX_TOTAL_PAGES * size,
                },
                'code': 200, 'message': '', 'errors': None,
            })

    monkeypatch.setattr(httpx, 'AsyncClient', _FakeAsyncClient)
    return captured


def _remax_filters() -> ScrapingFilters:
    return ScrapingFilters(zona='Palermo', tipo_operacion='venta')


async def test_remax_resolved_zona_pages_to_exhaustion(
    _mock_remax_api: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A resolved `locations` filter bounds the result set server-side to the
    # zona, so "no cap" is safe: page until `totalPages`.
    async def _resolved(_zona: str) -> str:
        return 'in::::1067:::'

    monkeypatch.setattr(apify, '_remax_resolve_location', _resolved)
    monkeypatch.setattr(settings, 'REMAX_MAX_PAGES', 0)
    await _scrape_remax_api(_remax_filters(), _noop_progress)
    assert len(_mock_remax_api['requests']) == _REMAX_TOTAL_PAGES
    assert _mock_remax_api['requests'][0]['locations'] == 'in::::1067:::'


async def test_remax_unresolved_zona_uses_the_bounded_sweep(
    _mock_remax_api: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With no `locations` filter the API serves the newest listings NATIONWIDE
    # (66k+ of them) and the zona is matched client-side on text. Sweeping all
    # of that costs hundreds of round-trips to keep a handful of rows, so the
    # unlocated fallback keeps its own ceiling — the zona search itself is what
    # must be uncapped, not a nationwide crawl.
    async def _none(_zona: str) -> None:
        return None

    monkeypatch.setattr(apify, '_remax_resolve_location', _none)
    monkeypatch.setattr(settings, 'REMAX_MAX_PAGES', 0)
    monkeypatch.setattr(settings, 'REMAX_UNLOCATED_MAX_PAGES', 6)
    await _scrape_remax_api(_remax_filters(), _noop_progress)
    assert len(_mock_remax_api['requests']) == 6


async def test_remax_explicit_cap_still_wins_over_the_sweep(
    _mock_remax_api: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _none(_zona: str) -> None:
        return None

    monkeypatch.setattr(apify, '_remax_resolve_location', _none)
    monkeypatch.setattr(settings, 'REMAX_MAX_PAGES', 2)
    monkeypatch.setattr(settings, 'REMAX_UNLOCATED_MAX_PAGES', 6)
    await _scrape_remax_api(_remax_filters(), _noop_progress)
    assert len(_mock_remax_api['requests']) == 2


# ── ZonaProp: no synthetic 20-page ceiling when uncapped ──────────────────────

_ZP_PAGE_SIZE = 30


def _zp_item(i: int) -> dict[str, Any]:
    return {
        'title': f'Casa {i} en Villa Elisa',
        'url': f'https://www.zonaprop.com.ar/propiedades/clasificado/x-{i}.html',
        'listingId': str(i), 'neighborhood': 'Villa Elisa', 'city': 'La Plata',
        'address': f'Calle {i}', 'listingType': 'sale', 'propertyType': 'house',
        'price': 100000 + i, 'currency': 'USD',
    }


async def test_zonaprop_uncapped_pages_past_the_old_twenty_page_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, 'ZONAPROP_MAX_RESULTS', 0)
    service = ApifyService(api_token='dummy-token')
    calls: list[dict[str, Any]] = []
    last_page = 26

    async def fake_run(src: str, actor: str, input_data: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append(input_data)
        if len(calls) >= last_page:
            return [_zp_item(len(calls) * 1000 + i) for i in range(11)]  # short = last
        return [_zp_item(len(calls) * 1000 + i) for i in range(_ZP_PAGE_SIZE)]

    monkeypatch.setattr(service, '_run_actor', fake_run)
    filters = ScrapingFilters(zona='Villa Elisa, La Plata', localidades=['Villa Elisa, La Plata'])
    results = await service.scrape_source('zonaprop', filters, _noop_progress)

    assert len(calls) == last_page
    assert len(results) == (last_page - 1) * _ZP_PAGE_SIZE + 11
