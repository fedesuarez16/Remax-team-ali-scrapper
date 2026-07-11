"""Test-first for `POST /api/v1/geo/reverse-zonas` (T-1.1) — written BEFORE
the route exists in `app.api.v1.geo`, so this file MUST fail (404s) until
T-1.2 lands.

Mounts only `geo.router` on a bare `FastAPI()` — stateless, no settings/
supabase dependency needed. Monkeypatches `app.api.v1.geo.reverse_geocode`
(the import site used by the endpoint module), not
`app.services.geocode.reverse_geocode` — mirrors `test_geo_reverse.py`.
"""
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.services.geocode import TransientGeocodeError


def _make_app() -> FastAPI:
    from app.api.v1 import geo

    app = FastAPI()
    app.include_router(geo.router)
    return app


async def test_reverse_zonas_resolves_and_dedups_two_points(monkeypatch) -> None:
    from app.api.v1 import geo

    calls = []

    async def fake_reverse_geocode_pair(lat, lng, *, client, zoom=14):
        calls.append((lat, lng))
        if lat == -34.58:
            return 'Palermo', 'CABA'
        return 'Villa Crespo', 'CABA'

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(geo, 'reverse_geocode_pair', fake_reverse_geocode_pair)
    monkeypatch.setattr(geo.asyncio, 'sleep', fake_sleep)
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.post('/reverse-zonas', json={
            'points': [
                {'lat': -34.58, 'lng': -58.43},
                {'lat': -34.60, 'lng': -58.44},
            ],
        })

    assert resp.status_code == 200
    data = resp.json()
    assert set(data['zonas']) == {'Palermo', 'Villa Crespo'}
    assert data['resolved'] == 2
    assert data['requested'] == 2
    assert data['failed'] == 0


# ── localidades (T-3.1/3.2): additive field, deduped independently ───────────

async def test_reverse_zonas_returns_deduped_localidades(monkeypatch) -> None:
    from app.api.v1 import geo

    async def fake_reverse_geocode_pair(lat, lng, *, client, zoom=14):
        if lat == -34.58:
            return 'Palermo', 'Ciudad Autónoma de Buenos Aires'
        return 'Chacarita', 'ciudad autonoma de Buenos Aires'

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(geo, 'reverse_geocode_pair', fake_reverse_geocode_pair)
    monkeypatch.setattr(geo.asyncio, 'sleep', fake_sleep)
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.post('/reverse-zonas', json={
            'points': [
                {'lat': -34.58, 'lng': -58.43},
                {'lat': -34.60, 'lng': -58.44},
            ],
        })

    assert resp.status_code == 200
    data = resp.json()
    assert set(data['zonas']) == {'Palermo', 'Chacarita'}
    assert data['localidades'] == ['Ciudad Autónoma de Buenos Aires']


