"""Place search for the /map search bar: Photon autocomplete + geometry lookup.

External HTTP is mocked with `httpx.MockTransport` — no network.
"""
import httpx
import pytest

from app.services import place_search
from app.services.place_search import (
    PlaceSearchError, connected_street, largest_ring, parse_overpass_lines, parse_photon_feature,
    place_geometry, search_places,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    place_search._cache.clear()


def _feature(**props: object) -> dict:
    return {
        'type': 'Feature',
        'geometry': {'type': 'Point', 'coordinates': [-57.95, -34.92]},
        'properties': {'osm_type': 'N', 'osm_id': 1, 'osm_key': 'place', **props},
    }


def test_parse_photon_feature_classifies_street() -> None:
    place = parse_photon_feature(_feature(
        osm_type='W', osm_id=42, osm_key='highway', osm_value='residential',
        type='street', name='Calle 7', city='La Plata',
    ))
    assert place is not None
    assert place.kind == 'street'
    assert place.id == 'W42'
    assert place.name == 'Calle 7'
    assert place.label == 'Calle 7, La Plata'
    assert place.city == 'La Plata'
    assert (place.lat, place.lng) == (-34.92, -57.95)


def test_parse_photon_feature_classifies_address_with_housenumber() -> None:
    place = parse_photon_feature(_feature(
        type='house', street='Calle 7', housenumber='1234', city='La Plata',
    ))
    assert place is not None
    assert place.kind == 'address'
    assert place.name == 'Calle 7 1234'


def test_parse_photon_feature_classifies_barrio_as_area() -> None:
    place = parse_photon_feature(_feature(
        osm_type='R', osm_id=9, osm_value='suburb', type='district',
        name='Tolosa', city='La Plata',
    ))
    assert place is not None
    assert place.kind == 'area'
    assert place.osm_type == 'R'


def test_parse_photon_feature_classifies_other_named_place_as_poi() -> None:
    place = parse_photon_feature(_feature(
        osm_key='amenity', osm_value='school', type='house', name='Colegio Nacional',
    ))
    assert place is not None
    assert place.kind == 'poi'


def test_parse_photon_feature_skips_nameless_feature() -> None:
    assert parse_photon_feature(_feature(type='house')) is None


async def test_search_places_parses_and_caches() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={'features': [
            _feature(osm_type='W', osm_id=1, osm_key='highway', type='street', name='Calle 7'),
            _feature(type='house'),  # nameless → dropped
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = await search_places('calle 7', client=client)
        second = await search_places('Calle 7 ', client=client)

    assert [p.name for p in first] == ['Calle 7']
    assert second == first
    assert len(calls) == 1
    assert calls[0].url.params['q'] == 'calle 7'
    assert 'bbox' in calls[0].url.params


async def test_search_places_collapses_segments_of_one_street() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'features': [
            _feature(osm_type='W', osm_id=1, osm_key='highway', type='street',
                     name='Avenida 7', city='La Plata'),
            _feature(osm_type='W', osm_id=2, osm_key='highway', type='street',
                     name='Avenida 7', district='Microcentro', city='La Plata'),
            _feature(osm_type='W', osm_id=3, osm_key='highway', type='street',
                     name='Avenida 7', city='Berisso'),
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        places = await search_places('avenida 7', client=client)

    assert [p.label for p in places] == ['Avenida 7, La Plata', 'Avenida 7, Berisso']


async def test_search_places_keeps_area_sharing_label_with_poi() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'features': [
            _feature(osm_key='railway', osm_value='station', type='house', name='Tolosa'),
            _feature(osm_type='R', osm_id=9, type='city', name='Tolosa'),
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        places = await search_places('tolosa', client=client)

    assert [p.kind for p in places] == ['poi', 'area']


async def test_place_geometry_street_falls_back_to_nominatim() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if 'overpass' in request.url.host:
            raise httpx.ReadTimeout('overloaded')
        assert request.url.params['street'] == 'Avenida 7'
        assert request.url.params['city'] == 'La Plata'
        assert request.url.params['dedupe'] == '0'
        return httpx.Response(200, json=[
            {'geojson': {'type': 'LineString', 'coordinates': [[-57.9, -34.9], [-57.91, -34.91]]}},
            {'geojson': {'type': 'Point', 'coordinates': [-57.9, -34.9]}},
        ])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        geom = await place_geometry(
            'street', osm_type='W', osm_id=1, name='Avenida 7', city='La Plata', client=client,
        )

    assert geom.lines == [[(-34.9, -57.9), (-34.91, -57.91)]]


async def test_search_places_raises_on_upstream_error() -> None:
    transport = httpx.MockTransport(lambda _r: httpx.Response(502))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(PlaceSearchError):
            await search_places('tolosa', client=client)


def test_largest_ring_picks_biggest_outer_ring_as_lat_lng() -> None:
    small = [[0, 0], [0, 1], [1, 1], [0, 0]]
    big = [[0, 0], [0, 5], [5, 5], [5, 0], [0, 0]]
    ring = largest_ring({'type': 'MultiPolygon', 'coordinates': [[small], [big]]})
    assert ring == [(0, 0), (5, 0), (5, 5), (0, 5), (0, 0)]


def test_largest_ring_returns_none_for_points() -> None:
    assert largest_ring({'type': 'Point', 'coordinates': [1, 2]}) is None


def test_parse_overpass_lines_converts_way_geometry() -> None:
    data = {'elements': [
        {'type': 'way', 'geometry': [{'lat': -34.9, 'lon': -57.9}, {'lat': -34.91, 'lon': -57.91}]},
        {'type': 'way', 'geometry': [{'lat': -34.9, 'lon': -57.9}]},  # degenerate → dropped
    ]}
    assert parse_overpass_lines(data) == [[(-34.9, -57.9), (-34.91, -57.91)]]


async def test_place_geometry_area_uses_nominatim_polygon() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params['osm_ids'] == 'R9'
        return httpx.Response(200, json=[{'geojson': {
            'type': 'Polygon', 'coordinates': [[[-57.9, -34.9], [-57.8, -34.9], [-57.8, -34.8]]],
        }}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        geom = await place_geometry('area', osm_type='R', osm_id=9, name='Tolosa', client=client)

    assert geom.polygon == [(-34.9, -57.9), (-34.9, -57.8), (-34.8, -57.8)]
    assert geom.lines is None


async def test_place_geometry_area_node_has_no_polygon() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError('a node has no boundary — must not hit Nominatim')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        geom = await place_geometry('area', osm_type='N', osm_id=1, name='Tolosa', client=client)

    assert geom.polygon is None
    assert geom.lines is None


async def test_place_geometry_street_uses_overpass_by_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert '"name"="Calle 7"' in request.url.params['data']
        return httpx.Response(200, json={'elements': [
            {'type': 'way', 'geometry': [{'lat': -34.9, 'lon': -57.9}, {'lat': -34.91, 'lon': -57.91}]},
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        geom = await place_geometry('street', osm_type='W', osm_id=1, name='Calle 7', client=client)

    assert geom.lines == [[(-34.9, -57.9), (-34.91, -57.91)]]
    assert geom.polygon is None


def test_connected_street_keeps_only_the_picked_towns_street() -> None:
    # Two chained segments near the pick + a same-named street in another town.
    a = [(-34.900, -57.900), (-34.905, -57.905)]
    b = [(-34.905, -57.905), (-34.910, -57.910)]
    elsewhere = [(-34.700, -58.300), (-34.705, -58.305)]
    assert connected_street([elsewhere, b, a], (-34.901, -57.901)) == [a, b]


def test_connected_street_bridges_tiny_gaps_between_segments() -> None:
    a = [(-34.900, -57.900), (-34.905, -57.905)]
    b = [(-34.9052, -57.9052), (-34.910, -57.910)]  # ~30 m gap, same street
    assert connected_street([a, b], (-34.900, -57.900)) == [a, b]


async def test_place_geometry_street_filters_by_pick_point() -> None:
    near = {'type': 'way', 'geometry': [{'lat': -34.9, 'lon': -57.9}, {'lat': -34.91, 'lon': -57.91}]}
    far = {'type': 'way', 'geometry': [{'lat': -34.7, 'lon': -58.3}, {'lat': -34.71, 'lon': -58.31}]}
    transport = httpx.MockTransport(
        lambda _r: httpx.Response(200, json={'elements': [far, near]})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        geom = await place_geometry(
            'street', osm_type='W', osm_id=1, name='Calle 7', lat=-34.9, lng=-57.9, client=client,
        )
    assert geom.lines == [[(-34.9, -57.9), (-34.91, -57.91)]]


async def test_place_geometry_street_escapes_quotes_in_name() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params['data'])
        return httpx.Response(200, json={'elements': []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await place_geometry('street', osm_type='W', osm_id=1, name='a"b\\c', client=client)

    assert '"name"="a\\"b\\\\c"' in seen[0]
