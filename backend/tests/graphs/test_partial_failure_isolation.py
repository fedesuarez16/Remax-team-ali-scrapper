"""Un portal o una zona que falla no puede llevarse la búsqueda entera.

Una búsqueda toca N portales y hasta cientos de inmobiliarias, y cada una es
una red ajena que se cae, tira captcha o cambia el HTML sin avisar. Si un
eslabón puede tirar todo, la búsqueda nunca termina bien: siempre hay UNO roto.

Los portales ya estaban aislados — `run_portal_scraper` catchea por portal y el
grafo abre un `Send` por cada uno. Lo que NO estaba aislado eran las UNIDADES
dentro de una misma fuente: `source_filters` trae una unidad por zona pedida
(City Bell, Gonnet, ...) y el `for` las recorría sin protección, así que la zona
5 rota descartaba las cuatro que YA habían traído propiedades.

Perder trabajo ya hecho y pagado es lo peor que puede hacer este código: las
propiedades estaban en la mano y se tiraban.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.graphs.extraction import nodes
from app.models.property import RawProperty, ScrapingFilters


def _prop(n: int, fuente: str = 'urquiza') -> RawProperty:
    return RawProperty(
        fuente=fuente, tipo_operacion='venta', tipo_propiedad='casa',
        direccion=f'Calle {n}, City Bell', precio=100000 + n,
        url_origen=f'https://www.urquiza.com.ar/p/{n}',
    )


@pytest.fixture
def _quiet(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    eventos = AsyncMock()
    monkeypatch.setattr(nodes, 'adispatch_custom_event', eventos)
    return eventos


class TestUnitIsolation:
    async def test_one_failing_zone_keeps_the_zones_that_worked(
        self, monkeypatch: pytest.MonkeyPatch, _quiet: AsyncMock,
    ) -> None:
        """Dos zonas traen propiedades, la tercera explota: se devuelven las dos."""
        async def scrape(source: str, filters: ScrapingFilters, on_progress: object) -> list[RawProperty]:
            if filters.zona == 'Gonnet':
                raise RuntimeError('InmoBúsqueda: no se pudo leer el listado.')
            return [_prop(1 if filters.zona == 'City Bell' else 2)]

        service = AsyncMock()
        service.scrape_source = scrape
        monkeypatch.setattr(nodes, 'get_apify_service', lambda: service)

        out = await nodes.run_website_scraper({
            'nombre': 'Urquiza', 'url': 'https://www.urquiza.com.ar/',
            'source_filters': [
                ScrapingFilters(zona='City Bell'),
                ScrapingFilters(zona='Gonnet'),
                ScrapingFilters(zona='Villa Elisa'),
            ],
        }, {'configurable': {'supabase': None}})

        encontradas = {p.url_origen for p in out['registered_properties']}
        assert encontradas == {
            'https://www.urquiza.com.ar/p/1', 'https://www.urquiza.com.ar/p/2',
        }

    async def test_the_failing_zone_is_reported_not_swallowed(
        self, monkeypatch: pytest.MonkeyPatch, _quiet: AsyncMock,
    ) -> None:
        """Seguir NO es tapar: la zona caída tiene que quedar registrada.

        Si no, una búsqueda que perdió media zona se ve idéntica a una completa
        y nadie sabe que hay que reintentarla.
        """
        async def scrape(source: str, filters: ScrapingFilters, on_progress: object) -> list[RawProperty]:
            if filters.zona == 'Gonnet':
                raise RuntimeError('captcha')
            return [_prop(1)]

        service = AsyncMock()
        service.scrape_source = scrape
        monkeypatch.setattr(nodes, 'get_apify_service', lambda: service)

        out = await nodes.run_website_scraper({
            'nombre': 'Urquiza', 'url': 'https://www.urquiza.com.ar/',
            'source_filters': [
                ScrapingFilters(zona='City Bell'), ScrapingFilters(zona='Gonnet'),
            ],
        }, {'configurable': {'supabase': None}})

        assert out['registered_properties'], 'lo que salió bien se conserva'
        assert any('Gonnet' in e or 'captcha' in e for e in out.get('errors', []))

    async def test_every_zone_failing_still_returns_a_usable_state(
        self, monkeypatch: pytest.MonkeyPatch, _quiet: AsyncMock,
    ) -> None:
        """Fuente entera caída: se reporta, pero el grafo no se cae con ella."""
        service = AsyncMock()
        service.scrape_source = AsyncMock(side_effect=RuntimeError('sitio caído'))
        monkeypatch.setattr(nodes, 'get_apify_service', lambda: service)

        out = await nodes.run_website_scraper({
            'nombre': 'Urquiza', 'url': 'https://www.urquiza.com.ar/',
            'source_filters': [
                ScrapingFilters(zona='City Bell'), ScrapingFilters(zona='Gonnet'),
            ],
        }, {'configurable': {'supabase': None}})

        assert out.get('registered_properties', []) == []
        assert out.get('errors'), 'una fuente entera caída no puede salir en silencio'

    async def test_duplicates_across_zones_are_still_collapsed(
        self, monkeypatch: pytest.MonkeyPatch, _quiet: AsyncMock,
    ) -> None:
        """El dedupe por `url_origen` no se pierde al aislar las unidades.

        Dos zonas vecinas devuelven el mismo aviso y no puede entrar dos veces.
        """
        service = AsyncMock()
        service.scrape_source = AsyncMock(return_value=[_prop(7)])
        monkeypatch.setattr(nodes, 'get_apify_service', lambda: service)

        out = await nodes.run_website_scraper({
            'nombre': 'Urquiza', 'url': 'https://www.urquiza.com.ar/',
            'source_filters': [
                ScrapingFilters(zona='City Bell'), ScrapingFilters(zona='Gonnet'),
            ],
        }, {'configurable': {'supabase': None}})

        assert len(out['registered_properties']) == 1
