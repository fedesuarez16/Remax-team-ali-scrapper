"""Test-first: fichas de selección (varias propiedades en una sola ficha).

El agente marca N Fichas Propio y genera UNA ficha compartible para un cliente
("Propiedades para Romina García"). El agente la personaliza (título,
subtítulo, comentario por propiedad, orden, mapa) y el cliente, desde el link
público, califica cada propiedad con "Me interesa" / "No me interesa".

Fake de Supabase EN MEMORIA: a diferencia del de carpetas, acá hay dos tablas
que se cruzan (colección → items → properties), así que el fake filtra de
verdad en vez de devolver filas prefijadas.
"""
from __future__ import annotations

import itertools
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


class _Res:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Query:
    def __init__(self, db: '_MemSupabase', table: str) -> None:
        self._db = db
        self._table = table
        self._op = 'select'
        self._payload: Any = None
        self._filters: list[tuple[str, str, Any]] = []
        self._order: tuple[str, bool] | None = None

    def select(self, *_a: Any, **_kw: Any) -> '_Query':
        self._op = 'select'
        return self

    def insert(self, payload: Any) -> '_Query':
        self._op, self._payload = 'insert', payload
        return self

    def update(self, patch: dict[str, Any]) -> '_Query':
        self._op, self._payload = 'update', patch
        return self

    def delete(self) -> '_Query':
        self._op = 'delete'
        return self

    def eq(self, column: str, value: Any) -> '_Query':
        self._filters.append(('eq', column, value))
        return self

    def in_(self, column: str, values: list[Any]) -> '_Query':
        self._filters.append(('in', column, list(values)))
        return self

    def is_(self, column: str, value: str) -> '_Query':
        # Sólo modela `is null`, que es lo que usa el router.
        self._filters.append(('eq', column, None if value == 'null' else value))
        return self

    def order(self, column: str, desc: bool = False) -> '_Query':
        self._order = (column, desc)
        return self

    def _match(self, row: dict[str, Any]) -> bool:
        for kind, col, val in self._filters:
            if kind == 'eq' and row.get(col) != val:
                return False
            if kind == 'in' and row.get(col) not in val:
                return False
        return True

    async def execute(self) -> _Res:
        rows = self._db.tables.setdefault(self._table, [])
        self._db.calls.append((self._table, self._op))
        if self._op == 'insert':
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            created = []
            for p in payload:
                row = {'id': f'{self._table}-{next(self._db.ids)}', **p}
                row.setdefault('created_at', f'2026-11-04T00:00:{len(rows):02d}Z')
                rows.append(row)
                created.append(dict(row))
            return _Res(created)
        hits = [r for r in rows if self._match(r)]
        if self._op == 'update':
            for r in hits:
                r.update(self._payload)
            return _Res([dict(r) for r in hits])
        if self._op == 'delete':
            self._db.tables[self._table] = [r for r in rows if not self._match(r)]
            return _Res([dict(r) for r in hits])
        if self._order:
            col, desc = self._order
            hits = sorted(hits, key=lambda r: r.get(col) or 0, reverse=desc)
        return _Res([dict(r) for r in hits])


