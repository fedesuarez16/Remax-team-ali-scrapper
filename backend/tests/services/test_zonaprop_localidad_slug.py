"""Test-first for ZonaProp search-URL slug using localidad when available
(T-5.3/5.4) — written BEFORE `_input_for` reads `filters.localidades`, so the
localidad-present assertion MUST fail until T-5.4 lands.

`_input_for` is a method on `ApifyService`; instantiating it doesn't touch the
network (only `_run_actor` does), so a dummy token is safe here.
"""
from app.models.property import ScrapingFilters
from app.services.apify import ApifyService


def _service() -> ApifyService:
    return ApifyService(api_token='dummy-token')


def test_zonaprop_url_uses_localidad_slug_when_present() -> None:
    filters = ScrapingFilters(zona='Palermo', localidades=['CABA'])
    input_data = _service()._input_for('zonaprop', filters)
    assert 'caba' in input_data['searchUrl']
    assert 'palermo' not in input_data['searchUrl']


def test_zonaprop_url_falls_back_to_barrio_slug_when_no_localidad() -> None:
    filters = ScrapingFilters(zona='Palermo')
    input_data = _service()._input_for('zonaprop', filters)
    assert 'palermo' in input_data['searchUrl']


def test_zonaprop_url_composite_localidad_slugs_with_partido() -> None:
    # "Villa Elisa" alone resolves to Villa Elisa, Entre Ríos on ZonaProp;
    # the composite localidad must produce the disambiguated slug.
    filters = ScrapingFilters(zona='Villa Elisa', localidades=['Villa Elisa, La Plata'])
    input_data = _service()._input_for('zonaprop', filters)
    assert 'inmuebles-venta-villa-elisa-la-plata.html' in input_data['searchUrl']
