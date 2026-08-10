"""Test-first for `GET/POST/PATCH/DELETE /api/v1/saved-zones` — named zone
delineations (polygons only, NO search results) that the map can re-draw with
one click.

Deliberately separate from `search_history`: a history entry is "this search
ran, here are its results"; a saved zone is just geometry + a name, reusable
across searches and never tied to a job.

Fake `app.state.supabase` mirrors the fluent `.table(name).select(...)/
.insert(...)/.update(...)/.delete(...).eq/.order(...).execute()` chain, same
pattern as `test_manual_sources.py`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

POLY = [[-34.60, -58.38], [-34.61, -58.38], [-34.61, -58.39]]


class _Res:
    def __init__(self, data) -> None:
        self.data = data


class _FakeQuery:
    def __init__(self, store: list[dict], mode: str) -> None:
        self._store = store
        self._mode = mode  # 'select' | 'insert' | 'update' | 'delete'
        self._filters: list[tuple[str, str, object]] = []
        self._order_field: str | None = None
        self._order_desc = False
        self._payload: dict | None = None

    def select(self, *_a, **_kw) -> '_FakeQuery':
        return self

    def insert(self, payload: dict) -> '_FakeQuery':
        self._payload = payload
        return self

    def update(self, payload: dict) -> '_FakeQuery':
        self._payload = payload
        return self

    def delete(self) -> '_FakeQuery':
        return self

    def eq(self, field: str, value) -> '_FakeQuery':
        self._filters.append(('eq', field, value))
        return self

    def order(self, field: str, desc: bool = False) -> '_FakeQuery':
        self._order_field = field
        self._order_desc = desc
        return self

    def _match(self, row: dict) -> bool:
        for op, field, value in self._filters:
            if op == 'eq' and row.get(field) != value:
                return False
        return True

    async def execute(self) -> _Res:
        if self._mode == 'insert':
            row = dict(self._payload or {})
            row.setdefault('id', f'id-{uuid.uuid4().hex[:8]}')
            row.setdefault('created_at', datetime.now(timezone.utc).isoformat())
            self._store.append(row)
            return _Res([row])

        if self._mode == 'update':
            matched = [r for r in self._store if self._match(r)]
            for r in matched:
                r.update(self._payload or {})
            return _Res(matched)

        if self._mode == 'delete':
            matched = [r for r in self._store if self._match(r)]
            for r in matched:
                self._store.remove(r)
            return _Res(matched)

        rows = [r for r in self._store if self._match(r)]
        if self._order_field:
            rows = sorted(rows, key=lambda r: r[self._order_field], reverse=self._order_desc)
        return _Res(rows)


class _FakeTable:
    def __init__(self, store: list[dict]) -> None:
        self._store = store

    def select(self, *a, **kw) -> _FakeQuery:
        return _FakeQuery(self._store, 'select').select(*a, **kw)

    def insert(self, payload: dict) -> _FakeQuery:
        return _FakeQuery(self._store, 'insert').insert(payload)

    def update(self, payload: dict) -> _FakeQuery:
        return _FakeQuery(self._store, 'update').update(payload)

    def delete(self) -> _FakeQuery:
        return _FakeQuery(self._store, 'delete')


class _FakeSupabase:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self._store: list[dict] = rows or []

    def table(self, name: str) -> _FakeTable:
        if name == 'saved_zones':
            return _FakeTable(self._store)
        raise AssertionError(f'unexpected table {name}')


class _RaisingTable:
    def select(self, *_a, **_kw):
        raise RuntimeError('boom')

    def insert(self, *_a, **_kw):
        raise RuntimeError('boom')

    def update(self, *_a, **_kw):
        raise RuntimeError('boom')

    def delete(self, *_a, **_kw):
        raise RuntimeError('boom')


class _RaisingSupabase:
    def table(self, name: str) -> _RaisingTable:
        return _RaisingTable()


def _make_app(fake_sb) -> FastAPI:
    from app.api.v1 import saved_zones

    app = FastAPI()
    app.include_router(saved_zones.router, prefix='/saved-zones')
    app.state.supabase = fake_sb
    return app


def _client(fake_sb) -> AsyncClient:
    # NOTE: base_url deliberately has no trailing path segment — see
    # test_search_history.py for why (avoids a 307 redirect on the bare route).
    transport = ASGITransport(app=_make_app(fake_sb))
    return AsyncClient(transport=transport, base_url='http://test')


def _row(name: str, *, minutes_ago: int, polygon: list | None = None) -> dict:
    created = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return {
        'id': f'id-{uuid.uuid4().hex[:8]}',
        'name': name,
        'polygon': polygon or POLY,
        'created_at': created.isoformat(),
    }


# -- GET (list) -----------------------------------------------------------


async def test_list_returns_zones_most_recent_first() -> None:
    fake_sb = _FakeSupabase([
        _row('Belgrano R', minutes_ago=5),
        _row('Palermo Chico', minutes_ago=1),
    ])
    async with _client(fake_sb) as client:
        resp = await client.get('/saved-zones')

    assert resp.status_code == 200
    data = resp.json()
    assert data['total'] == 2
    assert [z['name'] for z in data['zones']] == ['Palermo Chico', 'Belgrano R']
    assert data['zones'][0]['polygon'] == POLY


async def test_list_when_supabase_not_configured() -> None:
    async with _client(None) as client:
        resp = await client.get('/saved-zones')

    assert resp.status_code == 200
    assert resp.json() == {'zones': [], 'total': 0, 'error': 'Supabase no configurado'}


async def test_list_failure_returns_error_without_raising() -> None:
    async with _client(_RaisingSupabase()) as client:
        resp = await client.get('/saved-zones')

    assert resp.status_code == 200
    data = resp.json()
    assert data['zones'] == []
    assert data['total'] == 0
    assert 'error' in data


# -- POST (create) ---------------------------------------------------------


async def test_post_creates_named_zone() -> None:
    fake_sb = _FakeSupabase([])
    async with _client(fake_sb) as client:
        resp = await client.post('/saved-zones', json={'name': '  Belgrano R  ', 'polygon': POLY})

    assert resp.status_code == 200
    zone = resp.json()['zone']
    assert zone['name'] == 'Belgrano R'  # trimmed
    assert zone['polygon'] == POLY
    assert len(fake_sb._store) == 1


async def test_post_empty_name_is_rejected() -> None:
    fake_sb = _FakeSupabase([])
    async with _client(fake_sb) as client:
        resp = await client.post('/saved-zones', json={'name': '   ', 'polygon': POLY})

    data = resp.json()
    assert data['zone'] is None
    assert 'error' in data
    assert fake_sb._store == []


async def test_post_polygon_with_less_than_three_vertices_is_rejected() -> None:
    fake_sb = _FakeSupabase([])
    async with _client(fake_sb) as client:
        resp = await client.post('/saved-zones', json={'name': 'Linea', 'polygon': POLY[:2]})

    data = resp.json()
    assert data['zone'] is None
    assert 'error' in data
    assert fake_sb._store == []


async def test_post_malformed_vertices_are_rejected() -> None:
    """A vertex must be a numeric [lat, lng] pair — anything else is garbage
    geometry that would break the map's Polygon rendering."""
    fake_sb = _FakeSupabase([])
    bad = [[-34.6, -58.3], ['x', -58.3], [-34.61, -58.39]]
    async with _client(fake_sb) as client:
        resp = await client.post('/saved-zones', json={'name': 'Rota', 'polygon': bad})

    data = resp.json()
    assert data['zone'] is None
    assert 'error' in data
    assert fake_sb._store == []


