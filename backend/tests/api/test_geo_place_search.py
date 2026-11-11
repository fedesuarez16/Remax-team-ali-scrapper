"""`GET /geo/search` and `GET /geo/place-geometry` — the /map search bar API.

Mounts only `geo.router` on a bare app and monkeypatches the service functions
at their import site in `app.api.v1.geo`.
"""
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.services.place_search import Place, PlaceGeometry, PlaceSearchError


def _make_app() -> FastAPI:
    from app.api.v1 import geo

    app = FastAPI()
    app.include_router(geo.router)
    return app


async def _get(path: str, params: dict) -> tuple[int, dict]:
    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.get(path, params=params)
    return resp.status_code, resp.json()


async def test_search_returns_places(monkeypatch) -> None:
    from app.api.v1 import geo

    async def fake_search(q, *, client, limit=8):
        assert q == 'tolosa'
        return [Place(
            id='R9', name='Tolosa', label='Tolosa, La Plata', kind='area',
            lat=-34.9, lng=-57.97, osm_type='R', osm_id=9,
        )]

    monkeypatch.setattr(geo, 'search_places', fake_search)
    status, data = await _get('/search', {'q': 'tolosa'})

    assert status == 200
    assert data['places'][0]['kind'] == 'area'
    assert data['places'][0]['label'] == 'Tolosa, La Plata'


async def test_search_rejects_too_short_query() -> None:
    status, _ = await _get('/search', {'q': 'a'})
    assert status == 422


async def test_search_maps_upstream_failure_to_503(monkeypatch) -> None:
    from app.api.v1 import geo

    async def boom(q, *, client, limit=8):
        raise PlaceSearchError('photon down')

    monkeypatch.setattr(geo, 'search_places', boom)
    status, _ = await _get('/search', {'q': 'tolosa'})
    assert status == 503


async def test_place_geometry_returns_lines(monkeypatch) -> None:
    from app.api.v1 import geo

    async def fake_geometry(kind, *, osm_type, osm_id, name, city, lat, lng, client):
        assert (kind, osm_type, osm_id, name) == ('street', 'W', 1, 'Calle 7')
        assert (lat, lng, city) == (-34.9, -57.9, 'La Plata')
        return PlaceGeometry(polygon=None, lines=[[(-34.9, -57.9), (-34.91, -57.91)]])

    monkeypatch.setattr(geo, 'place_geometry', fake_geometry)
    status, data = await _get('/place-geometry', {
        'kind': 'street', 'osm_type': 'W', 'osm_id': 1, 'name': 'Calle 7',
        'lat': -34.9, 'lng': -57.9, 'city': 'La Plata',
    })

    assert status == 200
    assert data == {'polygon': None, 'lines': [[[-34.9, -57.9], [-34.91, -57.91]]]}


async def test_place_geometry_rejects_unknown_kind() -> None:
    status, _ = await _get('/place-geometry', {
        'kind': 'planet', 'osm_type': 'W', 'osm_id': 1, 'name': 'x',
    })
    assert status == 422
