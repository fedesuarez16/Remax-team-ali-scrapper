"""Test-first for `POST /api/v1/properties/import` — Ficha Propio.

The user pastes property links from any portal (Zonaprop, Argenprop, a RE/MAX
office site, etc.); the backend fetches each page, extracts the property with
an LLM, harvests the photo gallery and stores it in `properties` with
`fuente='manual'` so a branded, shareable ficha (/p/{id}) can replace the
portal link.

Network fetch and LLM extraction are monkeypatched at the importer seams
(`_fetch_page`, `_extract_llm`, `harvest_page_images`); Supabase is faked with
the same fluent-chain style used by the other API tests.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.services import importer


class _Res:
    def __init__(self, data: Any) -> None:
        self.data = data


class _FakeSelectQuery:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self._filters: dict = {}

    def eq(self, key: str, value: Any) -> '_FakeSelectQuery':
        self._filters[key] = value
        return self

    def limit(self, n: int) -> '_FakeSelectQuery':
        self._limit = n
        return self

    async def execute(self) -> _Res:
        rows = [
            r for r in self._rows
            if all(r.get(k) == v for k, v in self._filters.items())
        ]
        return _Res(rows[: getattr(self, '_limit', len(rows))])


class _FakeInsertQuery:
    def __init__(self, store: list[dict], row: dict) -> None:
        self._store = store
        self._row = row

    async def execute(self) -> _Res:
        saved = {**self._row, 'id': f'p{len(self._store) + 1}'}
        self._store.append(saved)
        return _Res([saved])


class _FakeTable:
    def __init__(self, store: list[dict]) -> None:
        self._store = store

    def select(self, *_cols: str, **_kw: Any) -> _FakeSelectQuery:
        return _FakeSelectQuery(self._store)

    def insert(self, row: dict) -> _FakeInsertQuery:
        return _FakeInsertQuery(self._store, row)


class _FakeSupabase:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []

    def table(self, name: str) -> _FakeTable:
        assert name == 'properties'
        return _FakeTable(self.rows)


def _make_client(fake_sb: _FakeSupabase) -> AsyncClient:
    from app.api.v1 import properties

    app = FastAPI()
    app.include_router(properties.router, prefix='/properties')
    app.state.supabase = fake_sb
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url='http://test/properties')


_EXTRACTED = {
    'titulo': 'Depto 3 amb en Palermo',
    'precio': 189000,
    'moneda': 'USD',
    'tipo_operacion': 'venta',
    'tipo_propiedad': 'departamento',
    'ambientes': 3,
    'banos': 1,
    'direccion': 'Guatemala 4500, Palermo',
    'descripcion': 'Luminoso, con balcón.',
    'm2': 72,
}


@pytest.fixture
def patched_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(url: str) -> tuple[str, list[str]]:
        return 'texto de la ficha ' * 20, [f'{url}/foto1.jpg', f'{url}/foto2.jpg']

    async def fake_llm(url: str, text: str) -> dict[str, Any] | None:
        return dict(_EXTRACTED)

    async def fake_harvest(urls: list[str], render_budget: int = 8) -> dict[str, list[str]]:
        return {u: [f'{u}/g{i}.jpg' for i in range(6)] for u in urls}

    monkeypatch.setattr(importer, '_fetch_page', fake_fetch)
    monkeypatch.setattr(importer, '_extract_llm', fake_llm)
    monkeypatch.setattr(importer, 'harvest_page_images', fake_harvest)


async def test_import_creates_property_with_fuente_manual(patched_seams: None) -> None:
    fake_sb = _FakeSupabase()
    async with _make_client(fake_sb) as client:
        resp = await client.post('/import', json={'urls': ['https://portal.com/ficha/1']})
    assert resp.status_code == 200
    results = resp.json()['results']
    assert len(results) == 1
    r = results[0]
    assert r['status'] == 'ok'
    assert r['created'] is True
    prop = r['property']
    assert prop['id']
    assert prop['fuente'] == 'manual'
    assert prop['url_origen'] == 'https://portal.com/ficha/1'
    assert prop['titulo'] == 'Depto 3 amb en Palermo'
    assert prop['precio'] == 189000
    assert prop['direccion'] == 'Guatemala 4500, Palermo'
    # under 4 httpx images → gallery harvested via headless pass
    assert len(prop['imagenes']) == 6


async def test_import_is_idempotent_by_url_origen(patched_seams: None) -> None:
    existing = {
        'id': 'p1', 'titulo': 'Ya importada', 'fuente': 'manual',
        'url_origen': 'https://portal.com/ficha/1',
    }
    fake_sb = _FakeSupabase([existing])
    async with _make_client(fake_sb) as client:
        resp = await client.post('/import', json={'urls': ['https://portal.com/ficha/1']})
    r = resp.json()['results'][0]
    assert r['status'] == 'ok'
    assert r['created'] is False
    assert r['property']['id'] == 'p1'
    assert len(fake_sb.rows) == 1  # no duplicate insert


async def test_import_isolates_per_url_failures(
    patched_seams: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def flaky_fetch(url: str) -> tuple[str, list[str]]:
        if 'mala' in url:
            raise RuntimeError('timeout')
        return 'texto de la ficha ' * 20, []

    monkeypatch.setattr(importer, '_fetch_page', flaky_fetch)
    fake_sb = _FakeSupabase()
    async with _make_client(fake_sb) as client:
        resp = await client.post('/import', json={
            'urls': ['https://portal.com/mala', 'https://portal.com/buena'],
        })
    results = resp.json()['results']
    assert results[0]['status'] == 'error'
    assert results[1]['status'] == 'ok'


async def test_import_rejects_empty_and_oversized_payloads(patched_seams: None) -> None:
    fake_sb = _FakeSupabase()
    async with _make_client(fake_sb) as client:
        empty = await client.post('/import', json={'urls': []})
        too_many = await client.post('/import', json={
            'urls': [f'https://portal.com/{i}' for i in range(11)],
        })
    assert empty.status_code == 400
    assert too_many.status_code == 400


async def test_import_rejects_non_http_urls(patched_seams: None) -> None:
    fake_sb = _FakeSupabase()
    async with _make_client(fake_sb) as client:
        resp = await client.post('/import', json={'urls': ['ftp://x', 'javascript:alert(1)']})
    results = resp.json()['results']
    assert all(r['status'] == 'error' for r in results)
    assert fake_sb.rows == []


async def test_import_errors_when_page_has_no_property(
    patched_seams: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_prop(url: str, text: str) -> dict[str, Any] | None:
        return None

    monkeypatch.setattr(importer, '_extract_llm', no_prop)
    fake_sb = _FakeSupabase()
    async with _make_client(fake_sb) as client:
        resp = await client.post('/import', json={'urls': ['https://portal.com/home']})
    r = resp.json()['results'][0]
    assert r['status'] == 'error'
    assert fake_sb.rows == []
