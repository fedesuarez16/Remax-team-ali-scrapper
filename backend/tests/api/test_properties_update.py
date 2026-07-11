"""Test-first for `PATCH /api/v1/properties/{property_id}` — manual ficha edits.

Google Maps scraping sometimes harvests junk images (agency logos, banners),
so the ficha needs manual curation: replace `imagenes`, fix `titulo`, etc.
The endpoint must whitelist editable fields and reject payloads that carry
nothing editable or a malformed `imagenes` list.

Fake `app.state.supabase` mirrors the fluent
`.table(name).update(patch).eq(...).execute()` chain used by the route.
"""
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


class _Res:
    def __init__(self, data) -> None:
        self.data = data


class _FakeUpdateQuery:
    def __init__(self, store: dict, patch: dict) -> None:
        self._store = store
        self._patch = patch
        self._filters: dict = {}

    def eq(self, key: str, value) -> '_FakeUpdateQuery':
        self._filters[key] = value
        return self

    async def execute(self) -> _Res:
        row = self._store.get(self._filters.get('id'))
        if row is None:
            return _Res([])
        row.update(self._patch)
        return _Res([row])


class _FakeTable:
    def __init__(self, store: dict) -> None:
        self._store = store

    def update(self, patch: dict) -> _FakeUpdateQuery:
        return _FakeUpdateQuery(self._store, patch)


class _FakeSupabase:
    def __init__(self, properties: dict) -> None:
        self._properties = properties
        self.patches: list[dict] = []

    def table(self, name: str) -> _FakeTable:
        assert name == 'properties'
        return _FakeTable(self._properties)


def _make_app(fake_sb: _FakeSupabase) -> FastAPI:
    from app.api.v1 import properties

    app = FastAPI()
    # The router declares a root '' path, so it must be mounted under a prefix
    # (as router.py does in the real app).
    app.include_router(properties.router, prefix='/properties')
    app.state.supabase = fake_sb
    return app


def _client(fake_sb: _FakeSupabase) -> AsyncClient:
    transport = ASGITransport(app=_make_app(fake_sb))
    return AsyncClient(transport=transport, base_url='http://test/properties')


async def test_patch_updates_editable_fields() -> None:
    fake_sb = _FakeSupabase({
        'p1': {'id': 'p1', 'titulo': 'Depto', 'imagenes': ['https://cdn/logo.png', 'https://cdn/frente.jpg']},
    })
    async with _client(fake_sb) as client:
        resp = await client.patch('/p1', json={
            'titulo': 'Depto 2 amb en Palermo',
            'imagenes': ['https://cdn/frente.jpg'],
        })

    assert resp.status_code == 200
    prop = resp.json()['property']
    assert prop['titulo'] == 'Depto 2 amb en Palermo'
    assert prop['imagenes'] == ['https://cdn/frente.jpg']


async def test_patch_ignores_non_editable_fields() -> None:
    fake_sb = _FakeSupabase({
        'p1': {'id': 'p1', 'fuente': 'googlemaps', 'created_at': 'x', 'titulo': 'Depto'},
    })
    async with _client(fake_sb) as client:
        resp = await client.patch('/p1', json={
            'titulo': 'Editado',
            'id': 'hacked',
            'fuente': 'zonaprop',
            'created_at': '2020-01-01',
        })

    assert resp.status_code == 200
    prop = resp.json()['property']
    assert prop['titulo'] == 'Editado'
    assert prop['id'] == 'p1'
    assert prop['fuente'] == 'googlemaps'
    assert prop['created_at'] == 'x'


async def test_patch_only_non_editable_fields_returns_400() -> None:
    fake_sb = _FakeSupabase({'p1': {'id': 'p1'}})
    async with _client(fake_sb) as client:
        resp = await client.patch('/p1', json={'id': 'hacked', 'fuente': 'zonaprop'})

    assert resp.status_code == 400


async def test_patch_malformed_imagenes_returns_400() -> None:
    fake_sb = _FakeSupabase({'p1': {'id': 'p1', 'imagenes': []}})
    async with _client(fake_sb) as client:
        resp = await client.patch('/p1', json={'imagenes': 'https://cdn/una.jpg'})
        assert resp.status_code == 400

        resp = await client.patch('/p1', json={'imagenes': [1, 2]})
        assert resp.status_code == 400


async def test_patch_missing_property_returns_404() -> None:
    fake_sb = _FakeSupabase({})
    async with _client(fake_sb) as client:
        resp = await client.patch('/nope', json={'titulo': 'X'})

    assert resp.status_code == 404
