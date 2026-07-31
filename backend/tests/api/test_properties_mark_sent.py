"""Test-first: marcar propiedades como ENVIADAS y poder filtrarlas después.

Al tocar "Preparar y enviar" en /properties (o en los resultados de una
búsqueda) el front llama `POST /api/v1/properties/mark-sent` con los ids
elegidos. El backend sella `enviada_at` para que, al volver a esa misma
búsqueda, las ya enviadas se distingan de las que no.

`GET /api/v1/properties?enviada=true|false` filtra por esa marca.
"""
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


class _Res:
    def __init__(self, data: Any, count: int | None = None) -> None:
        self.data = data
        self.count = count


class _Query:
    """Graba update/select/filtros; todas las ops devuelven self."""

    def __init__(self, log: dict[str, Any], rows: list[dict[str, Any]]) -> None:
        self._log = log
        self._rows = rows

    def select(self, columns: str, **kwargs: Any) -> '_Query':
        self._log.setdefault('selects', []).append(columns)
        return self

    def update(self, patch: dict[str, Any]) -> '_Query':
        self._log['update'] = patch
        return self

    def in_(self, column: str, values: list[str]) -> '_Query':
        self._log['in_'] = (column, values)
        return self

    def eq(self, column: str, value: Any) -> '_Query':
        self._log.setdefault('eq', []).append((column, value))
        return self

    def gte(self, column: str, value: Any) -> '_Query':
        return self

    def lte(self, column: str, value: Any) -> '_Query':
        return self

    def is_(self, column: str, value: Any) -> '_Query':
        self._log.setdefault('is_', []).append((column, value))
        return self

    @property
    def not_(self) -> '_Query':
        self._log['not_'] = True
        return self

    def order(self, *args: Any, **kwargs: Any) -> '_Query':
        return self

    def range(self, *args: Any, **kwargs: Any) -> '_Query':
        return self

    def limit(self, *args: Any, **kwargs: Any) -> '_Query':
        return self

    async def execute(self) -> _Res:
        return _Res(self._rows, len(self._rows))


class _FakeSupabase:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.log: dict[str, Any] = {}
        self._rows = rows if rows is not None else []

    def table(self, name: str) -> _Query:
        self.log['table'] = name
        return _Query(self.log, self._rows)


def _client(fake_sb: Any) -> AsyncClient:
    from app.api.v1 import properties

    app = FastAPI()
    app.include_router(properties.router, prefix='/properties')
    app.state.supabase = fake_sb
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


# ── POST /mark-sent ──────────────────────────────────────────────────────────


async def test_mark_sent_stamps_enviada_at_for_the_given_ids() -> None:
    fake_sb = _FakeSupabase([{'id': 'a', 'enviada_at': '2026-08-01T10:00:00Z'}])

    async with _client(fake_sb) as client:
        resp = await client.post('/properties/mark-sent', json={'ids': ['a', 'b']})

    assert resp.status_code == 200
    body = resp.json()
    assert body['updated'] == 1
    assert fake_sb.log['table'] == 'properties'
    assert fake_sb.log['in_'] == ('id', ['a', 'b'])
    # Sella un instante real, no un booleano.
    assert fake_sb.log['update']['enviada_at'] is not None
    assert body['properties'][0]['enviada_at'] == '2026-08-01T10:00:00Z'


async def test_mark_sent_can_unmark() -> None:
    """`enviada: false` desmarca — el usuario se puede arrepentir."""
    fake_sb = _FakeSupabase([{'id': 'a', 'enviada_at': None}])

    async with _client(fake_sb) as client:
        resp = await client.post('/properties/mark-sent', json={'ids': ['a'], 'enviada': False})

    assert resp.status_code == 200
    assert fake_sb.log['update'] == {'enviada_at': None}


async def test_mark_sent_dedupes_and_ignores_blanks() -> None:
    fake_sb = _FakeSupabase([])

    async with _client(fake_sb) as client:
        await client.post('/properties/mark-sent', json={'ids': ['a', 'a', '  ', 'b', None]})

    assert fake_sb.log['in_'] == ('id', ['a', 'b'])


async def test_mark_sent_rejects_an_empty_id_list() -> None:
    async with _client(_FakeSupabase()) as client:
        resp = await client.post('/properties/mark-sent', json={'ids': []})

    assert resp.status_code == 400


async def test_mark_sent_without_supabase_reports_the_error() -> None:
    from app.api.v1 import properties

    app = FastAPI()
    app.include_router(properties.router, prefix='/properties')
    app.state.supabase = None

    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        resp = await client.post('/properties/mark-sent', json={'ids': ['a']})

    assert resp.status_code == 200
    assert resp.json()['error']


# ── GET /properties?enviada= ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ('value', 'expect_not'),
    [('true', True), ('false', False)],
)
async def test_list_filters_by_enviada(value: str, expect_not: bool) -> None:
    fake_sb = _FakeSupabase([])

    async with _client(fake_sb) as client:
        resp = await client.get(f'/properties?enviada={value}')

    assert resp.status_code == 200
    assert ('enviada_at', 'null') in fake_sb.log['is_']
    assert fake_sb.log.get('not_', False) is expect_not


async def test_list_without_enviada_does_not_filter_by_it() -> None:
    fake_sb = _FakeSupabase([])

    async with _client(fake_sb) as client:
        await client.get('/properties')

    assert ('enviada_at', 'null') not in fake_sb.log.get('is_', [])
