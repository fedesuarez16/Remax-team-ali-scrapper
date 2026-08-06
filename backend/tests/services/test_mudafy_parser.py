"""Mudafy is a Next.js App Router site: the listing data is NOT in the DOM and
there is no `__NEXT_DATA__` blob — it ships inside the React Server Components
flight payload (`self.__next_f.push([...])`), JSON-escaped inside a JS string.

Rather than decoding the whole flight format (an internal React protocol that
changes between Next releases), `_parse_mudafy_payload` unescapes the page and
scans for the `"publication":{...}` objects, decoding each with a raw JSON
decoder. That keeps the coupling down to one stable property name.

The payload is by far the richest in this catalog — it carries `street` and
`street_number` as separate fields, which is exactly what the cross-portal
dedup fingerprint needs, plus coordinates, room breakdown and areas:

  publication.price          {currency, amount}
  publication.address        {full_address, street, street_number, floor_number,
                              public_address, coordinates}
  publication.dimensions     {total_area, roofed_area, …}
  publication.property.kind  "apartment" | "house" | …
  publication.property.rooms {total_count, bedrooms, bathrooms, garages}
  publication.slug           → https://mudafy.com.ar/{kind-path}/{slug}

robots.txt disallows `/api/` and `/*?`, so the scraper only ever walks
path-based URLs (`/{operacion}/{tipo}/{region}` and `/{N}-p` for later pages).
"""
from app.models.property import ScrapingFilters
from app.services.apify import _parse_mudafy_payload

# Trimmed verbatim from a live capture of
# https://mudafy.com.ar/venta/departamentos/provincia-de-buenos-aires-gba-sur
_PAYLOAD = r'''
self.__next_f.push([1,"18:[\"$\",\"$L3d\",null,{\"publication\":{\"id\":14507211,
\"type\":\"property\",\"resource_type\":\"properties\",\"resource_id\":10264375,
\"site_id\":\"AR\",\"price\":{\"currency\":\"USD\",\"amount\":60000},
\"slug\":\"plaza-matheu-9-departamento-en-venta-396098\",
\"address\":{\"full_address\":\"Plaza Matheu 9, La Plata, Provincia de Buenos Aires, Argentina\",
\"street\":\"Plaza Matheu\",\"street_number\":9,\"floor_number\":\"5\",\"unit_number\":\"B\",
\"public_address\":\"Plaza Matheu 9\",\"zip_code\":\"B1904\",
\"coordinates\":{\"latitude\":-34.9195027,\"longitude\":-57.9283069}},
\"dimensions\":{\"total_area\":56.76,\"plot_area\":0,\"roofed_area\":53},
\"property\":{\"kind\":\"apartment\",\"rooms\":{\"total_count\":3,\"bathrooms\":1,
\"toilets\":0,\"bedrooms\":2,\"garages\":1},\"construction_year\":2006},
\"title\":\"Venta departamento Semipiso de 3 Ambientes\",
\"description\":\"Muy luminoso, contrafrente.\",
\"pictures\":[{\"url\":\"https://media.mudafy.com/a.jpg\"},{\"url\":\"https://media.mudafy.com/b.jpg\"}]}}]"])
self.__next_f.push([1,"19:[\"$\",\"$L3d\",null,{\"publication\":{\"id\":9911,
\"price\":{\"currency\":\"ARS\",\"amount\":450000},
\"slug\":\"calle-7-1234-casa-en-alquiler-000111\",
\"address\":{\"full_address\":\"Calle 7 1234, Berisso, Provincia de Buenos Aires, Argentina\",
\"street\":\"Calle 7\",\"street_number\":1234,\"public_address\":\"Calle 7 1234\"},
\"dimensions\":{\"total_area\":120},
\"property\":{\"kind\":\"house\",\"rooms\":{\"total_count\":4,\"bathrooms\":2,\"garages\":0}},
\"title\":\"Casa en alquiler\",\"pictures\":[]}}]"])
self.__next_f.push([1,"1a:[\"$\",\"$L3d\",null,{\"publication\":{\"id\":7777,
\"price\":{\"currency\":\"USD\",\"amount\":90000},
\"slug\":\"otra-9999\",
\"address\":{\"full_address\":\"Av Colon 100, Mar del Plata, Provincia de Buenos Aires, Argentina\",
\"street\":\"Av Colon\",\"street_number\":100},
\"dimensions\":{\"total_area\":70},
\"property\":{\"kind\":\"apartment\",\"rooms\":{\"total_count\":2}},
\"title\":\"Depto Mar del Plata\",\"pictures\":[]}}]"])
'''

_LA_PLATA = ScrapingFilters(zona='La Plata', zonas=['La Plata'], tipo_operacion='venta')


def _parse(payload: str = _PAYLOAD, filters: ScrapingFilters = _LA_PLATA):
    return _parse_mudafy_payload(payload, filters)


def test_extracts_publications_matching_the_zona():
    """Mudafy only filters by broad REGION, so the searched zona is enforced
    locally — the Mar del Plata and Berisso rows are out of a La Plata search."""
    props = _parse()
    assert len(props) == 1
    assert props[0].direccion.startswith('Plaza Matheu 9')


def test_every_property_is_tagged_with_the_portal():
    wide = ScrapingFilters(zona='Provincia de Buenos Aires', zonas=[])
    assert {p.fuente for p in _parse(filters=wide)} == {'mudafy'}


def test_reads_price_and_currency():
    prop = _parse()[0]
    assert (prop.precio, prop.moneda) == (60000.0, 'USD')


def test_reads_the_room_breakdown():
    prop = _parse()[0]
    assert (prop.ambientes, prop.banos, prop.cocheras) == (3, 1, 1)


def test_reads_areas_and_age():
    prop = _parse()[0]
    assert prop.m2_total == 56.76
    assert prop.m2_cubiertos == 53.0
    assert prop.antiguedad == 2006


def test_reads_the_floor_number():
    assert _parse()[0].piso == 5


def test_maps_the_property_kind():
    wide = ScrapingFilters(zona='Provincia de Buenos Aires', zonas=[])
    kinds = {p.tipo_propiedad for p in _parse(filters=wide)}
    assert kinds == {'departamento', 'casa'}


def test_operation_comes_from_the_searched_filter():
    """The payload never names the operation — the listing URL selected it."""
    assert _parse()[0].tipo_operacion == 'venta'


def test_builds_the_listing_url_from_the_slug():
    assert _parse()[0].url_origen == 'https://mudafy.com.ar/apartment/plaza-matheu-9-departamento-en-venta-396098'


def test_collects_pictures():
    assert _parse()[0].imagenes == [
        'https://media.mudafy.com/a.jpg', 'https://media.mudafy.com/b.jpg',
    ]


def test_address_keeps_street_and_number_for_the_dedup_fingerprint():
    """`street` + `street_number` are what let the same listing collapse with
    Zonaprop's and Argenprop's spelling of it."""
    from app.services.zona import address_fingerprint
    assert address_fingerprint(_parse()[0].direccion) == 'plaza matheu 9'


def test_a_page_with_no_publications_yields_nothing():
    assert _parse('self.__next_f.push([1,"nothing here"])') == []
