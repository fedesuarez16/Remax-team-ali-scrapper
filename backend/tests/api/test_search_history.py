"""Test-first for `GET/POST /api/v1/search-history` — server-side sidebar
history persistence, replacing localStorage as source of truth.

Fake `app.state.supabase` mirrors the fluent `.table(name).select(...)/
.insert(...)/.delete(...).eq/.ilike/.in_/.order/.limit(...).execute()` chain
used by the route, generically enough to model an in-memory `search_history`
table. Mirrors `test_properties_update.py`'s fake-Supabase pattern.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


class _Res:
    def __init__(self, data) -> None:
        self.data = data


class _FakeQuery:
    def __init__(self, store: list[dict], mode: str, insert_defaults: dict | None = None) -> None:
        self._store = store
        self._mode = mode  # 'select' | 'insert' | 'delete'
        self._insert_defaults = insert_defaults or {}
        self._filters: list[tuple[str, str, object]] = []
        self._order_field: str | None = None
        self._order_desc = False
        self._limit_n: int | None = None
        self._insert_payload: dict | None = None

    # -- filters --------------------------------------------------------
    def select(self, *_a, **_kw) -> '_FakeQuery':
        return self

    def insert(self, payload: dict) -> '_FakeQuery':
        self._insert_payload = payload
        return self

    def delete(self) -> '_FakeQuery':
        return self

    def eq(self, field: str, value) -> '_FakeQuery':
        self._filters.append(('eq', field, value))
        return self

    def ilike(self, field: str, value) -> '_FakeQuery':
        self._filters.append(('ilike', field, value))
        return self

    def in_(self, field: str, values) -> '_FakeQuery':
        self._filters.append(('in', field, values))
        return self

    def order(self, field: str, desc: bool = False) -> '_FakeQuery':
        self._order_field = field
        self._order_desc = desc
        return self

    def limit(self, n: int) -> '_FakeQuery':
        self._limit_n = n
        return self

    def _match(self, row: dict) -> bool:
        for op, field, value in self._filters:
            if op == 'eq' and row.get(field) != value:
                return False
            if op == 'ilike' and str(row.get(field, '')).lower() != str(value).lower():
                return False
            if op == 'in' and row.get(field) not in value:
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

        if self._mode == 'delete':
            matched = [r for r in self._store if self._match(r)]
            for r in matched:
                self._store.remove(r)
            return _Res(matched)

        # select
        rows = [r for r in self._store if self._match(r)]
        if self._order_field:
            rows = sorted(rows, key=lambda r: r[self._order_field], reverse=self._order_desc)
        if self._limit_n is not None:
            rows = rows[: self._limit_n]
        return _Res(rows)


class _FakeTable:
    def __init__(self, store: list[dict], insert_defaults: dict | None = None) -> None:
        self._store = store
        self._insert_defaults = insert_defaults or {}

    def select(self, *a, **kw) -> _FakeQuery:
        return _FakeQuery(self._store, 'select', self._insert_defaults).select(*a, **kw)

    def insert(self, payload: dict) -> _FakeQuery:
        return _FakeQuery(self._store, 'insert', self._insert_defaults).insert(payload)

    def delete(self) -> _FakeQuery:
        return _FakeQuery(self._store, 'delete', self._insert_defaults)


class _FakeSupabase:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self._store: list[dict] = rows or []

    def table(self, name: str) -> _FakeTable:
        if name == 'search_history':
            return _FakeTable(self._store, insert_defaults={'zona': None, 'job_id': None})
        raise AssertionError(f'unexpected table {name}')


class _RaisingTable:
    """Every query on this table blows up — models a Supabase outage."""

    def select(self, *_a, **_kw):
        raise RuntimeError('boom')

    def insert(self, *_a, **_kw):
        raise RuntimeError('boom')

    def delete(self, *_a, **_kw):
        raise RuntimeError('boom')


class _RaisingSupabase:
    def table(self, name: str) -> _RaisingTable:
        return _RaisingTable()


def _make_app(fake_sb) -> FastAPI:
    from app.api.v1 import search_history

    app = FastAPI()
    app.include_router(search_history.router, prefix='/search-history')
    app.state.supabase = fake_sb
    return app


def _client(fake_sb) -> AsyncClient:
    # NOTE: base_url deliberately has no trailing path segment. httpx's
    # AsyncClient normalizes `base_url` to always end with '/', so a
    # base_url of 'http://test/search-history' + client.get('') actually
    # requests '/search-history/' (trailing slash) and 307-redirects since
    # the route has no trailing slash. Using the bare host + full path per
    # call avoids that entirely.
    transport = ASGITransport(app=_make_app(fake_sb))
    return AsyncClient(transport=transport, base_url='http://test')


def _row(query: str, *, minutes_ago: int, job_id: str | None = None) -> dict:
    created = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return {
        'id': f'id-{uuid.uuid4().hex[:8]}',
        'query': query,
        'zona': None,
        'job_id': job_id,
        'created_at': created.isoformat(),
    }


# -- GET / POST (list + upsert) ------------------------------------------


async def test_list_returns_entries_most_recent_first() -> None:
    fake_sb = _FakeSupabase([
        _row('casas en palermo', minutes_ago=5),
        _row('deptos en recoleta', minutes_ago=1),
        _row('ph en caballito', minutes_ago=10),
    ])
    async with _client(fake_sb) as client:
        resp = await client.get('/search-history')

    assert resp.status_code == 200
    data = resp.json()
    assert data['total'] == 3
    assert [e['query'] for e in data['history']] == [
        'deptos en recoleta', 'casas en palermo', 'ph en caballito',
    ]


async def test_list_when_supabase_not_configured() -> None:
    async with _client(None) as client:
        resp = await client.get('/search-history')

    assert resp.status_code == 200
    data = resp.json()
    assert data == {'history': [], 'total': 0, 'error': 'Supabase no configurado'}


async def test_list_failure_returns_error_without_raising() -> None:
    async with _client(_RaisingSupabase()) as client:
        resp = await client.get('/search-history')

    assert resp.status_code == 200
    data = resp.json()
    assert data['history'] == []
    assert data['total'] == 0
    assert 'error' in data


async def test_post_new_query_inserts() -> None:
    fake_sb = _FakeSupabase([])
    async with _client(fake_sb) as client:
        resp = await client.post('/search-history', json={'query': 'casas en palermo'})

    assert resp.status_code == 200
    entry = resp.json()['entry']
    assert entry['query'] == 'casas en palermo'
    assert entry['job_id'] is None
    assert len(fake_sb._store) == 1


async def test_post_case_insensitive_dedupe_replaces_row() -> None:
    existing = _row('casas en palermo', minutes_ago=5)
    fake_sb = _FakeSupabase([existing])
    async with _client(fake_sb) as client:
        resp = await client.post('/search-history', json={'query': 'Casas en Palermo', 'job_id': 'abc123'})

    assert resp.status_code == 200
    entry = resp.json()['entry']
    assert entry['job_id'] == 'abc123'
    # delete-then-insert: old row is gone, replaced by a fresh row
    assert entry['id'] != existing['id']
    assert len(fake_sb._store) == 1
    assert fake_sb._store[0]['query'] == 'Casas en Palermo'


async def test_post_cap_trims_oldest_beyond_20() -> None:
    seed = [_row(f'query {i}', minutes_ago=i) for i in range(20)]
    fake_sb = _FakeSupabase(seed)
    async with _client(fake_sb) as client:
        resp = await client.post('/search-history', json={'query': 'brand new query'})

    assert resp.status_code == 200
    assert len(fake_sb._store) == 20
    queries = {r['query'] for r in fake_sb._store}
    assert 'brand new query' in queries
    # the oldest seeded row (minutes_ago=19) must have been trimmed
    assert 'query 19' not in queries


async def test_post_empty_query_is_a_noop() -> None:
    fake_sb = _FakeSupabase([])
    async with _client(fake_sb) as client:
        resp = await client.post('/search-history', json={'query': '   '})

    assert resp.status_code == 200
    assert resp.json() == {'entry': None}
    assert fake_sb._store == []


async def test_post_failure_returns_error_without_raising() -> None:
    async with _client(_RaisingSupabase()) as client:
        resp = await client.post('/search-history', json={'query': 'casas en palermo'})

    assert resp.status_code == 200
    data = resp.json()
    assert data['entry'] is None
    assert 'error' in data


async def test_post_when_supabase_not_configured() -> None:
    async with _client(None) as client:
        resp = await client.post('/search-history', json={'query': 'casas en palermo'})

    assert resp.status_code == 200
    data = resp.json()
    assert data == {'entry': None, 'error': 'Supabase no configurado'}
