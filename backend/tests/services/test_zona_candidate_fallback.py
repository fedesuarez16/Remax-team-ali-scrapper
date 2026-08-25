"""`scrape_source` walks the zona candidate chain until one yields results.

Resolving a barrio can fail three different ways, and only the third is
visible from inside a resolver:

  1. the portal's autocomplete has no match at all — Argenprop answers
     "Casco Urbano" with a barrio in SAN LUIS, which the composite-phrase
     check rejects, leaving no slug;
  2. it has a match the URL builder accepts but that is the wrong place —
     RE/MAX's only literal "Casco Urbano" is the gated community "Los
     Eucaliptus Casco Urbano", a plausible-looking level-3 location;
  3. the slug is fine and the listing page is simply empty.

Text inspection cannot tell (2) from a good hit, so the retry is driven by
the only honest signal there is — zero results — and lives at `scrape_source`,
the single entry point every portal already goes through.

This generalises the one-off retry ZonaProp had for composite localidad slugs,
which only ever ran on the map path.
"""
import pytest

from app.models.property import RawProperty, ScrapingFilters
from app.services import apify
from app.services.apify import ApifyService, ZonaPropFunnel


def _prop(zona: str) -> RawProperty:
    return RawProperty(fuente='mudafy', titulo=f'Depto en {zona}',
                       direccion=f'calle 47 1234, {zona}')


@pytest.fixture
def service() -> ApifyService:
    return ApifyService(api_token='test-token')


@pytest.fixture
def calls() -> list[str]:
    return []


async def _noop_progress(src: str, status: str, count: int) -> None:
    return None


def _stub(monkeypatch, name: str, calls: list[str], *, hits: set[str]):
    """Patch a module-level scraper to answer only for `hits` zonas."""
    async def _fake(filters, on_progress):
        calls.append(filters.zona or '')
        await on_progress('mudafy', 'done', 0)
        return [_prop(filters.zona)] if filters.zona in hits else []

    monkeypatch.setattr(apify, name, _fake)


class TestFallsBackToTheContainingLocalidad:
    async def test_retries_when_the_barrio_yields_nothing(
        self, service, calls, monkeypatch,
    ):
        _stub(monkeypatch, '_scrape_mudafy', calls, hits={'La Plata'})
        filters = ScrapingFilters(zona='Casco Urbano, La Plata')

        results = await service.scrape_source('mudafy', filters, _noop_progress)

        assert calls == ['Casco Urbano, La Plata', 'La Plata']
        assert len(results) == 1

    async def test_stops_at_the_first_candidate_that_works(
        self, service, calls, monkeypatch,
    ):
        """No wasted actor run — and no actor cost — when the barrio resolves."""
        _stub(monkeypatch, '_scrape_mudafy', calls,
              hits={'Casco Urbano, La Plata', 'La Plata'})
        filters = ScrapingFilters(zona='Casco Urbano, La Plata')

        results = await service.scrape_source('mudafy', filters, _noop_progress)

        assert calls == ['Casco Urbano, La Plata']
        assert len(results) == 1

    async def test_bare_zona_is_tried_exactly_once(
        self, service, calls, monkeypatch,
    ):
        _stub(monkeypatch, '_scrape_mudafy', calls, hits=set())
        filters = ScrapingFilters(zona='Palermo')

        results = await service.scrape_source('mudafy', filters, _noop_progress)

        assert calls == ['Palermo']
        assert results == []

    async def test_never_degrades_into_a_whole_province(
        self, service, calls, monkeypatch,
    ):
        _stub(monkeypatch, '_scrape_mudafy', calls, hits={'Buenos Aires'})
        filters = ScrapingFilters(zona='Los Hornos, Buenos Aires')

        results = await service.scrape_source('mudafy', filters, _noop_progress)

        assert calls == ['Los Hornos, Buenos Aires']
        assert results == []


class TestAppliesToEveryPortal:
    @pytest.mark.parametrize('source,fn', [
        ('remax', '_scrape_remax_api'),
        ('inmobusqueda', '_scrape_inmobusqueda'),
        ('mercadolibre', '_scrape_mercadolibre'),
    ])
    async def test_direct_http_portals_degrade(
        self, service, calls, monkeypatch, source, fn,
    ):
        _stub(monkeypatch, fn, calls, hits={'La Plata'})
        filters = ScrapingFilters(zona='Casco Urbano, La Plata')

        results = await service.scrape_source(source, filters, _noop_progress)

        assert calls == ['Casco Urbano, La Plata', 'La Plata']
        assert len(results) == 1

    async def test_argenprop_degrades(self, service, calls, monkeypatch):
        async def _fake(self_, filters, on_progress):
            calls.append(filters.zona or '')
            return [_prop(filters.zona)] if filters.zona == 'La Plata' else []

        monkeypatch.setattr(ApifyService, '_scrape_argenprop', _fake)
        filters = ScrapingFilters(zona='Casco Urbano, La Plata')

        results = await service.scrape_source('argenprop', filters, _noop_progress)

        assert calls == ['Casco Urbano, La Plata', 'La Plata']
        assert len(results) == 1

    async def test_zonaprop_degrades_on_the_chat_path(
        self, service, calls, monkeypatch,
    ):
        """ZonaProp's own retry only ever fired when `localidades` was set —
        the map path. A chat query never reached it.

        Three attempts now, and the middle one is the point: ZonaProp's real
        URL uses the BARE localidad (`.../departamentos-venta-city-bell-...`),
        so the composite slug is tried, then the plain barrio, and only then
        does the candidate chain widen to the partido.
        """
        async def _fake(self_, actor_id, filters):
            calls.append(filters.zona or '')
            props = [_prop(filters.zona)] if filters.zona == 'La Plata' else []
            return props, ZonaPropFunnel(search_url='https://www.zonaprop.com.ar/x.html')

        monkeypatch.setattr(ApifyService, '_scrape_zonaprop_paginated', _fake)
        filters = ScrapingFilters(zona='Casco Urbano, La Plata')

        results = await service.scrape_source('zonaprop', filters, _noop_progress)

        assert calls == ['Casco Urbano, La Plata', 'Casco Urbano', 'La Plata']
        assert len(results) == 1


class TestDegradesTheWholeFilterNotJustTheZona:
    async def test_localidades_degrade_alongside_zona(
        self, service, calls, monkeypatch,
    ):
        """Every portal resolver reads `localidades[0]` in preference to
        `zona`; leaving a stale barrio there would re-resolve the same dead
        slug on the retry."""
        seen: list[list[str]] = []

        async def _fake(filters, on_progress):
            seen.append(list(filters.localidades))
            calls.append(filters.zona or '')
            return [_prop(filters.zona)] if filters.zona == 'La Plata' else []

        monkeypatch.setattr(apify, '_scrape_mudafy', _fake)
        filters = ScrapingFilters(zona='Villa Elisa, La Plata',
                                  zonas=['Villa Elisa, La Plata'],
                                  localidades=['Villa Elisa, La Plata'])

        await service.scrape_source('mudafy', filters, _noop_progress)

        assert seen == [['Villa Elisa, La Plata'], ['La Plata']]
