"""Test-first for the manually-curated zona classification on
`/api/v1/manual-sources`:

- `POST` accepts an optional `zona` and stores its normalized key `zona_norm`
  (same `normalize_zona` the agency cache uses) so lookups are accent- and
  qualifier-insensitive.
- `GET ?zona=City Bell` returns only that zona's inmobiliarias.
- `GET /zonas` lists the zonas that actually have sources loaded, with counts —
  this is what the search UI's "elegí la zona" step renders.

The classification is OURS, not inferred: `zona` is free text, whatever we
type when loading the inmobiliaria.

Reuses the fluent fake from `test_manual_sources.py`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.services.zona import normalize_zona

from tests.api.test_manual_sources import _FakeSupabase


def _make_app(fake_sb) -> FastAPI:
    from app.api.v1 import manual_sources

    app = FastAPI()
    app.include_router(manual_sources.router, prefix='/manual-sources')
    app.state.supabase = fake_sb
    return app


def _client(fake_sb) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=_make_app(fake_sb)), base_url='http://test')


def _row(nombre: str, url: str, zona: str | None, *, minutes_ago: int = 1) -> dict:
    created = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return {
        'id': f'id-{uuid.uuid4().hex[:8]}',
        'nombre': nombre,
        'url': url,
        'activo': True,
        'zona': zona,
        'zona_norm': normalize_zona(zona) if zona else None,
        'created_at': created.isoformat(),
    }


ROWS = [
    _row('Inmobiliaria A', 'https://a.com', 'City Bell', minutes_ago=5),
    _row('Inmobiliaria B', 'https://b.com', 'City Bell', minutes_ago=4),
    _row('Inmobiliaria D', 'https://d.com', 'Gonnet', minutes_ago=3),
    _row('Portal suelto', 'https://z.com', None, minutes_ago=2),
]


# ── POST with zona ────────────────────────────────────────────────────────────


async def test_post_stores_zona_and_its_normalized_key() -> None:
    fake_sb = _FakeSupabase([])
    async with _client(fake_sb) as client:
        resp = await client.post('/manual-sources', json={
            'nombre': 'Inmobiliaria A', 'url': 'https://a.com', 'zona': '  City Bell  ',
        })

    assert resp.status_code == 200
    source = resp.json()['source']
    assert source['zona'] == 'City Bell'
    assert source['zona_norm'] == normalize_zona('City Bell')


async def test_post_without_zona_leaves_it_null() -> None:
    fake_sb = _FakeSupabase([])
    async with _client(fake_sb) as client:
        resp = await client.post('/manual-sources', json={
            'nombre': 'Portal suelto', 'url': 'https://z.com',
        })

    assert resp.status_code == 200
    source = resp.json()['source']
    assert source['zona'] is None
    assert source['zona_norm'] is None


async def test_post_zona_normalization_is_qualifier_insensitive() -> None:
    fake_sb = _FakeSupabase([])
    async with _client(fake_sb) as client:
        await client.post('/manual-sources', json={
            'nombre': 'A', 'url': 'https://a.com', 'zona': 'City Bell',
        })
        await client.post('/manual-sources', json={
            'nombre': 'B', 'url': 'https://b.com', 'zona': 'city bell, Buenos Aires',
        })

    assert len({r['zona_norm'] for r in fake_sb._store}) == 1


# ── GET ?zona= ────────────────────────────────────────────────────────────────


async def test_list_filtered_by_zona_returns_only_that_zonas_sources() -> None:
    async with _client(_FakeSupabase(list(ROWS))) as client:
        resp = await client.get('/manual-sources', params={'zona': 'City Bell'})

    assert resp.status_code == 200
    data = resp.json()
    assert sorted(s['nombre'] for s in data['sources']) == ['Inmobiliaria A', 'Inmobiliaria B']
    assert data['total'] == 2


async def test_list_filtered_by_zona_is_normalized() -> None:
    async with _client(_FakeSupabase(list(ROWS))) as client:
        resp = await client.get('/manual-sources', params={'zona': 'CITY BELL, Buenos Aires'})

    assert sorted(s['nombre'] for s in resp.json()['sources']) == ['Inmobiliaria A', 'Inmobiliaria B']


async def test_list_without_zona_returns_every_source() -> None:
    async with _client(_FakeSupabase(list(ROWS))) as client:
        resp = await client.get('/manual-sources')

    assert resp.json()['total'] == 4


# ── GET /zonas ────────────────────────────────────────────────────────────────


async def test_zonas_lists_loaded_zonas_with_counts() -> None:
    async with _client(_FakeSupabase(list(ROWS))) as client:
        resp = await client.get('/manual-sources/zonas')

    assert resp.status_code == 200
    zonas = resp.json()['zonas']
    by_name = {z['zona']: z for z in zonas}
    assert by_name['City Bell']['count'] == 2
    assert by_name['Gonnet']['count'] == 1
    assert by_name['City Bell']['zona_norm'] == normalize_zona('City Bell')


async def test_zonas_excludes_sources_without_zona() -> None:
    async with _client(_FakeSupabase(list(ROWS))) as client:
        resp = await client.get('/manual-sources/zonas')

    assert [z['zona'] for z in resp.json()['zonas']] == ['City Bell', 'Gonnet']


async def test_zonas_are_alphabetical() -> None:
    rows = [
        _row('X', 'https://x.com', 'Villa Elisa'),
        _row('Y', 'https://y.com', 'Berisso'),
        _row('Z', 'https://z.com', 'Gonnet'),
    ]
    async with _client(_FakeSupabase(rows)) as client:
        resp = await client.get('/manual-sources/zonas')

    assert [z['zona'] for z in resp.json()['zonas']] == ['Berisso', 'Gonnet', 'Villa Elisa']


async def test_zonas_when_supabase_not_configured() -> None:
    async with _client(None) as client:
        resp = await client.get('/manual-sources/zonas')

    assert resp.status_code == 200
    assert resp.json() == {'zonas': [], 'total': 0, 'error': 'Supabase no configurado'}


async def test_zonas_failure_returns_error_without_raising() -> None:
    from tests.api.test_manual_sources import _RaisingSupabase

    async with _client(_RaisingSupabase()) as client:
        resp = await client.get('/manual-sources/zonas')

    assert resp.status_code == 200
    data = resp.json()
    assert data['zonas'] == []
    assert 'error' in data
