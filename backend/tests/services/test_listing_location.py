import httpx
import pytest

from app.services.listing_location import (
    listing_coordinates, remax_coordinates, tokko_coordinates, valid_coordinates,
)


@pytest.mark.parametrize(('lat', 'lng'), [
    (0, 0), ('NaN', -58), (-34, 'inf'), (-58.05, -34.86), (None, None),
])
def test_invalid_coordinates_are_rejected(lat, lng):
    assert valid_coordinates(lat, lng) is None


def test_remax_geojson_uses_longitude_first():
    assert remax_coordinates({
        'location': {'type': 'Point', 'coordinates': [-58.059611, -34.864257]},
    }) == (-34.864257, -58.059611)


@pytest.mark.parametrize('item', [
    {}, {'location': {}}, {'location': {'type': 'Point', 'coordinates': [1]}},
    {'location': {'type': 'Polygon', 'coordinates': [-58, -34]}},
])
def test_remax_does_not_invent_a_point(item):
    assert remax_coordinates(item) is None


@pytest.mark.parametrize('script', [
    'var map = L.map("openstreetmap_box"); L.circle([-34.874, -58.0583], 400, {});',
    'function initMap() {var fenway = new google.maps.LatLng(-34.874,-58.0583);'
    '$("#ficha_streetview").show();}',
])
def test_known_tokko_property_map_templates(script):
    html = f'<div id="ficha_desc"></div><script>{script}</script>'
    assert tokko_coordinates(html) == (-34.874, -58.0583)


def test_office_footer_map_and_search_map_are_not_property_coordinates():
    assert tokko_coordinates('''<div id="ficha_desc"></div><script>
        var office = new google.maps.LatLng(-34.90, -58.05);
    </script>''') is None
    assert tokko_coordinates('''<script>
        var map = L.map("openstreetmap_box"); L.circle([-34.874, -58.0583], 400, {});
    </script>''') is None


@pytest.mark.parametrize('url', [
    'https://localhost/p/1', 'https://www.urquiza.com.ar.evil.example/p/1',
    'http://www.urquiza.com.ar/p/1', 'https://user@www.urquiza.com.ar/p/1',
    'https://www.urquiza.com.ar/contacto', 'https://www.remax.com.ar/roble',
])
async def test_only_known_listing_endpoints_are_fetched(url):
    def unexpected(_):
        pytest.fail('unapproved URL was requested')

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        assert await listing_coordinates({'url_origen': url}, client=client) is None


async def test_remax_reads_listing_point_instead_of_geocoding_street():
    def reply(request):
        assert request.url.host == 'api-ar.redremax.com'
        assert request.url.path.endswith('/findBySlug/casa-city-bell')
        return httpx.Response(200, json={'data': {
            'location': {'type': 'Point', 'coordinates': [-58.059611, -34.864257]},
        }})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        assert await listing_coordinates({
            'url_origen': 'https://www.remax.com.ar/listings/casa-city-bell',
        }, client=client) == (-34.864257, -58.059611)


async def test_listing_redirect_is_not_followed_to_an_unrelated_map():
    requests = []

    def reply(request):
        requests.append(request)
        return httpx.Response(302, headers={'Location': 'https://www.urquiza.com.ar/contacto'})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(reply), follow_redirects=True,
    ) as client:
        assert await listing_coordinates({
            'url_origen': 'https://www.urquiza.com.ar/p/123-casa',
        }, client=client) is None
    assert len(requests) == 1
