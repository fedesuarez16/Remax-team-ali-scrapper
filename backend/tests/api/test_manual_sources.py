"""Test-first for `GET/POST/DELETE /api/v1/manual-sources` — lets the user
manually register additional real-estate agency/portal websites (e.g. a
RE/MAX office not surfaced by the Google Maps agency-discovery step) so they
get folded into the existing `run_website_scraper` → LLM-extraction pipeline
on every future search, regardless of what Google Maps finds.

Fake `app.state.supabase` mirrors the fluent `.table(name).select(...)/
.insert(...)/.delete(...).eq/.order(...).execute()` chain, same pattern as
`test_search_history.py`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.services.zona import normalize_zona


class _Res:
    def __init__(self, data) -> None:
        self.data = data


class _FakeQuery:
    def __init__(self, store: list[dict], mode: str, insert_defaults: dict | None = None) -> None:
        self._store = store
        self._mode = mode  # 'select' | 'insert' | 'update' | 'delete'
        self._insert_defaults = insert_defaults or {}
        self._filters: list[tuple[str, str, object]] = []
        self._order_field: str | None = None
        self._order_desc = False
        self._insert_payload: dict | None = None
        self._update_payload: dict | None = None

    def select(self, *_a, **_kw) -> '_FakeQuery':
        return self

    def insert(self, payload: dict) -> '_FakeQuery':
        self._insert_payload = payload
        return self

    def update(self, payload: dict) -> '_FakeQuery':
        self._update_payload = payload
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
            row = dict(self._insert_payload or {})
            row.setdefault('id', f'id-{uuid.uuid4().hex[:8]}')
            for k, v in self._insert_defaults.items():
                row.setdefault(k, v)
            row.setdefault('created_at', datetime.now(timezone.utc).isoformat())
            self._store.append(row)
            return _Res([row])

        if self._mode == 'update':
            matched = [r for r in self._store if self._match(r)]
            for r in matched:
                r.update(self._update_payload or {})
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
    def __init__(self, store: list[dict], insert_defaults: dict | None = None) -> None:
        self._store = store
        self._insert_defaults = insert_defaults or {}

    def select(self, *a, **kw) -> _FakeQuery:
        return _FakeQuery(self._store, 'select', self._insert_defaults).select(*a, **kw)

    def insert(self, payload: dict) -> _FakeQuery:
        return _FakeQuery(self._store, 'insert', self._insert_defaults).insert(payload)

    def update(self, payload: dict) -> _FakeQuery:
        return _FakeQuery(self._store, 'update', self._insert_defaults).update(payload)

    def delete(self) -> _FakeQuery:
        return _FakeQuery(self._store, 'delete', self._insert_defaults)


class _FakeSupabase:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self._store: list[dict] = rows or []

    def table(self, name: str) -> _FakeTable:
        if name == 'manual_sources':
            return _FakeTable(self._store, insert_defaults={'activo': True})
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
    from app.api.v1 import manual_sources

    app = FastAPI()
    app.include_router(manual_sources.router, prefix='/manual-sources')
    app.state.supabase = fake_sb
    return app


def _client(fake_sb) -> AsyncClient:
    # NOTE: base_url deliberately has no trailing path segment — see
    # test_search_history.py for why (avoids a 307 redirect on the bare route).
    transport = ASGITransport(app=_make_app(fake_sb))
    return AsyncClient(transport=transport, base_url='http://test')


def _row(nombre: str, url: str, *, minutes_ago: int, activo: bool = True) -> dict:
    created = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return {
        'id': f'id-{uuid.uuid4().hex[:8]}',
        'nombre': nombre,
        'url': url,
        'activo': activo,
        'created_at': created.isoformat(),
    }


# -- GET (list) -----------------------------------------------------------


async def test_list_returns_sources_most_recent_first() -> None:
    fake_sb = _FakeSupabase([
        _row('RE/MAX Belgrano', 'https://remax.com.ar/belgrano', minutes_ago=5),
        _row('Inmobiliaria Sur', 'https://inmosur.com.ar', minutes_ago=1),
    ])
    async with _client(fake_sb) as client:
        resp = await client.get('/manual-sources')

    assert resp.status_code == 200
    data = resp.json()
    assert data['total'] == 2
    assert [s['nombre'] for s in data['sources']] == ['Inmobiliaria Sur', 'RE/MAX Belgrano']


async def test_list_when_supabase_not_configured() -> None:
    async with _client(None) as client:
        resp = await client.get('/manual-sources')

    assert resp.status_code == 200
    assert resp.json() == {'sources': [], 'total': 0, 'error': 'Supabase no configurado'}


async def test_list_failure_returns_error_without_raising() -> None:
    async with _client(_RaisingSupabase()) as client:
        resp = await client.get('/manual-sources')

    assert resp.status_code == 200
    data = resp.json()
    assert data['sources'] == []
    assert data['total'] == 0
    assert 'error' in data


# -- POST (create) ---------------------------------------------------------


async def test_post_creates_source_with_https_url() -> None:
    fake_sb = _FakeSupabase([])
    async with _client(fake_sb) as client:
        resp = await client.post('/manual-sources', json={
            'nombre': 'RE/MAX Belgrano', 'url': 'https://www.remax.com.ar/agencia/belgrano',
        })

    assert resp.status_code == 200
    source = resp.json()['source']
    assert source['nombre'] == 'RE/MAX Belgrano'
    assert source['url'] == 'https://www.remax.com.ar/agencia/belgrano'
    assert source['activo'] is True
    assert len(fake_sb._store) == 1


async def test_post_empty_nombre_is_rejected() -> None:
    fake_sb = _FakeSupabase([])
    async with _client(fake_sb) as client:
        resp = await client.post('/manual-sources', json={'nombre': '   ', 'url': 'https://x.com'})

    assert resp.status_code == 200
    data = resp.json()
    assert data['source'] is None
    assert 'error' in data
    assert fake_sb._store == []


async def test_post_url_without_http_scheme_is_rejected() -> None:
    fake_sb = _FakeSupabase([])
    async with _client(fake_sb) as client:
        resp = await client.post('/manual-sources', json={'nombre': 'Sitio raro', 'url': 'ftp://x.com'})

    assert resp.status_code == 200
    data = resp.json()
    assert data['source'] is None
    assert 'error' in data
    assert fake_sb._store == []


async def test_post_failure_returns_error_without_raising() -> None:
    async with _client(_RaisingSupabase()) as client:
        resp = await client.post('/manual-sources', json={'nombre': 'X', 'url': 'https://x.com'})

    assert resp.status_code == 200
    data = resp.json()
    assert data['source'] is None
    assert 'error' in data


async def test_post_when_supabase_not_configured() -> None:
    async with _client(None) as client:
        resp = await client.post('/manual-sources', json={'nombre': 'X', 'url': 'https://x.com'})

    assert resp.status_code == 200
    assert resp.json() == {'source': None, 'error': 'Supabase no configurado'}


# -- PATCH (rename / toggle) ------------------------------------------------


async def test_patch_renames_source() -> None:
    existing = _row('RE/MAX Belgrano', 'https://remax.com.ar/belgrano', minutes_ago=1)
    fake_sb = _FakeSupabase([existing])
    async with _client(fake_sb) as client:
        resp = await client.patch(
            f'/manual-sources/{existing["id"]}', json={'nombre': 'RE/MAX Belgrano Norte'}
        )

    assert resp.status_code == 200
    source = resp.json()['source']
    assert source['nombre'] == 'RE/MAX Belgrano Norte'
    assert fake_sb._store[0]['nombre'] == 'RE/MAX Belgrano Norte'


async def test_patch_rename_leaves_activo_untouched() -> None:
    existing = _row('Inmobiliaria Sur', 'https://inmosur.com.ar', minutes_ago=1, activo=False)
    fake_sb = _FakeSupabase([existing])
    async with _client(fake_sb) as client:
        resp = await client.patch(f'/manual-sources/{existing["id"]}', json={'nombre': 'Inmo Sur'})

    assert resp.status_code == 200
    assert resp.json()['source']['activo'] is False


async def test_patch_toggles_activo() -> None:
    existing = _row('Inmobiliaria Sur', 'https://inmosur.com.ar', minutes_ago=1)
    fake_sb = _FakeSupabase([existing])
    async with _client(fake_sb) as client:
        resp = await client.patch(f'/manual-sources/{existing["id"]}', json={'activo': False})

    assert resp.status_code == 200
    assert resp.json()['source']['activo'] is False
    assert fake_sb._store[0]['activo'] is False


async def test_patch_updates_url() -> None:
    existing = _row('Inmobiliaria Sur', 'https://inmosur.com.ar', minutes_ago=1)
    fake_sb = _FakeSupabase([existing])
    async with _client(fake_sb) as client:
        resp = await client.patch(
            f'/manual-sources/{existing["id"]}', json={'url': 'https://www.inmosur.com.ar/venta'}
        )

    assert resp.status_code == 200
    assert resp.json()['source']['url'] == 'https://www.inmosur.com.ar/venta'


async def test_patch_url_without_http_scheme_is_rejected() -> None:
    existing = _row('Inmobiliaria Sur', 'https://inmosur.com.ar', minutes_ago=1)
    fake_sb = _FakeSupabase([existing])
    async with _client(fake_sb) as client:
        resp = await client.patch(f'/manual-sources/{existing["id"]}', json={'url': 'ftp://x.com'})

    assert resp.status_code == 200
    data = resp.json()
    assert data['source'] is None
    assert 'error' in data
    assert fake_sb._store[0]['url'] == 'https://inmosur.com.ar'


async def test_patch_zona_recomputes_zona_norm() -> None:
    """The zona bucket is matched on `zona_norm` (see list_manual_sources), so
    editing `zona` without recomputing it would silently orphan the source."""
    existing = _row('Inmobiliaria Sur', 'https://inmosur.com.ar', minutes_ago=1)
    existing['zona'] = 'City Bell'
    existing['zona_norm'] = normalize_zona('City Bell')
    fake_sb = _FakeSupabase([existing])
    async with _client(fake_sb) as client:
        resp = await client.patch(f'/manual-sources/{existing["id"]}', json={'zona': 'Gonnet'})

    assert resp.status_code == 200
    source = resp.json()['source']
    assert source['zona'] == 'Gonnet'
    assert source['zona_norm'] == normalize_zona('Gonnet')


async def test_patch_blank_zona_clears_the_bucket() -> None:
    existing = _row('Inmobiliaria Sur', 'https://inmosur.com.ar', minutes_ago=1)
    existing['zona'] = 'City Bell'
    existing['zona_norm'] = normalize_zona('City Bell')
    fake_sb = _FakeSupabase([existing])
    async with _client(fake_sb) as client:
        resp = await client.patch(f'/manual-sources/{existing["id"]}', json={'zona': '  '})

    assert resp.status_code == 200
    source = resp.json()['source']
    assert source['zona'] is None
    assert source['zona_norm'] is None


async def test_patch_updates_nombre_url_and_zona_together() -> None:
    existing = _row('Inmobiliaria Sur', 'https://inmosur.com.ar', minutes_ago=1)
    fake_sb = _FakeSupabase([existing])
    async with _client(fake_sb) as client:
        resp = await client.patch(f'/manual-sources/{existing["id"]}', json={
            'nombre': 'Inmo Sur', 'url': 'https://inmosur.com.ar/ventas', 'zona': 'Gonnet',
        })

    assert resp.status_code == 200
    source = resp.json()['source']
    assert source['nombre'] == 'Inmo Sur'
    assert source['url'] == 'https://inmosur.com.ar/ventas'
    assert source['zona'] == 'Gonnet'
    assert source['zona_norm'] == normalize_zona('Gonnet')


async def test_patch_empty_url_is_rejected() -> None:
    existing = _row('Inmobiliaria Sur', 'https://inmosur.com.ar', minutes_ago=1)
    fake_sb = _FakeSupabase([existing])
    async with _client(fake_sb) as client:
        resp = await client.patch(f'/manual-sources/{existing["id"]}', json={'url': '   '})

    assert resp.status_code == 200
    data = resp.json()
    assert data['source'] is None
    assert 'error' in data


async def test_patch_empty_nombre_is_rejected() -> None:
    existing = _row('Inmobiliaria Sur', 'https://inmosur.com.ar', minutes_ago=1)
    fake_sb = _FakeSupabase([existing])
    async with _client(fake_sb) as client:
        resp = await client.patch(f'/manual-sources/{existing["id"]}', json={'nombre': '   '})

    assert resp.status_code == 200
    data = resp.json()
    assert data['source'] is None
    assert 'error' in data
    assert fake_sb._store[0]['nombre'] == 'Inmobiliaria Sur'


async def test_patch_with_nothing_to_update_is_rejected() -> None:
    existing = _row('Inmobiliaria Sur', 'https://inmosur.com.ar', minutes_ago=1)
    fake_sb = _FakeSupabase([existing])
    async with _client(fake_sb) as client:
        resp = await client.patch(f'/manual-sources/{existing["id"]}', json={})

    assert resp.status_code == 200
    data = resp.json()
    assert data['source'] is None
    assert 'error' in data


async def test_patch_failure_returns_error_without_raising() -> None:
    async with _client(_RaisingSupabase()) as client:
        resp = await client.patch('/manual-sources/some-id', json={'nombre': 'X'})

    assert resp.status_code == 200
    data = resp.json()
    assert data['source'] is None
    assert 'error' in data


async def test_patch_when_supabase_not_configured() -> None:
    async with _client(None) as client:
        resp = await client.patch('/manual-sources/some-id', json={'nombre': 'X'})

    assert resp.status_code == 200
    assert resp.json() == {'source': None, 'error': 'Supabase no configurado'}


# -- DELETE -----------------------------------------------------------------


async def test_delete_removes_source() -> None:
    existing = _row('RE/MAX Belgrano', 'https://remax.com.ar/belgrano', minutes_ago=1)
    fake_sb = _FakeSupabase([existing])
    async with _client(fake_sb) as client:
        resp = await client.delete(f'/manual-sources/{existing["id"]}')

    assert resp.status_code == 200
    assert resp.json() == {'deleted': True}
    assert fake_sb._store == []


async def test_delete_unknown_id_is_a_noop() -> None:
    fake_sb = _FakeSupabase([])
    async with _client(fake_sb) as client:
        resp = await client.delete('/manual-sources/does-not-exist')

    assert resp.status_code == 200
    assert resp.json() == {'deleted': True}


async def test_delete_failure_returns_error_without_raising() -> None:
    async with _client(_RaisingSupabase()) as client:
        resp = await client.delete('/manual-sources/some-id')

    assert resp.status_code == 200
    data = resp.json()
    assert data['deleted'] is False
    assert 'error' in data


async def test_delete_when_supabase_not_configured() -> None:
    async with _client(None) as client:
        resp = await client.delete('/manual-sources/some-id')

    assert resp.status_code == 200
    assert resp.json() == {'deleted': False, 'error': 'Supabase no configurado'}
