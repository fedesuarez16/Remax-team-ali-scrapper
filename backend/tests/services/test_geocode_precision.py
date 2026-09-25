from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import geocode as service


def _candidate(**changes):
    row = {
        'lat': '-34.864257', 'lon': '-58.059611',
        'address': {
            'road': 'Calle 461', 'house_number': '200',
            'town': 'City Bell', 'city': 'La Plata', 'country_code': 'ar',
        },
    }
    row.update(changes)
    return row


async def test_skips_wrong_first_result_and_checks_street_height_and_locality():
    bad = [
        _candidate(lat='-38.0855', lon='-57.6184'),
        _candidate(address={'country_code': 'ar', 'road': '461', 'town': 'City Bell'}),
        _candidate(address={**_candidate()['address'], 'house_number': '201'}),
        _candidate(address={**_candidate()['address'], 'road': 'Calle 462'}),
        _candidate(address={**_candidate()['address'], 'town': 'Gonnet'}),
    ]
    requests = []

    def reply(request):
        requests.append(request)
        return httpx.Response(200, json=[*bad, _candidate()])

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        point = await service.geocode(
            'Calle 461 200, City Bell, La Plata', client=client, viewbox=service.LP_VIEWBOX,
        )
    assert point == (-34.864257, -58.059611)
    assert requests[0].url.params['bounded'] == '1'
    assert requests[0].url.params['addressdetails'] == '1'


@pytest.mark.parametrize('address', [
    'Calle 461', 'Calle 461, City Bell', 'Calle 461 entre 14 y 14a, City Bell',
    'Calle 461 y 14, City Bell', 'Calle 461 200', 'City Bell, La Plata',
])
async def test_ambiguous_addresses_never_become_road_or_locality_centroids(address):
    client = AsyncMock()
    assert await service.geocode(address, client=client) is None
    client.get.assert_not_called()


@pytest.mark.parametrize('payload', [None, {}, [None], [{'lat': 'NaN', 'lon': '-58'}]])
async def test_malformed_response_is_not_a_location(payload):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json=payload),
    )) as client:
        assert await service.geocode('Calle 461 200, City Bell', client=client) is None


@pytest.mark.parametrize('status', [429, 500, 503])
async def test_transient_error_does_not_become_permanent_missing_address(status):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(status),
    )) as client:
        with pytest.raises(service.TransientGeocodeError):
            await service.geocode('Calle 461 200, City Bell', client=client)


def test_historical_remax_address_recovers_only_explicit_listing_locality():
    row = {'direccion': '461 E/ 14 y 14a 200', 'titulo': 'CASA CITY BELL 3 DORMITORIOS'}
    assert service._address_properties(row) == 'Calle 461 200, CITY BELL'
    assert service._viewbox_for_properties(row) == service.LP_VIEWBOX
    assert service._address_properties({'direccion': '461 E/ 14 y 14a 200'}) == 'Calle 461 200'


async def test_native_coordinates_are_preferred_and_repeated_urls_are_cached(monkeypatch):
    native = AsyncMock(return_value=(-34.864257, -58.059611))
    fallback = AsyncMock()
    monkeypatch.setattr(service, 'listing_coordinates', native)
    monkeypatch.setattr(service, 'geocode', fallback)
    cache = {}
    for _ in range(2):
        result = await service.resolve_property_coordinates(
            {'url_origen': 'https://www.remax.com.ar/listings/example'},
            address='Calle 461 200, City Bell', client=AsyncMock(),
            viewbox=service.LP_VIEWBOX, cache=cache,
        )
        assert result == (-34.864257, -58.059611)
    native.assert_awaited_once()
    fallback.assert_not_called()


