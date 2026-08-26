"""`scrape_source('mercadolibre', ...)` walks listing HTML, not the dead API.

`_scrape_mercadolibre_api` hit `api.mercadolibre.com/sites/MLA/search`, which
answers 403 forbidden without OAuth for every query, and swallowed it in
`except Exception: break`. The result was a silent `0 props` on EVERY search —
indistinguishable from "nothing matched". These tests pin the replacement:
page through the public listing HTML with the parser, and stop cleanly.
"""
import logging

import httpx
import pytest

from app.core.config import settings
from app.models.property import ScrapingFilters
from app.services.apify import ApifyService, _ML_HTML_PAGE_SIZE


def _card(href: str, *, location='C. 56 720, La Plata, Buenos Aires') -> str:
    return f'''
    <li class="ui-search-layout__item"><div class="poly-card">
      <a class="poly-component__title" href="{href}">Depto</a>
      <span class="poly-component__headline">Departamento en venta</span>
      <span class="andes-money-amount__currency-symbol">US$</span>
      <span class="andes-money-amount__fraction">55.000</span>
      <span class="poly-component__location">{location}</span>
      <ul class="poly-attributes_list">
        <li class="poly-attributes_list__item">3 ambs.</li>
      </ul>
    </div></li>'''


def _page(*cards: str) -> str:
    return f'<html><body><ol class="ui-search-layout">{"".join(cards)}</ol></body></html>'


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


@pytest.fixture
def fetched(monkeypatch):
    """Records requested URLs; serves whatever `pages` maps them to."""
    state: dict = {'urls': [], 'pages': {}, 'default': _page()}

    class _FakeClient:
        def __init__(self, *a, **kw) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, *a, **kw):
            state['urls'].append(url)
            return _FakeResponse(state['pages'].get(url, state['default']))

    monkeypatch.setattr(httpx, 'AsyncClient', _FakeClient)
    return state


@pytest.fixture
def service() -> ApifyService:
    return ApifyService(api_token='unused')


async def _noop(src: str, status: str, count: int) -> None:
    return None


def _filters(**kw) -> ScrapingFilters:
    kw.setdefault('zona', 'La Plata')
    kw.setdefault('tipo_operacion', 'venta')
    kw.setdefault('tipos_propiedad', ['departamento'])
    return ScrapingFilters(**kw)


class TestUsesListingHtml:
    async def test_requests_the_listing_page_not_the_api(self, service, fetched):
        fetched['pages'] = {
            'https://inmuebles.mercadolibre.com.ar/departamentos/venta/la-plata':
                _page(_card('https://x.com/MLA-1')),
        }
        await service.scrape_source('mercadolibre', _filters(), _noop)

        assert fetched['urls']
        assert not any('api.mercadolibre.com' in u for u in fetched['urls'])
        assert fetched['urls'][0].startswith('https://inmuebles.mercadolibre.com.ar/')

    async def test_returns_parsed_properties(self, service, fetched):
        fetched['pages'] = {
            'https://inmuebles.mercadolibre.com.ar/departamentos/venta/la-plata':
                _page(_card('https://x.com/MLA-1'), _card('https://x.com/MLA-2')),
        }
        res = await service.scrape_source('mercadolibre', _filters(), _noop)

        assert [p.fuente for p in res] == ['mercadolibre', 'mercadolibre']


class TestPaging:
    async def test_follows_desde_pagination(self, service, fetched, monkeypatch):
        monkeypatch.setattr(settings, 'MERCADOLIBRE_MAX_PAGES', 0)
        base = 'https://inmuebles.mercadolibre.com.ar/departamentos/venta/la-plata'
        fetched['pages'] = {
            base: _page(_card('https://x.com/MLA-1')),
            f'{base}/_Desde_{_ML_HTML_PAGE_SIZE + 1}': _page(_card('https://x.com/MLA-2')),
        }
        res = await service.scrape_source('mercadolibre', _filters(), _noop)

        assert len(res) == 2
        assert f'/_Desde_{_ML_HTML_PAGE_SIZE + 1}' in fetched['urls'][1]

    async def test_stops_on_a_page_with_nothing_new(self, service, fetched, monkeypatch):
        """An out-of-range offset re-serves page 1; without this guard the
        loop would page forever on duplicates."""
        monkeypatch.setattr(settings, 'MERCADOLIBRE_MAX_PAGES', 0)
        fetched['default'] = _page(_card('https://x.com/MLA-1'))
        res = await service.scrape_source('mercadolibre', _filters(), _noop)

        assert len(res) == 1
        assert len(fetched['urls']) == 2  # page 1, then the duplicate that stops it

    async def test_stops_on_an_empty_page(self, service, fetched, monkeypatch):
        monkeypatch.setattr(settings, 'MERCADOLIBRE_MAX_PAGES', 0)
        res = await service.scrape_source('mercadolibre', _filters(), _noop)

        assert res == []
        assert len(fetched['urls']) == 1


