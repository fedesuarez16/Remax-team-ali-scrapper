import pytest

from app.graphs.extraction.nodes import _prop_to_dict, normalize_properties
from app.models.property import RawProperty
from app.services.apify import _norm_remax
from app.services.tokko import parse_detail


@pytest.mark.parametrize('source', ['remax', 'remaxroble'])
def test_remax_keeps_locality_and_native_coordinates_through_persistence(source):
    raw = _norm_remax({
        'price': 150000, 'slug': 'casa-city-bell',
        'displayAddress': '461 E/ 14 y 14a 200', 'geoLabel': 'City Bell, La Plata, Buenos Aires',
        'location': {'type': 'Point', 'coordinates': [-58.059611, -34.864257]},
    }, 'City Bell', source)
    assert raw is not None
    prop = normalize_properties({'collected_properties': [raw]})['normalized_properties'][0]
    row = _prop_to_dict(prop, 'job')
    assert row['direccion'] == '461 E/ 14 y 14a 200, City Bell, La Plata, Buenos Aires'
    assert row['lat'] == -34.864257
    assert row['lng'] == -58.059611
    assert row['geocoded_at']  # skip the background street geocoder


def test_tokko_keeps_published_location_without_an_extra_http_request():
    raw = parse_detail('''<div id="ficha_desc"></div><script>
        var map = L.map("openstreetmap_box"); L.circle([-34.874,-58.0583], 400, {});
    </script>''', RawProperty(fuente='urquiza', direccion='19 e/ 462 y 464, City Bell'))
    prop = normalize_properties({'collected_properties': [raw]})['normalized_properties'][0]
    row = _prop_to_dict(prop, 'job')
    assert (row['lat'], row['lng']) == (-34.874, -58.0583)


@pytest.mark.parametrize('raw', [{}, {'latitude': -38.0855, 'longitude': -57.61}])
def test_missing_or_out_of_region_native_coordinates_remain_pending(raw):
    prop = normalize_properties({'collected_properties': [
        RawProperty(fuente='dacalbr', direccion='Casa, City Bell', raw=raw),
    ]})['normalized_properties'][0]
    row = _prop_to_dict(prop, 'job')
    assert row['lat'] is None
    assert row['lng'] is None
    assert row['geocoded_at'] is None