async def test_native_point_outside_advertised_region_is_rejected(monkeypatch):
    monkeypatch.setattr(service, 'listing_coordinates', AsyncMock(return_value=(-38.08, -57.61)))
    monkeypatch.setattr(service, 'geocode', AsyncMock(return_value=None))
    assert await service.resolve_property_coordinates(
        {}, address='Calle 461 entre 14 y 14a, City Bell', client=AsyncMock(),
        viewbox=service.LP_VIEWBOX,
    ) is None


async def test_named_entre_rios_street_is_not_rejected_as_between_streets():
    candidate = {
        'lat': '-31.733', 'lon': '-60.529',
        'address': {
            'road': 'Entre Ríos', 'house_number': '450',
            'city': 'Paraná', 'country_code': 'ar',
        },
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json=[candidate]),
    )) as client:
        assert await service.geocode('Entre Ríos 450, Paraná', client=client) == (-31.733, -60.529)


@pytest.mark.parametrize('address', [
    'La Plata 500, Córdoba', 'Romero 100, Palermo', '10 100, Villa Elisa, Entre Ríos',
])
def test_street_names_and_homonyms_do_not_force_la_plata_bounds(address):
    assert service._viewbox_for_properties({'direccion': address}) == service.BA_VIEWBOX


class _Query:
    def __init__(self, sb, table):
        self.sb, self.table = sb, table
        self.filters = []
        self.patch = None

    def select(self, *_args):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def update(self, patch):
        self.patch = patch
        return self

    async def execute(self):
        if self.patch:
            self.sb.updates.append((self.filters, self.patch))
            return SimpleNamespace(data=[])
        self.sb.reads.append((self.table, self.filters))
        if self.table == 'search_property_results':
            return SimpleNamespace(data=[{'properties': self.sb.row}])
        return SimpleNamespace(data=[self.sb.row])  # both paths can link the same property


class _DB:
    def __init__(self):
        self.reads, self.updates = [], []
        self.row = {
            'id': 'property', 'direccion': '461 E/ 14 y 14a 200',
            'titulo': 'Casa en City Bell', 'lat': -38.0855, 'lng': -57.6184,
            'geocoded_at': '2026-07-29',
        }

    def table(self, table):
        return _Query(self, table)


@pytest.mark.parametrize('dry_run', [True, False])
async def test_job_recheck_includes_cached_coordinates_and_is_scoped(monkeypatch, dry_run):
    sb = _DB()
    resolver = AsyncMock(return_value=(-34.864257, -58.059611))
    monkeypatch.setattr(service, 'resolve_property_coordinates', resolver)
    monkeypatch.setattr(service, 'RATE_LIMIT_SECONDS', 0)
    state = await service.run_backfill(sb, job_id='job', recheck=True, dry_run=dry_run)
    assert state['processed'] == 1
    assert state['changes'] == [{
        'id': 'property', 'before': [-38.0855, -57.6184],
        'after': [-34.864257, -58.059611],
    }]
    assert sb.reads == [
        ('search_property_results', [('job_id', 'job')]),
        ('properties', [('scraping_job_id', 'job')]),
    ]
    assert len(sb.updates) == (0 if dry_run else 1)
    if not dry_run:
        filters, patch = sb.updates[0]
        assert ('direccion', sb.row['direccion']) in filters
        assert (patch['lat'], patch['lng']) == (-34.864257, -58.059611)
    resolver.assert_awaited_once()


async def test_recheck_requires_a_search_scope():
    with pytest.raises(ValueError, match='job_id'):
        await service.run_backfill(_DB(), recheck=True)


async def test_recheck_does_not_overwrite_cached_point_on_transient_failure(monkeypatch):
    sb = _DB()
    monkeypatch.setattr(service, 'resolve_property_coordinates', AsyncMock(
        side_effect=service.TransientGeocodeError('HTTP 503'),
    ))
    monkeypatch.setattr(service, 'THROTTLE_BACKOFF_SECONDS', 0)
    state = await service.run_backfill(sb, job_id='job', recheck=True)
    assert state['aborted']
    assert sb.updates == []
