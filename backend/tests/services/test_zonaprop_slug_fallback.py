"""ZonaProp composite-slug fallback: the disambiguated slug
(`villa-elisa-la-plata`) is tried first; if the portal doesn't know it, it
redirects to a nationwide listing whose items all fail the zona guard (or the
page 404s and the actor returns nothing). In that case `scrape_source` must
retry ONCE with the plain localidad slug instead of returning 0 results.
"""
from typing import Any

import pytest

from app.models.property import ScrapingFilters
from app.services.apify import ApifyService

# These exercise the Apify actor path, kept as the documented fallback
# (`ZONAPROP_USE_APIFY=true`). Production reads ZonaProp directly.
pytestmark = pytest.mark.usefixtures('apify_zonaprop')


_LP_ITEM = {
    'title': 'Casa en Villa Elisa',
    'url': 'https://www.zonaprop.com.ar/propiedades/clasificado/x-1.html',
    'neighborhood': 'Villa Elisa', 'city': 'La Plata',
    'address': 'Calle 425 e/ 135 y 136',
    'listingType': 'sale', 'propertyType': 'house',
    'price': 100000, 'currency': 'USD',
}

_ER_ITEM = {
    'title': 'Casa en Villa Elisa E.R.',
    'url': 'https://www.zonaprop.com.ar/propiedades/clasificado/x-2.html',
    'neighborhood': 'Villa Elisa', 'city': 'Villa Elisa',
    'address': 'Av. Mitre 228',
    'description': 'Casa en Villa Elisa, Colón, Entre Ríos.',
    'listingType': 'sale', 'propertyType': 'house',
    'price': 50000, 'currency': 'USD',
}


def _filters() -> ScrapingFilters:
    return ScrapingFilters(
        zona='Villa Elisa, La Plata',
        localidades=['Villa Elisa, La Plata'],
    )


async def _noop_progress(source: str, status: str, count: int) -> None:
    return None


@pytest.fixture()
def service(monkeypatch: pytest.MonkeyPatch) -> ApifyService:
    svc = ApifyService(api_token='dummy-token')
    return svc


async def test_composite_slug_success_runs_actor_once(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_run(src: str, actor: str, input_data: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append(input_data)
        # Page 1 only: page 2 ends the listing, so the call count below counts
        # SLUGS tried, not pages walked.
        return [] if '-pagina-' in input_data['searchUrl'] else [_LP_ITEM]

    monkeypatch.setattr(service, '_run_actor', fake_run)
    results = await service.scrape_source('zonaprop', _filters(), _noop_progress)
    slugs = [c['searchUrl'] for c in calls if '-pagina-' not in c['searchUrl']]
    assert len(slugs) == 1
    assert 'villa-elisa-la-plata' in slugs[0]
    assert len(results) == 1


async def test_fallback_to_plain_slug_when_guard_rejects_everything(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_run(src: str, actor: str, input_data: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append(input_data)
        url = input_data['searchUrl']
        if '-pagina-' in url:
            return []
        if 'villa-elisa-la-plata' in url:
            # composite slug redirected nationwide → off-zona garbage only
            return [_ER_ITEM]
        return [_LP_ITEM]

    monkeypatch.setattr(service, '_run_actor', fake_run)
    results = await service.scrape_source('zonaprop', _filters(), _noop_progress)
    slugs = [c['searchUrl'] for c in calls if '-pagina-' not in c['searchUrl']]
    assert len(slugs) == 2
    assert 'villa-elisa-la-plata' in slugs[0]
    assert 'inmuebles-venta-villa-elisa.html' in slugs[1]
    assert len(results) == 1


async def test_fallback_also_triggers_on_empty_actor_result(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_run(src: str, actor: str, input_data: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append(input_data)
        url = input_data['searchUrl']
        if '-pagina-' in url or 'villa-elisa-la-plata' in url:
            return []
        return [_LP_ITEM]

    monkeypatch.setattr(service, '_run_actor', fake_run)
    results = await service.scrape_source('zonaprop', _filters(), _noop_progress)
    slugs = [c['searchUrl'] for c in calls if '-pagina-' not in c['searchUrl']]
    assert len(slugs) == 2
    assert len(results) == 1


async def test_no_fallback_for_plain_localidad(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_run(src: str, actor: str, input_data: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append(input_data)
        return []

    monkeypatch.setattr(service, '_run_actor', fake_run)
    filters = ScrapingFilters(zona='Monte Grande', localidades=['Monte Grande'])
    results = await service.scrape_source('zonaprop', filters, _noop_progress)
    assert len(calls) == 1
    assert results == []
