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
  publication.photos[]       {order, is_enabled, type, large_link, medium_link,
                              full_size_link, standard_link, small_link,
                              tiny_link, original_link}

robots.txt disallows `/api/` and `/*?`, so the scraper only ever walks
path-based URLs (`/{operacion}/{tipo}/{region}` and `/{N}-p` for later pages).
"""
from app.models.property import ScrapingFilters
from app.services.apify import _mudafy_search_bases, _parse_mudafy_payload

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
\"photos\":[
{\"id\":2,\"type\":\"photo\",\"order\":1,\"is_enabled\":true,
\"original_link\":\"https://mfy.mudafy.com/b.jpg\",\"large_link\":\"https://mfy.mudafy.com/b_large.webp\",
\"medium_link\":\"https://mfy.mudafy.com/b_medium.webp\",\"tiny_link\":\"https://mfy.mudafy.com/b_tiny.webp\"},
{\"id\":1,\"type\":\"photo\",\"order\":0,\"is_enabled\":true,
\"original_link\":\"https://mfy.mudafy.com/a.jpg\",\"large_link\":\"https://mfy.mudafy.com/a_large.webp\"},
{\"id\":3,\"type\":\"photo\",\"order\":2,\"is_enabled\":false,
\"large_link\":\"https://mfy.mudafy.com/hidden_large.webp\"},
{\"id\":4,\"type\":\"blueprint\",\"order\":3,\"is_enabled\":true,
\"large_link\":\"https://mfy.mudafy.com/plano_large.webp\"},
{\"id\":5,\"type\":\"photo\",\"order\":4,\"is_enabled\":true,
\"medium_link\":\"https://mfy.mudafy.com/e_medium.webp\",\"original_link\":\"https://mfy.mudafy.com/e.jpg\"}
]}}]"])
self.__next_f.push([1,"19:[\"$\",\"$L3d\",null,{\"publication\":{\"id\":9911,
\"price\":{\"currency\":\"ARS\",\"amount\":450000},
\"slug\":\"calle-7-1234-casa-en-alquiler-000111\",
\"address\":{\"full_address\":\"Calle 7 1234, Berisso, Provincia de Buenos Aires, Argentina\",
\"street\":\"Calle 7\",\"street_number\":1234,\"public_address\":\"Calle 7 1234\"},
\"dimensions\":{\"total_area\":120},
\"property\":{\"kind\":\"house\",\"rooms\":{\"total_count\":4,\"bathrooms\":2,\"garages\":0}},
\"title\":\"Casa en alquiler\",\"photos\":[]}}]"])
self.__next_f.push([1,"1a:[\"$\",\"$L3d\",null,{\"publication\":{\"id\":7777,
\"price\":{\"currency\":\"USD\",\"amount\":90000},
\"slug\":\"otra-9999\",
\"address\":{\"full_address\":\"Av Colon 100, Mar del Plata, Provincia de Buenos Aires, Argentina\",
\"street\":\"Av Colon\",\"street_number\":100},
\"dimensions\":{\"total_area\":70},
\"property\":{\"kind\":\"apartment\",\"rooms\":{\"total_count\":2}},
\"title\":\"Depto Mar del Plata\",\"photos\":null}}]"])
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


def test_collects_the_gallery_from_photos_in_display_order():
    """The payload names the gallery `photos` (not `pictures`), each entry a bag
    of per-size CDN links. `order` is the gallery order the site itself renders,
    and it is NOT guaranteed to match the array order."""
    assert _parse()[0].imagenes == [
        'https://mfy.mudafy.com/a_large.webp',
        'https://mfy.mudafy.com/b_large.webp',
        'https://mfy.mudafy.com/e_medium.webp',
    ]


def test_skips_disabled_photos_and_non_photo_assets():
    """`is_enabled: false` is a photo the seller pulled down, and blueprints are
    not gallery material — neither belongs in a card."""
    urls = _parse()[0].imagenes
    assert not any('hidden' in u or 'plano' in u for u in urls)


def test_falls_back_down_the_size_ladder_when_a_variant_is_missing():
    """Not every photo carries every size; the entry is only dropped when it has
    no usable link at all."""
    assert 'https://mfy.mudafy.com/e_medium.webp' in _parse()[0].imagenes


def test_a_publication_with_no_photos_yields_an_empty_gallery():
    """`photos` arrives both as `[]` and as `null` — neither may raise."""
    wide = ScrapingFilters(zona='Provincia de Buenos Aires', zonas=[])
    galleries = {p.titulo: p.imagenes for p in _parse(filters=wide)}
    assert galleries['Casa en alquiler'] == []
    assert galleries['Depto Mar del Plata'] == []


def test_address_keeps_street_and_number_for_the_dedup_fingerprint():
    """`street` + `street_number` are what let the same listing collapse with
    Zonaprop's and Argenprop's spelling of it."""
    from app.services.zona import address_fingerprint
    assert address_fingerprint(_parse()[0].direccion) == 'plaza matheu 9'


def test_a_page_with_no_publications_yields_nothing():
    assert _parse('self.__next_f.push([1,"nothing here"])') == []


# ── Where the search actually points ─────────────────────────────────────────
# Mudafy DOES serve city and barrio pages — the slug just has to carry its full
# ancestry (`{region}-{city}`, `{region}-{city}-{barrio}`); only a bare
# `/la-plata` 404s. Verified live: `…-gba-sur-la-plata` answers 200 with "10
# propiedades" and NO pagination, while `…-gba-sur` answers 200 with 14 pages
# of ~25. Searching the region for a city therefore means sweeping ~350 rows to
# find ~10, and the zona guard throws away everything else on the way.

def test_searches_the_city_page_before_the_region():
    """The precise location first — the region is only the safety net."""
    bases = _mudafy_search_bases(_LA_PLATA)
    assert bases[0] == (
        'https://mudafy.com.ar/venta/propiedades/provincia-de-buenos-aires-gba-sur-la-plata'
    )
    assert bases[-1] == (
        'https://mudafy.com.ar/venta/propiedades/provincia-de-buenos-aires-gba-sur'
    )


def test_a_barrio_slug_hangs_off_its_parent_city():
    """`…-gba-sur-city-bell` 404s; `…-gba-sur-la-plata-city-bell` is the real one."""
    f = ScrapingFilters(zona='City Bell', zonas=['City Bell'], tipo_operacion='venta')
    assert _mudafy_search_bases(f)[0].endswith(
        '/provincia-de-buenos-aires-gba-sur-la-plata-city-bell'
    )


def test_a_zona_that_renames_itself_uses_its_mudafy_spelling():
    """Mudafy files Gonnet under `manuel-b-gonnet` — the derived slug would 404."""
    f = ScrapingFilters(zona='Gonnet', zonas=['Gonnet'], tipo_operacion='venta')
    assert _mudafy_search_bases(f)[0].endswith(
        '/provincia-de-buenos-aires-gba-sur-la-plata-manuel-b-gonnet'
    )


def test_an_unmapped_zona_derives_its_slug_from_the_region():
    """`{region}-{zona}` is the site's own convention, so it's worth attempting;
    a 404 just falls through to the region base."""
    f = ScrapingFilters(zona='Quilmes', zonas=['Quilmes'], tipo_operacion='venta')
    assert _mudafy_search_bases(f)[0].endswith('/provincia-de-buenos-aires-gba-sur-quilmes')


def test_a_region_wide_search_has_nothing_more_precise_to_try():
    f = ScrapingFilters(zona='CABA', zonas=['CABA'], tipo_operacion='venta')
    assert _mudafy_search_bases(f) == ['https://mudafy.com.ar/venta/propiedades/caba']


# ── The zona guard ───────────────────────────────────────────────────────────
# `address.full_address` is free text the seller typed: on a live page 17 of 25
# rows carried no locality at all ("Belgrano 838", "LINEO 19"). The locality
# lives in `location_name` / `location_short_name` / `location_slug`, which the
# guard was ignoring — so real La Plata listings were being discarded.
_FREE_ADDRESS_PAYLOAD = r'''
self.__next_f.push([1,"20:[\"$\",\"$L3d\",null,{\"publication\":{\"id\":5150,
\"price\":{\"currency\":\"USD\",\"amount\":75000},
\"slug\":\"belgrano-838-departamento-en-venta-1\",
\"location_name\":\"La Plata, Provincia de Buenos Aires\",
\"location_short_name\":\"La Plata\",
\"location_slug\":\"provincia-de-buenos-aires-gba-sur-la-plata\",
\"address\":{\"full_address\":\"Belgrano 838\",\"public_address\":\"Belgrano 838\"},
\"dimensions\":{\"total_area\":48},
\"property\":{\"kind\":\"apartment\",\"rooms\":{\"total_count\":2}},
\"title\":\"Departamento 2 ambientes\",\"photos\":[]}}]"])
'''


def test_zona_guard_reads_the_location_fields_not_just_the_address():
    """A listing whose address is bare street text is still a La Plata listing."""
    props = _parse_mudafy_payload(_FREE_ADDRESS_PAYLOAD, _LA_PLATA)
    assert [p.direccion for p in props] == ['Belgrano 838']


def test_the_location_fields_do_not_smuggle_in_another_zona():
    """The guard must stay a guard: matching on location cannot turn into
    matching on the region every row shares."""
    berazategui = ScrapingFilters(
        zona='Berazategui', zonas=['Berazategui'], tipo_operacion='venta',
    )
    assert _parse_mudafy_payload(_FREE_ADDRESS_PAYLOAD, berazategui) == []