async def test_reverse_zonas_failed_point_excluded_from_zonas_and_localidades(monkeypatch) -> None:
    from app.api.v1 import geo

    responses = iter([TransientGeocodeError('HTTP 429'), ('Belgrano', 'CABA')])

    async def fake_reverse_geocode_pair(lat, lng, *, client, zoom=14):
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(geo, 'reverse_geocode_pair', fake_reverse_geocode_pair)
    monkeypatch.setattr(geo.asyncio, 'sleep', fake_sleep)
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.post('/reverse-zonas', json={
            'points': [
                {'lat': -34.58, 'lng': -58.43},
                {'lat': -34.56, 'lng': -58.45},
            ],
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data['failed'] == 1
    assert data['zonas'] == ['Belgrano']
    assert data['localidades'] == ['CABA']


async def test_reverse_zonas_all_transient_failures_returns_503_with_localidades(monkeypatch) -> None:
    from app.api.v1 import geo

    async def fake_reverse_geocode_pair(lat, lng, *, client, zoom=14):
        raise TransientGeocodeError('HTTP 429')

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(geo, 'reverse_geocode_pair', fake_reverse_geocode_pair)
    monkeypatch.setattr(geo.asyncio, 'sleep', fake_sleep)
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.post('/reverse-zonas', json={
            'points': [
                {'lat': -34.58, 'lng': -58.43},
                {'lat': -34.56, 'lng': -58.45},
            ],
        })

    assert resp.status_code == 503


async def test_reverse_zonas_dedup_keeps_first_seen_surface_name(monkeypatch) -> None:
    from app.api.v1 import geo

    responses = iter([('Palermo', 'CABA'), ('palermo, CABA', 'caba')])

    async def fake_reverse_geocode_pair(lat, lng, *, client, zoom=14):
        return next(responses)

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(geo, 'reverse_geocode_pair', fake_reverse_geocode_pair)
    monkeypatch.setattr(geo.asyncio, 'sleep', fake_sleep)
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.post('/reverse-zonas', json={
            'points': [
                {'lat': -34.58, 'lng': -58.43},
                {'lat': -34.581, 'lng': -58.431},
            ],
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data['zonas'] == ['Palermo']


async def test_reverse_zonas_none_result_excluded_and_unresolved_counted(monkeypatch) -> None:
    from app.api.v1 import geo

    responses = iter([('Palermo', 'CABA'), (None, None)])

    async def fake_reverse_geocode_pair(lat, lng, *, client, zoom=14):
        return next(responses)

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(geo, 'reverse_geocode_pair', fake_reverse_geocode_pair)
    monkeypatch.setattr(geo.asyncio, 'sleep', fake_sleep)
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.post('/reverse-zonas', json={
            'points': [
                {'lat': -34.58, 'lng': -58.43},
                {'lat': 0.0, 'lng': 0.0},
            ],
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data['zonas'] == ['Palermo']
    assert data['resolved'] < data['requested']


async def test_reverse_zonas_partial_transient_failure_still_succeeds(monkeypatch) -> None:
    from app.api.v1 import geo

    responses = iter([TransientGeocodeError('HTTP 429'), ('Belgrano', 'CABA')])

    async def fake_reverse_geocode_pair(lat, lng, *, client, zoom=14):
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(geo, 'reverse_geocode_pair', fake_reverse_geocode_pair)
    monkeypatch.setattr(geo.asyncio, 'sleep', fake_sleep)
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.post('/reverse-zonas', json={
            'points': [
                {'lat': -34.58, 'lng': -58.43},
                {'lat': -34.56, 'lng': -58.45},
            ],
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data['failed'] == 1
    assert 'Belgrano' in data['zonas']


async def test_reverse_zonas_all_transient_failures_returns_503(monkeypatch) -> None:
    from app.api.v1 import geo

    async def fake_reverse_geocode_pair(lat, lng, *, client, zoom=14):
        raise TransientGeocodeError('HTTP 429')

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(geo, 'reverse_geocode_pair', fake_reverse_geocode_pair)
    monkeypatch.setattr(geo.asyncio, 'sleep', fake_sleep)
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.post('/reverse-zonas', json={
            'points': [
                {'lat': -34.58, 'lng': -58.43},
                {'lat': -34.56, 'lng': -58.45},
            ],
        })

    assert resp.status_code == 503
    data = resp.json()
    assert 'detail' in data
    assert 'HTTP 429' not in data['detail']


async def test_reverse_zonas_all_none_returns_200_empty_zonas(monkeypatch) -> None:
    from app.api.v1 import geo

    async def fake_reverse_geocode_pair(lat, lng, *, client, zoom=14):
        return None, None

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(geo, 'reverse_geocode_pair', fake_reverse_geocode_pair)
    monkeypatch.setattr(geo.asyncio, 'sleep', fake_sleep)
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.post('/reverse-zonas', json={
            'points': [
                {'lat': -34.58, 'lng': -58.43},
                {'lat': -34.56, 'lng': -58.45},
            ],
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data['zonas'] == []


async def test_reverse_zonas_empty_points_returns_422() -> None:
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.post('/reverse-zonas', json={'points': []})

    assert resp.status_code == 422


async def test_reverse_zonas_out_of_range_lat_returns_422() -> None:
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.post('/reverse-zonas', json={
            'points': [
                {'lat': 200.0, 'lng': -58.43},
            ],
        })

    assert resp.status_code == 422


async def test_reverse_zonas_sequential_throttle_timing(monkeypatch) -> None:
    from app.api.v1 import geo

    sleep_calls = []

    async def fake_reverse_geocode_pair(lat, lng, *, client, zoom=14):
        return 'Palermo', 'CABA'

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(geo, 'reverse_geocode_pair', fake_reverse_geocode_pair)
    monkeypatch.setattr(geo.asyncio, 'sleep', fake_sleep)
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.post('/reverse-zonas', json={
            'points': [
                {'lat': -34.58, 'lng': -58.43},
                {'lat': -34.56, 'lng': -58.45},
                {'lat': -34.57, 'lng': -58.46},
            ],
        })

    assert resp.status_code == 200
    assert len(sleep_calls) == 2
