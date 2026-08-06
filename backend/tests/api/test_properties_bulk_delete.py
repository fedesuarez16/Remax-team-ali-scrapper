"""Test-first: borrar propiedades elegidas con el seleccionador.

En /properties y en los resultados de una búsqueda el usuario marca tarjetas y
toca "Eliminar". El front llama `POST /api/v1/properties/bulk-delete` con los
ids elegidos y el backend los borra en UNA sola operación (`in_`), no de a uno:
borrar 40 propiedades no puede costar 40 round-trips.
"""
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


class _Res:
    def __init__(self, data: Any, count: int | None = None) -> None:
        self.data = data
        self.count = count


class _Query:
    """Graba delete/filtros; todas las ops devuelven self."""

    def __init__(self, log: dict[str, Any], rows: list[dict[str, Any]]) -> None:
        self._log = log
        self._rows = rows

    def delete(self) -> '_Query':
        self._log['delete'] = True
        return self

    def in_(self, column: str, values: list[str]) -> '_Query':
        self._log['in_'] = (column, values)
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


class _BoomSupabase(_FakeSupabase):
    def table(self, name: str) -> Any:
        raise RuntimeError('supabase caído')


def _client(fake_sb: Any) -> AsyncClient:
    from app.api.v1 import properties

    app = FastAPI()
    app.include_router(properties.router, prefix='/properties')
    app.state.supabase = fake_sb
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


async def test_bulk_delete_removes_the_given_ids_in_one_call() -> None:
    fake_sb = _FakeSupabase([{'id': 'a'}, {'id': 'b'}])

    async with _client(fake_sb) as client:
        resp = await client.post('/properties/bulk-delete', json={'ids': ['a', 'b']})

    assert resp.status_code == 200
    body = resp.json()
    assert body['deleted'] == 2
    assert body['ids'] == ['a', 'b']
    assert fake_sb.log['table'] == 'properties'
    assert fake_sb.log['delete'] is True
    assert fake_sb.log['in_'] == ('id', ['a', 'b'])


async def test_bulk_delete_dedupes_and_ignores_blanks() -> None:
    fake_sb = _FakeSupabase([])

    async with _client(fake_sb) as client:
        await client.post('/properties/bulk-delete', json={'ids': ['a', 'a', '  ', 'b', None]})

    assert fake_sb.log['in_'] == ('id', ['a', 'b'])


async def test_bulk_delete_rejects_an_empty_id_list() -> None:
    """Sin ids no hay borrado: un `in_` vacío podría barrer la tabla entera."""
    async with _client(_FakeSupabase()) as client:
        resp = await client.post('/properties/bulk-delete', json={'ids': []})

    assert resp.status_code == 400


async def test_bulk_delete_without_supabase_reports_the_error() -> None:
    from app.api.v1 import properties

    app = FastAPI()
    app.include_router(properties.router, prefix='/properties')
    app.state.supabase = None

    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        resp = await client.post('/properties/bulk-delete', json={'ids': ['a']})

    assert resp.status_code == 200
    body = resp.json()
    assert body['deleted'] == 0
    assert body['error']


async def test_bulk_delete_surfaces_supabase_failures_without_crashing() -> None:
    async with _client(_BoomSupabase()) as client:
        resp = await client.post('/properties/bulk-delete', json={'ids': ['a']})

    assert resp.status_code == 200
    body = resp.json()
    assert body['deleted'] == 0
    assert 'supabase caído' in body['error']