class _MemSupabase:
    def __init__(self, tables: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = tables or {}
        self.calls: list[tuple[str, str]] = []
        self.ids = itertools.count(1)

    def table(self, name: str) -> _Query:
        return _Query(self, name)


def _client(sb: Any) -> AsyncClient:
    from app.api.v1 import ficha_colecciones

    app = FastAPI()
    app.include_router(ficha_colecciones.router, prefix='/ficha-colecciones')
    app.state.supabase = sb
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


def _props(*ids: str) -> list[dict[str, Any]]:
    return [{'id': i, 'titulo': f'Casa {i}', 'precio': 100_000, 'fuente': 'manual'} for i in ids]


async def _crear(c: AsyncClient, ids: list[str], **extra: Any) -> dict[str, Any]:
    resp = await c.post('/ficha-colecciones', json={'property_ids': ids, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()['coleccion']


# ── crear ───────────────────────────────────────────────────────────────────


async def test_create_keeps_the_selection_order_and_titles_it_for_the_client() -> None:
    sb = _MemSupabase({'properties': _props('a', 'b', 'c')})

    async with _client(sb) as c:
        col = await _crear(c, ['c', 'a', 'b'], cliente='  Romina García ', agente_email='nali@remax.com.ar')

    assert col['titulo'] == 'Propiedades para Romina García'
    assert col['cliente'] == 'Romina García'
    assert col['agente_email'] == 'nali@remax.com.ar'
    items = sb.tables['ficha_coleccion_items']
    assert [(i['property_id'], i['orden']) for i in items] == [('c', 0), ('a', 1), ('b', 2)]
    assert all(i['coleccion_id'] == col['id'] for i in items)


async def test_create_without_client_name_uses_a_generic_title() -> None:
    sb = _MemSupabase({'properties': _props('a')})

    async with _client(sb) as c:
        col = await _crear(c, ['a'])

    assert col['titulo'] == 'Propiedades seleccionadas'


async def test_create_dedupes_ids_and_ignores_blanks() -> None:
    sb = _MemSupabase({'properties': _props('a', 'b')})

    async with _client(sb) as c:
        await _crear(c, ['a', 'a', ' ', None, 'b'])

    assert [i['property_id'] for i in sb.tables['ficha_coleccion_items']] == ['a', 'b']


async def test_create_rejects_an_empty_selection() -> None:
    sb = _MemSupabase()

    async with _client(sb) as c:
        resp = await c.post('/ficha-colecciones', json={'property_ids': []})

    assert resp.status_code == 400
    assert 'ficha_colecciones' not in sb.tables


# ── leer ────────────────────────────────────────────────────────────────────


async def test_get_returns_items_in_order_with_their_property() -> None:
    sb = _MemSupabase({'properties': _props('a', 'b', 'c')})

    async with _client(sb) as c:
        col = await _crear(c, ['b', 'c', 'a'])
        body = (await c.get(f"/ficha-colecciones/{col['id']}")).json()

    assert body['coleccion']['id'] == col['id']
    assert [i['property']['id'] for i in body['items']] == ['b', 'c', 'a']
    assert body['items'][0]['property']['titulo'] == 'Casa b'


async def test_get_skips_items_whose_property_was_deleted() -> None:
    sb = _MemSupabase({'properties': _props('a', 'b')})

    async with _client(sb) as c:
        col = await _crear(c, ['a', 'b'])
        sb.tables['properties'] = _props('b')
        body = (await c.get(f"/ficha-colecciones/{col['id']}")).json()

    assert [i['property']['id'] for i in body['items']] == ['b']


async def test_get_unknown_collection_is_404() -> None:
    sb = _MemSupabase()

    async with _client(sb) as c:
        resp = await c.get('/ficha-colecciones/nope')

    assert resp.status_code == 404


async def test_list_counts_items_and_client_ratings() -> None:
    sb = _MemSupabase({'properties': _props('a', 'b', 'c')})

    async with _client(sb) as c:
        col = await _crear(c, ['a', 'b', 'c'], cliente='Romina')
        await c.post(f"/ficha-colecciones/{col['id']}/items/a/calificacion", json={'calificacion': 'interesa'})
        await c.post(f"/ficha-colecciones/{col['id']}/items/b/calificacion", json={'calificacion': 'no_interesa'})
        body = (await c.get('/ficha-colecciones')).json()

    [listed] = body['colecciones']
    assert listed['total'] == 3
    assert listed['interesa'] == 1
    assert listed['no_interesa'] == 1
    assert listed['portada'] is None or isinstance(listed['portada'], str)


# ── personalizar (agente) ───────────────────────────────────────────────────


async def test_patch_only_applies_whitelisted_fields() -> None:
    sb = _MemSupabase({'properties': _props('a')})

    async with _client(sb) as c:
        col = await _crear(c, ['a'])
        body = (await c.patch(
            f"/ficha-colecciones/{col['id']}",
            json={'subtitulo': 'Casas en La Plata', 'mostrar_mapa': True, 'id': 'hack', 'enviada_at': 'x'},
        )).json()

    assert body['coleccion']['subtitulo'] == 'Casas en La Plata'
    assert body['coleccion']['mostrar_mapa'] is True
    assert body['coleccion']['id'] == col['id']
    assert 'enviada_at' not in sb.tables['ficha_colecciones'][0] or \
        sb.tables['ficha_colecciones'][0]['enviada_at'] != 'x'


async def test_patch_with_nothing_editable_is_400() -> None:
    sb = _MemSupabase({'properties': _props('a')})

    async with _client(sb) as c:
        col = await _crear(c, ['a'])
        resp = await c.patch(f"/ficha-colecciones/{col['id']}", json={'id': 'hack'})

    assert resp.status_code == 400


async def test_reorder_rewrites_positions() -> None:
    sb = _MemSupabase({'properties': _props('a', 'b', 'c')})

    async with _client(sb) as c:
        col = await _crear(c, ['a', 'b', 'c'])
        await c.put(f"/ficha-colecciones/{col['id']}/orden", json={'property_ids': ['c', 'a', 'b']})
        body = (await c.get(f"/ficha-colecciones/{col['id']}")).json()

    assert [i['property']['id'] for i in body['items']] == ['c', 'a', 'b']


async def test_comment_is_saved_per_item() -> None:
    sb = _MemSupabase({'properties': _props('a', 'b')})

    async with _client(sb) as c:
        col = await _crear(c, ['a', 'b'])
        await c.patch(f"/ficha-colecciones/{col['id']}/items/b", json={'comentario': '  Tiene patio  '})
        body = (await c.get(f"/ficha-colecciones/{col['id']}")).json()

    comentarios = {i['property']['id']: i.get('comentario') for i in body['items']}
    assert comentarios == {'a': None, 'b': 'Tiene patio'}


async def test_remove_item_drops_it_from_the_collection_only() -> None:
    sb = _MemSupabase({'properties': _props('a', 'b')})

    async with _client(sb) as c:
        col = await _crear(c, ['a', 'b'])
        await c.delete(f"/ficha-colecciones/{col['id']}/items/a")
        body = (await c.get(f"/ficha-colecciones/{col['id']}")).json()

    assert [i['property']['id'] for i in body['items']] == ['b']
    assert len(sb.tables['properties']) == 2


# ── calificar (cliente) ─────────────────────────────────────────────────────


async def test_client_rating_is_saved_and_can_be_undone() -> None:
    sb = _MemSupabase({'properties': _props('a')})

    async with _client(sb) as c:
        col = await _crear(c, ['a'])
        url = f"/ficha-colecciones/{col['id']}/items/a/calificacion"
        first = (await c.post(url, json={'calificacion': 'interesa'})).json()
        item = sb.tables['ficha_coleccion_items'][0]
        assert item['calificacion'] == 'interesa'
        assert item['calificada_at']
        await c.post(url, json={'calificacion': None})

    assert first['item']['calificacion'] == 'interesa'
    assert sb.tables['ficha_coleccion_items'][0]['calificacion'] is None


async def test_rating_rejects_unknown_values() -> None:
    sb = _MemSupabase({'properties': _props('a')})

    async with _client(sb) as c:
        col = await _crear(c, ['a'])
        resp = await c.post(
            f"/ficha-colecciones/{col['id']}/items/a/calificacion", json={'calificacion': 'tal vez'},
        )

    assert resp.status_code == 400


async def test_rating_an_item_outside_the_collection_is_404() -> None:
    sb = _MemSupabase({'properties': _props('a')})

    async with _client(sb) as c:
        col = await _crear(c, ['a'])
        resp = await c.post(
            f"/ficha-colecciones/{col['id']}/items/zzz/calificacion", json={'calificacion': 'interesa'},
        )

    assert resp.status_code == 404


# ── enviar / borrar ─────────────────────────────────────────────────────────


async def test_mark_sent_sets_and_clears_the_timestamp() -> None:
    sb = _MemSupabase({'properties': _props('a')})

    async with _client(sb) as c:
        col = await _crear(c, ['a'])
        sent = (await c.post(f"/ficha-colecciones/{col['id']}/enviada", json={'enviada': True})).json()
        assert sent['coleccion']['enviada_at']
        cleared = (await c.post(f"/ficha-colecciones/{col['id']}/enviada", json={'enviada': False})).json()

    assert cleared['coleccion']['enviada_at'] is None


async def test_delete_archives_instead_of_removing_the_row() -> None:
    """Borrar desde /ficha-propio la saca de la UI, pero el link que ya tiene
    el cliente tiene que seguir andando: es un borrado lógico."""
    sb = _MemSupabase({'properties': _props('a')})

    async with _client(sb) as c:
        col = await _crear(c, ['a'])
        body = (await c.delete(f"/ficha-colecciones/{col['id']}")).json()

    assert body['deleted'] is True
    (row,) = sb.tables['ficha_colecciones']
    assert row['archivada_at']
    assert len(sb.tables['ficha_coleccion_items']) == 1
    assert len(sb.tables['properties']) == 1


async def test_archived_selection_is_hidden_from_the_list() -> None:
    sb = _MemSupabase({'properties': _props('a', 'b')})

    async with _client(sb) as c:
        borrada = await _crear(c, ['a'])
        viva = await _crear(c, ['b'])
        await c.delete(f"/ficha-colecciones/{borrada['id']}")
        body = (await c.get('/ficha-colecciones')).json()

    assert [x['id'] for x in body['colecciones']] == [viva['id']]
    assert body['total'] == 1


async def test_archived_selection_link_still_works_for_the_client() -> None:
    sb = _MemSupabase({'properties': _props('a')})

    async with _client(sb) as c:
        col = await _crear(c, ['a'])
        await c.delete(f"/ficha-colecciones/{col['id']}")
        detalle = await c.get(f"/ficha-colecciones/{col['id']}")
        voto = await c.post(
            f"/ficha-colecciones/{col['id']}/items/a/calificacion",
            json={'calificacion': 'interesa'},
        )

    assert detalle.status_code == 200
    assert [i['property']['id'] for i in detalle.json()['items']] == ['a']
    assert voto.status_code == 200


# ── degradación ─────────────────────────────────────────────────────────────


async def test_without_supabase_list_answers_with_an_error_field() -> None:
    async with _client(None) as c:
        body = (await c.get('/ficha-colecciones')).json()

    assert body['colecciones'] == []
    assert body['error']
