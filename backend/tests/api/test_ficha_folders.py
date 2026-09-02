"""Test-first: carpetas para agrupar Fichas Propio.

Las fichas propias (`properties` con `fuente='manual'`) se acumulan en una sola
lista. El usuario quiere separarlas por cliente o por búsqueda, con el mismo
gesto que ya tiene en el historial: carpetas con nombre libre y "mover a".

- `GET/POST   /api/v1/ficha-folders`           listar / crear carpeta
- `DELETE     /api/v1/ficha-folders/{id}`      borrar (las fichas quedan sin carpeta, FK set null)
- `POST       /api/v1/ficha-folders/assign`    mover fichas a una carpeta (o sacarlas con null)

Fake de Supabase con la misma forma que `test_properties_mark_sent.py`: graba
la cadena fluida y devuelve filas prefijadas.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


class _Res:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Query:
    def __init__(self, log: dict[str, Any], rows: list[dict[str, Any]]) -> None:
        self._log = log
        self._rows = rows

    def select(self, *_a: Any, **_kw: Any) -> '_Query':
        self._log['select'] = True
        return self

    def insert(self, payload: dict[str, Any]) -> '_Query':
        self._log['insert'] = payload
        return self

    def update(self, patch: dict[str, Any]) -> '_Query':
        self._log['update'] = patch
        return self

    def delete(self) -> '_Query':
        self._log['delete'] = True
        return self

    def eq(self, column: str, value: Any) -> '_Query':
        self._log.setdefault('eq', []).append((column, value))
        return self

    def in_(self, column: str, values: list[str]) -> '_Query':
        self._log['in_'] = (column, values)
        return self

    def order(self, *_a: Any, **_kw: Any) -> '_Query':
        return self

    async def execute(self) -> _Res:
        return _Res(self._rows)


class _FakeSupabase:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.log: dict[str, Any] = {}
        self._rows = rows if rows is not None else []

    def table(self, name: str) -> _Query:
        self.log.setdefault('tables', []).append(name)
        return _Query(self.log, self._rows)


class _BrokenSupabase(_FakeSupabase):
    def table(self, name: str) -> _Query:
        raise RuntimeError('relation "ficha_folders" does not exist')


def _client(sb: Any) -> AsyncClient:
    from app.api.v1 import ficha_folders

    app = FastAPI()
    app.include_router(ficha_folders.router, prefix='/ficha-folders')
    app.state.supabase = sb
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


# ── listar / crear ──────────────────────────────────────────────────────────


async def test_list_returns_folders_from_their_own_table() -> None:
    sb = _FakeSupabase([{'id': 'f1', 'name': 'Cliente Pérez', 'created_at': '2026-09-01T00:00:00Z'}])

    async with _client(sb) as c:
        body = (await c.get('/ficha-folders')).json()

    assert sb.log['tables'] == ['ficha_folders']
    assert body['folders'][0]['name'] == 'Cliente Pérez'
    assert body['total'] == 1


async def test_create_trims_the_name_and_returns_the_row() -> None:
    sb = _FakeSupabase([{'id': 'f1', 'name': 'Cliente Pérez'}])

    async with _client(sb) as c:
        body = (await c.post('/ficha-folders', json={'name': '  Cliente Pérez  '})).json()

    assert sb.log['insert'] == {'name': 'Cliente Pérez'}
    assert body['folder']['id'] == 'f1'


async def test_create_rejects_a_blank_name() -> None:
    sb = _FakeSupabase()

    async with _client(sb) as c:
        resp = await c.post('/ficha-folders', json={'name': '   '})

    assert resp.status_code == 400
    assert 'insert' not in sb.log


# ── borrar ──────────────────────────────────────────────────────────────────


async def test_delete_only_touches_the_folder_row() -> None:
    """Las fichas NO se tocan: la FK `on delete set null` las suelta sola."""
    sb = _FakeSupabase([])

    async with _client(sb) as c:
        body = (await c.delete('/ficha-folders/f1')).json()

    assert body['deleted'] is True
    assert sb.log['tables'] == ['ficha_folders']
    assert sb.log['delete'] is True
    assert ('id', 'f1') in sb.log['eq']


# ── mover fichas ────────────────────────────────────────────────────────────


async def test_assign_moves_the_given_fichas_into_the_folder() -> None:
    sb = _FakeSupabase([{'id': 'a', 'ficha_folder_id': 'f1'}, {'id': 'b', 'ficha_folder_id': 'f1'}])

    async with _client(sb) as c:
        body = (await c.post('/ficha-folders/assign', json={'ids': ['a', 'b'], 'folder_id': 'f1'})).json()

    assert sb.log['tables'] == ['properties']
    assert sb.log['update'] == {'ficha_folder_id': 'f1'}
    assert sb.log['in_'] == ('id', ['a', 'b'])
    assert body['updated'] == 2
    assert [p['id'] for p in body['properties']] == ['a', 'b']


async def test_assign_with_null_folder_takes_fichas_out_of_any_folder() -> None:
    sb = _FakeSupabase([{'id': 'a', 'ficha_folder_id': None}])

    async with _client(sb) as c:
        await c.post('/ficha-folders/assign', json={'ids': ['a'], 'folder_id': None})

    assert sb.log['update'] == {'ficha_folder_id': None}


async def test_assign_dedupes_and_ignores_blanks() -> None:
    sb = _FakeSupabase([])

    async with _client(sb) as c:
        await c.post('/ficha-folders/assign', json={'ids': ['a', 'a', ' ', None, 'b'], 'folder_id': 'f1'})

    assert sb.log['in_'] == ('id', ['a', 'b'])


async def test_assign_rejects_an_empty_id_list() -> None:
    """Un `in_` vacío podría barrer la tabla entera: se corta antes."""
    sb = _FakeSupabase()

    async with _client(sb) as c:
        resp = await c.post('/ficha-folders/assign', json={'ids': [], 'folder_id': 'f1'})

    assert resp.status_code == 400
    assert 'update' not in sb.log


async def test_assign_is_not_shadowed_by_the_folder_id_delete_route() -> None:
    """`DELETE /{folder_id}` y `POST /assign` conviven: distinto método."""
    sb = _FakeSupabase([{'id': 'a', 'ficha_folder_id': 'f1'}])

    async with _client(sb) as c:
        resp = await c.post('/ficha-folders/assign', json={'ids': ['a'], 'folder_id': 'f1'})

    assert resp.status_code == 200
    assert 'updated' in resp.json()


# ── degradación ─────────────────────────────────────────────────────────────


async def test_without_supabase_every_route_answers_200_with_an_error_field() -> None:
    async with _client(None) as c:
        listed = await c.get('/ficha-folders')
        created = await c.post('/ficha-folders', json={'name': 'x'})
        assigned = await c.post('/ficha-folders/assign', json={'ids': ['a'], 'folder_id': None})
        deleted = await c.delete('/ficha-folders/f1')

    for resp in (listed, created, assigned, deleted):
        assert resp.status_code == 200
        assert resp.json()['error']
    assert listed.json()['folders'] == []


async def test_list_survives_a_missing_migration() -> None:
    """Sin la tabla todavía, la pestaña Ficha Propio tiene que seguir cargando."""
    async with _client(_BrokenSupabase()) as c:
        resp = await c.get('/ficha-folders')

    assert resp.status_code == 200
    assert resp.json()['folders'] == []
    assert resp.json()['error']