async def test_post_out_of_range_coordinates_are_rejected() -> None:
    fake_sb = _FakeSupabase([])
    bad = [[-34.6, -58.3], [-91.0, -58.3], [-34.61, -58.39]]
    async with _client(fake_sb) as client:
        resp = await client.post('/saved-zones', json={'name': 'Fuera', 'polygon': bad})

    data = resp.json()
    assert data['zone'] is None
    assert 'error' in data
    assert fake_sb._store == []


async def test_post_normalizes_vertices_to_float_pairs() -> None:
    """Ints and tuples from the client come back as plain float pairs so the
    stored jsonb shape is stable for the map."""
    fake_sb = _FakeSupabase([])
    async with _client(fake_sb) as client:
        resp = await client.post('/saved-zones', json={
            'name': 'Enteros', 'polygon': [[-34, -58], [-35, -58], [-35, -59]],
        })

    zone = resp.json()['zone']
    assert zone['polygon'] == [[-34.0, -58.0], [-35.0, -58.0], [-35.0, -59.0]]


async def test_post_failure_returns_error_without_raising() -> None:
    async with _client(_RaisingSupabase()) as client:
        resp = await client.post('/saved-zones', json={'name': 'X', 'polygon': POLY})

    assert resp.status_code == 200
    data = resp.json()
    assert data['zone'] is None
    assert 'error' in data


