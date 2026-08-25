"""Push the price ceiling into the ZonaProp search URL.

Every listing page is a PAID actor run, and the search URL carried no price at
all — so a "hasta 30.000 USD" query paid to scrape every page of every price
and then merely re-ordered the results (`_split_by_criteria` returns
`matched + rest`, it never drops). Letting the portal filter server-side is
what stops paying for pages that can't contain a single match.

URL grammar confirmed from real portal searches:
    ceiling  /locales-comerciales-venta-la-plata-la-plata-menos-30000-dolar.html
    range    /locales-comerciales-venta-la-plata-la-plata-20000-30000-dolar.html
    paging   /locales-comerciales-venta-la-plata-la-plata-menos-30000-dolar-pagina-3.html
Note `dolar` is SINGULAR and `-pagina-N` stays last, after the price segment.
"""
import pytest

from app.models.property import ScrapingFilters
from app.services.apify import ApifyService


@pytest.fixture()
def service() -> ApifyService:
    return ApifyService(api_token='dummy-token')


def _url(service: ApifyService, **overrides) -> str:
    data = {
        'zona': 'La Plata',
        'tipo_operacion': 'venta',
        'tipos_propiedad': ['local'],
    }
    data.update(overrides)
    return service._input_for('zonaprop', ScrapingFilters(**data))['searchUrl']


def test_price_ceiling_reaches_the_url(service: ApifyService) -> None:
    assert _url(service, precio_max=30000) == (
        'https://www.zonaprop.com.ar/locales-comerciales-venta-la-plata-menos-30000-dolar.html'
    )


def test_no_price_segment_without_a_ceiling(service: ApifyService) -> None:
    assert _url(service) == (
        'https://www.zonaprop.com.ar/locales-comerciales-venta-la-plata.html'
    )


def test_float_ceiling_is_rendered_as_an_integer(service: ApifyService) -> None:
    """`precio_max` is a float on the model; `30000.0` in a slug is a 404."""
    assert 'menos-30000-dolar' in _url(service, precio_max=30000.0)
    assert '30000.0' not in _url(service, precio_max=30000.0)


def test_a_floor_alone_does_not_change_the_url(service: ApifyService) -> None:
    """The `mas-N-dolar` form is NOT confirmed against the live portal, and a
    wrong slug redirects nationwide (the zona guard then rejects everything =
    a silent zero-result search). Until it is verified, a floor-only search
    keeps paying for the wide URL rather than risking that."""
    assert _url(service, precio_min=10000) == (
        'https://www.zonaprop.com.ar/locales-comerciales-venta-la-plata.html'
    )


def test_a_range_reaches_the_url(service: ApifyService) -> None:
    """Bare `{min}-{max}-dolar` — no `menos`/`mas` keyword, confirmed live."""
    assert _url(service, precio_min=20000, precio_max=30000) == (
        'https://www.zonaprop.com.ar/locales-comerciales-venta-la-plata-20000-30000-dolar.html'
    )


def test_range_bounds_are_rendered_as_integers(service: ApifyService) -> None:
    url = _url(service, precio_min=20000.0, precio_max=30000.0)
    assert '20000-30000-dolar' in url
    assert '.0' not in url


def test_a_zero_floor_uses_the_ceiling_form(service: ApifyService) -> None:
    """`0-30000-dolar` is a range the portal never emits for "up to 30k"; the
    `menos-` form is the one confirmed to work, so prefer it."""
    assert _url(service, precio_min=0, precio_max=30000) == (
        'https://www.zonaprop.com.ar/locales-comerciales-venta-la-plata-menos-30000-dolar.html'
    )


def test_an_inverted_range_falls_back_to_the_ceiling(service: ApifyService) -> None:
    """A floor above the ceiling is a parse error upstream, not a search. Emit
    the ceiling rather than a slug ZonaProp will not recognise — an unknown
    slug redirects nationwide and the zona guard then returns ZERO results."""
    assert _url(service, precio_min=50000, precio_max=30000) == (
        'https://www.zonaprop.com.ar/locales-comerciales-venta-la-plata-menos-30000-dolar.html'
    )


async def test_pagination_appends_after_the_price_segment(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`-pagina-N` is the LAST token, after the price — matching the real
    page-3 URL. Getting this backwards 404s every page but the first."""
    from app.core.config import settings
    monkeypatch.setattr(settings, 'ZONAPROP_MAX_RESULTS', 0)

    seen: list[str] = []

    async def fake_run(src: str, actor: str, input_data: dict) -> list:
        seen.append(input_data['searchUrl'])
        return [] if len(seen) > 1 else [{
            'title': 'Local en La Plata', 'url': f'https://z/{len(seen)}',
            'listingId': str(len(seen)), 'neighborhood': 'La Plata',
            'city': 'La Plata', 'address': 'Calle 47 500',
            'propertyType': 'commercial', 'price': 25000, 'currency': 'USD',
        }] * 30

    monkeypatch.setattr(service, '_run_actor', fake_run)
    filters = ScrapingFilters(
        zona='La Plata', tipo_operacion='venta',
        tipos_propiedad=['local'], precio_max=30000,
    )

    await service._scrape_zonaprop_paginated('actor', filters)

    assert seen[0].endswith('-menos-30000-dolar.html')
    assert seen[1].endswith('-menos-30000-dolar-pagina-2.html')
