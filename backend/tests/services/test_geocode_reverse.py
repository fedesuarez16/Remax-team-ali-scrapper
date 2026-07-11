"""Test-first for `reverse_geocode()` (T-2.1) — written BEFORE the
implementation lands (T-2.2), so this file MUST fail on collection/import
until `app.services.geocode.reverse_geocode` exists.
"""
import httpx
import pytest

from app.services.geocode import TransientGeocodeError, reverse_geocode, reverse_geocode_pair


def _client_for(handler) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


async def test_reverse_geocode_prefers_suburb_over_city() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            'address': {
                'suburb': 'Palermo',
                'city_district': 'Comuna 14',
                'city': 'Buenos Aires',
            },
        })

    async with _client_for(handler) as client:
        result = await reverse_geocode(-34.58, -58.43, client=client)
    assert result == 'Palermo'


async def test_reverse_geocode_falls_back_to_town() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            'address': {
                'town': 'City Bell',
                'city': 'La Plata',
            },
        })

    async with _client_for(handler) as client:
        result = await reverse_geocode(-34.9, -58.0, client=client)
    assert result == 'City Bell'


async def test_reverse_geocode_returns_none_for_empty_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async with _client_for(handler) as client:
        result = await reverse_geocode(0.0, 0.0, client=client)
    assert result is None


async def test_reverse_geocode_returns_none_when_no_address_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'display_name': 'Somewhere'})

    async with _client_for(handler) as client:
        result = await reverse_geocode(0.0, 0.0, client=client)
    assert result is None


async def test_reverse_geocode_raises_transient_on_429() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={'error': 'throttled'})

    async with _client_for(handler) as client:
        with pytest.raises(TransientGeocodeError):
            await reverse_geocode(-34.58, -58.43, client=client)


async def test_reverse_geocode_raises_transient_on_500() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text='boom')

    async with _client_for(handler) as client:
        with pytest.raises(TransientGeocodeError):
            await reverse_geocode(-34.58, -58.43, client=client)


async def test_reverse_geocode_returns_none_on_malformed_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'not json at all')

    async with _client_for(handler) as client:
        result = await reverse_geocode(-34.58, -58.43, client=client)
    assert result is None


# ── reverse_geocode_pair (T-1.1 / ADR-2): single call, (barrio, localidad) ────

async def test_reverse_geocode_pair_returns_barrio_and_localidad_from_one_call() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={
            'address': {
                'suburb': 'Palermo',
                'city_district': 'Comuna 14',
                'city': 'Ciudad Autónoma de Buenos Aires',
            },
        })

    async with _client_for(handler) as client:
        result = await reverse_geocode_pair(-34.58, -58.43, client=client)
    assert result == ('Palermo', 'Ciudad Autónoma de Buenos Aires')
    assert len(calls) == 1


async def test_reverse_geocode_pair_barrio_and_localidad_are_independent_keys() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            'address': {
                'suburb': 'Los Hornos',
                'municipality': 'La Plata',
            },
        })

    async with _client_for(handler) as client:
        result = await reverse_geocode_pair(-34.9, -58.0, client=client)
    assert result == ('Los Hornos', 'La Plata')


async def test_reverse_geocode_pair_town_only_used_for_both_when_no_coarser_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            'address': {'town': 'City Bell'},
        })

    async with _client_for(handler) as client:
        result = await reverse_geocode_pair(-34.9, -58.0, client=client)
    assert result == ('City Bell', 'City Bell')


async def test_reverse_geocode_pair_none_when_no_address_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'display_name': 'Somewhere'})

    async with _client_for(handler) as client:
        result = await reverse_geocode_pair(0.0, 0.0, client=client)
    assert result == (None, None)


async def test_reverse_geocode_pair_raises_transient_on_429() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={'error': 'throttled'})

    async with _client_for(handler) as client:
        with pytest.raises(TransientGeocodeError):
            await reverse_geocode_pair(-34.58, -58.43, client=client)


# ── partido disambiguation: localidad becomes "Localidad, Partido" so portal
# slugs resolve the RIGHT Villa Elisa (La Plata, not Entre Ríos). ──────────────

async def test_reverse_geocode_pair_appends_partido_to_localidad() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            'address': {
                'town': 'Villa Elisa',
                'county': 'Partido de La Plata',
                'state': 'Buenos Aires',
            },
        })

    async with _client_for(handler) as client:
        result = await reverse_geocode_pair(-34.85, -58.06, client=client)
    assert result == ('Villa Elisa', 'Villa Elisa, La Plata')


async def test_reverse_geocode_pair_skips_partido_equal_to_localidad() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            'address': {
                'city': 'La Plata',
                'county': 'Partido de La Plata',
            },
        })

    async with _client_for(handler) as client:
        result = await reverse_geocode_pair(-34.92, -57.95, client=client)
    assert result == ('La Plata', 'La Plata')


async def test_reverse_geocode_pair_strips_departamento_prefix() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            'address': {
                'town': 'Villa Elisa',
                'county': 'Departamento Colón',
                'state': 'Entre Ríos',
            },
        })

    async with _client_for(handler) as client:
        result = await reverse_geocode_pair(-32.16, -58.4, client=client)
    assert result == ('Villa Elisa', 'Villa Elisa, Colón')


async def test_reverse_geocode_pair_no_partido_key_leaves_localidad_plain() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            'address': {'suburb': 'Palermo', 'city': 'Ciudad Autónoma de Buenos Aires'},
        })

    async with _client_for(handler) as client:
        result = await reverse_geocode_pair(-34.58, -58.43, client=client)
    assert result == ('Palermo', 'Ciudad Autónoma de Buenos Aires')


async def test_reverse_geocode_wrapper_still_returns_barrio_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            'address': {'suburb': 'Palermo', 'city': 'Ciudad Autónoma de Buenos Aires'},
        })

    async with _client_for(handler) as client:
        result = await reverse_geocode(-34.58, -58.43, client=client)
    assert result == 'Palermo'