class TestFailuresAreVisible:
    async def test_http_failure_does_not_raise(self, service, fetched, monkeypatch):
        """A dead source must not take the whole search down with it."""
        class _Boom:
            def __init__(self, *a, **kw) -> None: pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def get(self, *a, **kw): raise RuntimeError('403 forbidden')

        monkeypatch.setattr(httpx, 'AsyncClient', _Boom)
        assert await service.scrape_source('mercadolibre', _filters(), _noop) == []

    async def test_http_failure_is_logged(self, service, fetched, monkeypatch, caplog):
        """Not raising is only half the job — the failure has to be SAYABLE.

        This is the exact hole that cost us the portal: the REST scraper ate a
        403 in a bare `except Exception: break` and reported `done, 0`, so a
        broken source was indistinguishable from a zona with no listings. It
        stayed broken for weeks because nothing ever said so.
        """
        class _Boom:
            def __init__(self, *a, **kw) -> None: pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def get(self, *a, **kw): raise RuntimeError('403 forbidden')

        monkeypatch.setattr(httpx, 'AsyncClient', _Boom)
        with caplog.at_level(logging.WARNING):
            await service.scrape_source('mercadolibre', _filters(), _noop)

        assert any(
            r.levelno >= logging.WARNING and 'mercadolibre' in r.getMessage().lower()
            for r in caplog.records
        ), f'la falla no quedó registrada: {[r.getMessage() for r in caplog.records]}'

    async def test_the_offending_url_is_in_the_message(self, service, fetched, monkeypatch):
        """Un log que no dice QUÉ URL falló no sirve para diagnosticar."""
        class _Boom:
            def __init__(self, *a, **kw) -> None: pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def get(self, url, *a, **kw): raise RuntimeError('403 forbidden')

        monkeypatch.setattr(httpx, 'AsyncClient', _Boom)
        seen: list[str] = []
        monkeypatch.setattr(
            logging.getLogger('app.services.apify'), 'warning',
            lambda msg, *a, **kw: seen.append(msg % a if a else msg),
        )
        await service.scrape_source('mercadolibre', _filters(), _noop)

        assert seen, 'no se emitió ningún warning'
        assert any('inmuebles.mercadolibre.com.ar' in m for m in seen), seen
        assert any('403 forbidden' in m for m in seen), seen


class TestProgressReporting:
    async def test_reports_done_with_the_count(self, service, fetched):
        seen: list[tuple[str, int]] = []

        async def prog(src, status, count):
            seen.append((status, count))

        fetched['pages'] = {
            'https://inmuebles.mercadolibre.com.ar/departamentos/venta/la-plata':
                _page(_card('https://x.com/MLA-1')),
        }
        await service.scrape_source('mercadolibre', _filters(), prog)

        assert ('done', 1) in seen


class TestZonaCandidateChainStillApplies:
    async def test_falls_back_to_the_localidad(self, service, fetched, monkeypatch):
        """A barrio page with nothing usable must degrade, same as every other
        portal — this is what `scrape_source` adds on top.

        The SLUG degrades; the guard does not. `zona_pedida` pins it to
        "Casco Urbano, La Plata", so a card whose location reads only
        "C. 7, La Plata" is rejected even on the localidad page. The walk is
        still worth making: a card that DID name the barrio would be kept."""
        monkeypatch.setattr(settings, 'MERCADOLIBRE_MAX_PAGES', 1)
        fetched['pages'] = {
            'https://inmuebles.mercadolibre.com.ar/departamentos/venta/la-plata':
                _page(_card('https://x.com/MLA-1', location='C. 7, La Plata, Buenos Aires')),
        }
        res = await service.scrape_source(
            'mercadolibre', _filters(zona='Casco Urbano, La Plata'), _noop,
        )

        urls = fetched['urls']
        assert any('casco-urbano-la-plata' in u for u in urls)
        assert any(u.endswith('/la-plata') for u in urls)
        assert len(res) == 0
