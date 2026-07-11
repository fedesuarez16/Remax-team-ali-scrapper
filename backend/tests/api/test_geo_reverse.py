"""Test-first for `POST /api/v1/geo/reverse` (T-3.1) — written BEFORE
`app.api.v1.geo` exists, so this file MUST fail on collection/import until
T-3.2 lands.

Mounts only `geo.router` on a bare `FastAPI()` — stateless, no settings/
supabase dependency needed. Monkeypatches `app.api.v1.geo.reverse_geocode`
(the import site used by the endpoint module), not
`app.services.geocode.reverse_geocode`.
"""
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.services.geocode import TransientGeocodeError


def _make_app() -> FastAPI:
    from app.api.v1 import geo

    app = FastAPI()
    app.include_router(geo.router)
    return app


async def test_reverse_geocode_endpoint_resolves_zona(monkeypatch) -> None:
    from app.api.v1 import geo

    async def fake_reverse_geocode(lat, lng, *, client, zoom=14):
        return 'Palermo'

    monkeypatch.setattr(geo, 'reverse_geocode', fake_reverse_geocode)
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.post('/reverse', json={'lat': -34.58, 'lng': -58.43})

    assert resp.status_code == 200
    data = resp.json()
    assert data['zona'] == 'Palermo'
    assert data['display_name'] == 'Palermo'
    assert data['lat'] == -34.58
    assert data['lng'] == -58.43


async def test_reverse_geocode_endpoint_unresolved(monkeypatch) -> None:
    from app.api.v1 import geo

    async def fake_reverse_geocode(lat, lng, *, client, zoom=14):
        return None

    monkeypatch.setattr(geo, 'reverse_geocode', fake_reverse_geocode)
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.post('/reverse', json={'lat': 0.0, 'lng': 0.0})

    assert resp.status_code == 200
    data = resp.json()
    assert data['zona'] is None
    assert data['display_name'] is None


async def test_reverse_geocode_endpoint_transient_error_returns_503(monkeypatch) -> None:
    from app.api.v1 import geo

    async def fake_reverse_geocode(lat, lng, *, client, zoom=14):
        raise TransientGeocodeError('HTTP 429')

    monkeypatch.setattr(geo, 'reverse_geocode', fake_reverse_geocode)
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.post('/reverse', json={'lat': -34.58, 'lng': -58.43})

    assert resp.status_code == 503
    data = resp.json()
    assert 'detail' in data
    assert 'HTTP 429' not in data['detail']


async def test_reverse_geocode_endpoint_missing_lat_returns_422() -> None:
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.post('/reverse', json={'lng': -58.43})

    assert resp.status_code == 422


async def test_reverse_geocode_endpoint_out_of_range_lat_returns_422() -> None:
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.post('/reverse', json={'lat': 200.0, 'lng': -58.43})

    assert resp.status_code == 422
