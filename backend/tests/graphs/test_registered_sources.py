from unittest.mock import AsyncMock

from app.graphs.extraction import nodes
from app.models.property import Agency, RawProperty, ScrapingFilters


def test_registered_agency_receives_filters_once_even_if_discovered_twice():
    filters = ScrapingFilters(zonas=['City Bell', 'Gonnet'], tipo_operacion='venta')
    sends = nodes.route_after_review({
        'filters': filters,
        'manual_sources': [{'nombre': 'Urquiza', 'url': 'https://www.urquiza.com.ar/'}],
        'agencies': [Agency(id='1', nombre='Urquiza', sitio_web='http://urquiza.com.ar')],
        'selected_agency_ids': ['1'],
    })
    assert len(sends) == 1
    units = sends[0].arg['source_filters']
    assert [f.zona_pedida for f in units] == ['City Bell', 'Gonnet']
    assert [f.zonas for f in units] == [['City Bell'], ['Gonnet']]
    assert all(f.tipo_operacion == 'venta' for f in units)


def test_registered_agency_keeps_selected_gated_community_scope():
    units = nodes._registered_search_units({
        'filters': ScrapingFilters(zona='City Bell', tipo_operacion='venta'),
        'localidades': ['City Bell'],
        'barrios_cerrados': [{'nombre': 'Grand Bell', 'localidad': 'City Bell, La Plata'}],
    })
    assert len(units) == 1
    assert units[0].zona_pedida.startswith('Grand Bell,')
    assert units[0].localidades == []
    assert units[0].barrio_aliases


async def test_registered_source_uses_scrape_source_and_never_crawls_or_calls_llm(monkeypatch):
    filters = ScrapingFilters(zona='City Bell', tipo_operacion='venta')
    prop = RawProperty(
        fuente='urquiza', tipo_operacion='venta', tipo_propiedad='casa',
        direccion='Calle 123, City Bell', precio=100000,
        url_origen='https://www.urquiza.com.ar/p/100',
        imagenes=['https://example.com/house.jpg'],
    )
    service = AsyncMock()
    service.scrape_source.return_value = [prop]
    monkeypatch.setattr(nodes, 'get_apify_service', lambda: service)
    monkeypatch.setattr(nodes, 'adispatch_custom_event', AsyncMock())
    llm = AsyncMock(side_effect=AssertionError('A reviewed source must not use LLM extraction'))
    monkeypatch.setattr(nodes, '_extract_page_properties', llm)
    gallery = AsyncMock(side_effect=AssertionError('The scraper already read the detail gallery'))
    monkeypatch.setattr(nodes, 'harvest_page_images', gallery)
    save = AsyncMock()
    monkeypatch.setattr(nodes, '_upsert_properties', save)
    monkeypatch.setattr(nodes, '_link_job_properties', AsyncMock())
    out = await nodes.run_website_scraper({
        'nombre': 'Urquiza', 'url': 'https://www.urquiza.com.ar/', 'source_filters': [filters],
    }, {'configurable': {'supabase': None}})
    assert out['registered_properties'] == [prop]
    service.scrape_website.assert_not_called()
    service.scrape_source.assert_awaited_once()
    extracted = await nodes.extract_website_properties_llm({
        **out, 'filters': filters,
    }, {'configurable': {'supabase': None}})
    assert extracted['website_properties'][0].fuente == 'urquiza'
    llm.assert_not_called()
    gallery.assert_not_called()
    save.assert_awaited_once()


async def test_inmobusqueda_manual_root_uses_its_existing_portal_scraper(monkeypatch):
    service = AsyncMock()
    service.scrape_source.return_value = []
    monkeypatch.setattr(nodes, 'get_apify_service', lambda: service)
    monkeypatch.setattr(nodes, 'adispatch_custom_event', AsyncMock())
    await nodes.run_website_scraper({
        'url': 'https://www.inmobusqueda.com.ar/',
        'source_filters': [ScrapingFilters(zona='City Bell')],
    }, {})
    assert service.scrape_source.call_args.args[0] == 'inmobusqueda'
    service.scrape_website.assert_not_called()


def test_final_classification_checks_operation_type_and_area():
    from app.models.property import NormalizedProperty
    prop = NormalizedProperty(
        fuente='googlemaps', direccion='City Bell', tipo_operacion='alquiler',
        tipo_propiedad='departamento', m2_total=40,
    )
    assert not nodes._matches_filters(prop, ScrapingFilters(tipo_operacion='venta'))
    assert not nodes._matches_filters(prop, ScrapingFilters(tipos_propiedad=['casa']))
    assert not nodes._matches_filters(prop, ScrapingFilters(m2_min=80))