# -- PATCH (rename / redraw) ----------------------------------------------


async def test_patch_renames_zone_without_touching_polygon() -> None:
    existing = _row('Sin nombre', minutes_ago=3)
    fake_sb = _FakeSupabase([existing])
    async with _client(fake_sb) as client:
        resp = await client.patch(f"/saved-zones/{existing['id']}", json={'name': 'Nuñez sur'})

    zone = resp.json()['zone']
    assert zone['name'] == 'Nuñez sur'
    assert zone['polygon'] == POLY


async def test_patch_updates_polygon() -> None:
    existing = _row('Belgrano R', minutes_ago=3)
    fake_sb = _FakeSupabase([existing])
    nuevo = [[-34.55, -58.45], [-34.56, -58.45], [-34.56, -58.46], [-34.55, -58.46]]
    async with _client(fake_sb) as client:
        resp = await client.patch(f"/saved-zones/{existing['id']}", json={'polygon': nuevo})

    zone = resp.json()['zone']
    assert zone['polygon'] == nuevo
    assert zone['name'] == 'Belgrano R'


async def test_patch_rejects_blank_name() -> None:
    existing = _row('Belgrano R', minutes_ago=3)
    fake_sb = _FakeSupabase([existing])
    async with _client(fake_sb) as client:
        resp = await client.patch(f"/saved-zones/{existing['id']}", json={'name': '  '})

    data = resp.json()
    assert data['zone'] is None
    assert 'error' in data
    assert fake_sb._store[0]['name'] == 'Belgrano R'


async def test_patch_rejects_invalid_polygon() -> None:
    existing = _row('Belgrano R', minutes_ago=3)
    fake_sb = _FakeSupabase([existing])
    async with _client(fake_sb) as client:
        resp = await client.patch(f"/saved-zones/{existing['id']}", json={'polygon': POLY[:2]})

    data = resp.json()
    assert data['zone'] is None
    assert 'error' in data
    assert fake_sb._store[0]['polygon'] == POLY


async def test_patch_unknown_id_returns_error() -> None:
    fake_sb = _FakeSupabase([])
    async with _client(fake_sb) as client:
        resp = await client.patch('/saved-zones/nope', json={'name': 'X'})

    data = resp.json()
    assert data['zone'] is None
    assert 'error' in data


# -- DELETE ----------------------------------------------------------------


async def test_delete_removes_zone() -> None:
    existing = _row('Belgrano R', minutes_ago=3)
    fake_sb = _FakeSupabase([existing])
    async with _client(fake_sb) as client:
        resp = await client.delete(f"/saved-zones/{existing['id']}")

    assert resp.json() == {'deleted': True}
    assert fake_sb._store == []


async def test_delete_is_idempotent() -> None:
    fake_sb = _FakeSupabase([])
    async with _client(fake_sb) as client:
        resp = await client.delete('/saved-zones/nope')

    assert resp.json() == {'deleted': True}


async def test_delete_failure_returns_error_without_raising() -> None:
    async with _client(_RaisingSupabase()) as client:
        resp = await client.delete('/saved-zones/x')

    data = resp.json()
    assert data['deleted'] is False
    assert 'error' in data
