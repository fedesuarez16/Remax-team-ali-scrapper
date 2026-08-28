"""The composite-slug retry must fire on the chat path too.

ZonaProp's own URL for City Bell is `.../departamentos-venta-city-bell-...`
— the bare localidad, no partido. But a query parsed as "City Bell, La Plata"
slugifies to `city-bell-la-plata`, which the portal does not know, so it
redirects to a nationwide listing.

`_scrape_source_once` already retries with the plain first part for exactly
this reason — but only `if filters.localidades`, i.e. the map path. A typed
query leaves `localidades` empty and never reached it. The failure mode is
identical on both paths, so the gate belongs on "is the slug composite", not
on which path built it.
"""
from typing import Any

import pytest

from app.models.property import RawProperty, ScrapingFilters
from app.services.apify import ApifyService, ZonaPropFunnel

# These exercise the Apify actor path, kept as the documented fallback
# (`ZONAPROP_USE_APIFY=true`). Production reads ZonaProp directly.
pytestmark = pytest.mark.usefixtures('apify_zonaprop')



@pytest.fixture()
def service() -> ApifyService:
    return ApifyService(api_token='dummy-token')


async def _noop_progress(source: str, status: str, count: int) -> None:
    return None


def _prop(zona: str) -> RawProperty:
    return RawProperty(
        fuente='zonaprop', titulo=f'Depto en {zona}', direccion=f'Calle 13, {zona}',
        precio=250000.0, moneda='USD', tipo_operacion='venta',
        tipo_propiedad='departamento',
    )


def _spy(service: ApifyService, monkeypatch: pytest.MonkeyPatch,
         hits: set[str]) -> list[str]:
    """Records the `zona` of every pagination attempt; returns props only for
    the zonas in `hits`, mimicking a slug the portal actually knows."""
    seen: list[str] = []

    async def fake(actor_id: str, filters: ScrapingFilters) -> Any:
        zona = filters.zona or ''
        seen.append(zona)
        props = [_prop(zona)] if zona in hits else []
        return props, ZonaPropFunnel(search_url=f'https://z/{zona}')

    monkeypatch.setattr(service, '_scrape_zonaprop_paginated', fake)
    return seen


async def test_chat_path_retries_with_the_plain_localidad(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _spy(service, monkeypatch, hits={'City Bell'})
    filters = ScrapingFilters(zona='City Bell, La Plata', tipos_propiedad=['departamento'])

    results = await service._scrape_source_once('zonaprop', filters, _noop_progress)

    assert seen == ['City Bell, La Plata', 'City Bell']
    assert len(results) == 1


async def test_the_retry_does_not_degrade_to_the_partido(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"City Bell, La Plata" must retry as "City Bell", never as "La Plata" —
    the whole point is to keep the search in the barrio the user asked for."""
    seen = _spy(service, monkeypatch, hits=set())
    filters = ScrapingFilters(zona='City Bell, La Plata', tipos_propiedad=['departamento'])

    await service._scrape_source_once('zonaprop', filters, _noop_progress)

    assert 'La Plata' not in seen


async def test_no_retry_when_the_first_attempt_found_something(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _spy(service, monkeypatch, hits={'City Bell, La Plata'})
    filters = ScrapingFilters(zona='City Bell, La Plata', tipos_propiedad=['departamento'])

    await service._scrape_source_once('zonaprop', filters, _noop_progress)

    assert seen == ['City Bell, La Plata']


async def test_no_retry_for_a_bare_zona(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing to strip — a second identical run would just burn a paid page."""
    seen = _spy(service, monkeypatch, hits=set())
    filters = ScrapingFilters(zona='City Bell', tipos_propiedad=['departamento'])

    await service._scrape_source_once('zonaprop', filters, _noop_progress)

    assert seen == ['City Bell']


async def test_map_path_retry_keeps_localidades_in_sync(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`localidades` outranks `zona` in `_input_for`; leaving a stale composite
    there would rebuild the same dead slug on the retry."""
    captured: list[tuple[str, list[str]]] = []

    async def fake(actor_id: str, filters: ScrapingFilters) -> Any:
        captured.append((filters.zona or '', list(filters.localidades)))
        return [], ZonaPropFunnel(search_url='https://z/x')

    monkeypatch.setattr(service, '_scrape_zonaprop_paginated', fake)
    filters = ScrapingFilters(
        zona='Villa Elisa', localidades=['Villa Elisa, La Plata'],
        tipos_propiedad=['departamento'],
    )

    await service._scrape_source_once('zonaprop', filters, _noop_progress)

    assert captured[1] == ('Villa Elisa', ['Villa Elisa'])
