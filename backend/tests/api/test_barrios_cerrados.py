"""Test-first for `/api/v1/barrios-cerrados` — the hand-loaded catalogue of
gated communities, plus the per-portal probe that answers "how does this
barrio figure on each portal?".

Shape mirrors `test_saved_zones.py` (fluent fake Supabase), with one addition:
this router touches TWO tables, and the probe has to reconcile new answers
against rows a human already confirmed.

THE RULE THAT MATTERS. A re-probe keeps `confirmed` only when the ref came
back IDENTICAL. If a portal now answers something else, the human's old "yes,
that is my barrio" was about a different ref and cannot carry over — a stale
confirmation is worse than no confirmation, because the UI stops flagging it
for review and the scraper keeps filtering on a handle that moved.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


class _Res:
    def __init__(self, data) -> None:
        self.data = data


class _FakeQuery:
    def __init__(self, store: list[dict], mode: str) -> None:
        self._store = store
        self._mode = mode
        self._filters: list[tuple[str, object]] = []
        self._order_field: str | None = None
        self._order_desc = False
        self._payload = None

    def select(self, *_a, **_kw) -> '_FakeQuery':
        return self

    def insert(self, payload) -> '_FakeQuery':
        self._payload = payload
        return self

    def update(self, payload: dict) -> '_FakeQuery':
        self._payload = payload
        return self

    def delete(self) -> '_FakeQuery':
        return self

    def eq(self, field: str, value) -> '_FakeQuery':
        self._filters.append((field, value))
        return self

    def order(self, field: str, desc: bool = False) -> '_FakeQuery':
        self._order_field = field
        self._order_desc = desc
        return self

    def _match(self, row: dict) -> bool:
        return all(row.get(f) == v for f, v in self._filters)

    async def execute(self) -> _Res:
        if self._mode == 'insert':
            payload = self._payload
            rows = payload if isinstance(payload, list) else [payload]
            out = []
            for raw in rows:
                row = dict(raw or {})
                row.setdefault('id', f'id-{uuid.uuid4().hex[:8]}')
                row.setdefault('created_at', datetime.now(timezone.utc).isoformat())
                self._store.append(row)
                out.append(row)
            return _Res(out)

        matched = [r for r in self._store if self._match(r)]
        if self._mode == 'update':
            for r in matched:
                r.update(self._payload or {})
            return _Res(matched)
        if self._mode == 'delete':
            for r in matched:
                self._store.remove(r)
            return _Res(matched)

        rows = matched
        if self._order_field:
            rows = sorted(rows, key=lambda r: r.get(self._order_field) or '',
                          reverse=self._order_desc)
        return _Res(rows)


class _FakeTable:
    def __init__(self, store: list[dict]) -> None:
        self._store = store

    def select(self, *a, **kw) -> _FakeQuery:
        return _FakeQuery(self._store, 'select')

    def insert(self, payload) -> _FakeQuery:
        return _FakeQuery(self._store, 'insert').insert(payload)

    def update(self, payload: dict) -> _FakeQuery:
        return _FakeQuery(self._store, 'update').update(payload)

    def delete(self) -> _FakeQuery:
        return _FakeQuery(self._store, 'delete')


class _FakeSupabase:
    def __init__(self) -> None:
        self.barrios: list[dict] = []
        self.refs: list[dict] = []

    def table(self, name: str) -> _FakeTable:
        if name == 'barrios_cerrados':
            return _FakeTable(self.barrios)
        if name == 'barrio_cerrado_portal_refs':
            return _FakeTable(self.refs)
        raise AssertionError(f'tabla inesperada: {name}')


@pytest.fixture()
def sb() -> _FakeSupabase:
    return _FakeSupabase()


@pytest.fixture()
def client(sb: _FakeSupabase) -> AsyncClient:
    from app.api.v1 import barrios_cerrados

    app = FastAPI()
    app.include_router(barrios_cerrados.router, prefix='/barrios-cerrados')
    app.state.supabase = sb
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


@pytest.fixture(autouse=True)
def _probe_estable(monkeypatch):
    """One deterministic probe answer, so these tests are about the ENDPOINT.

    `test_barrio_probe.py` owns the resolution logic; duplicating it here would
    just make both files fragile.
    """
    from app.services.barrio_probe import PortalRef

    async def _fake(nombre: str, localidad: str) -> list[PortalRef]:
        return [
            PortalRef('remax', 'native', 'in::::::2439:', 'Grand Bell, City Bell',
                      None, False, 'RE/MAX lo tiene como barrio privado (slot 5).'),
            PortalRef('mudafy', 'localidad', None, None, None, False,
                      'mudafy no publica páginas de barrio cerrado.'),
        ]

    from app.api.v1 import barrios_cerrados
    monkeypatch.setattr(barrios_cerrados, 'probe_barrio', _fake)


async def _crear(client: AsyncClient, **over) -> dict:
    body = {'nombre': 'Grand Bell', 'localidad': 'City Bell, La Plata'} | over
    res = await client.post('/barrios-cerrados', json=body)
    return res.json()['barrio']


class TestCarga:
    async def test_alta_minima(self, client):
        barrio = await _crear(client)
        assert barrio['nombre'] == 'Grand Bell'
        assert barrio['localidad'] == 'City Bell, La Plata'

    async def test_la_localidad_es_obligatoria(self, client):
        """Sin localidad la cadena de candidatos no tiene por dónde degradar y
        el barrio queda inbuscable en cualquier portal que no lo resuelva —
        que es justo el problema que este catálogo existe para arreglar."""
        res = await client.post('/barrios-cerrados', json={'nombre': 'Grand Bell'})
        assert res.json()['barrio'] is None
        assert 'localidad' in res.json()['error']

    async def test_el_nombre_es_obligatorio(self, client):
        res = await client.post(
            '/barrios-cerrados', json={'nombre': '  ', 'localidad': 'La Plata'})
        assert res.json()['barrio'] is None

    async def test_kind_invalido_se_rechaza(self, client):
        res = await client.post('/barrios-cerrados', json={
            'nombre': 'X', 'localidad': 'La Plata', 'kind': 'shopping'})
        assert res.json()['barrio'] is None

    async def test_los_alias_generados_vienen_en_la_respuesta(self, client):
        """El operador tiene que VER contra qué se va a filtrar antes de
        confiar en el barrio; si no, el filtro es una caja negra."""
        barrio = await _crear(client)
        assert 'barrio cerrado grand bell' in barrio['aliases_efectivos']

    async def test_los_alias_manuales_se_suman_a_los_generados(self, client):
        barrio = await _crear(client, aliases=['Grand Bell II'])
        assert 'grand bell ii' in barrio['aliases_efectivos']
        assert 'grand bell' in barrio['aliases_efectivos']

    async def test_la_zona_compuesta_viene_en_la_respuesta(self, client):
        """Es la string que el pipeline de zonas va a caminar. Mostrarla es lo
        que hace auditable "por dónde va a degradar esta búsqueda"."""
        barrio = await _crear(client)
        assert barrio['zona'] == 'Grand Bell, City Bell, La Plata'

    async def test_un_poligono_invalido_se_rechaza(self, client):
        res = await client.post('/barrios-cerrados', json={
            'nombre': 'X', 'localidad': 'La Plata', 'polygon': [[0, 0], [1, 1]]})
        assert res.json()['barrio'] is None

    async def test_un_poligono_valido_se_guarda(self, client):
        poly = [[-34.60, -58.38], [-34.61, -58.38], [-34.61, -58.39]]
        barrio = await _crear(client, polygon=poly)
        assert barrio['polygon'] == poly


class TestListado:
    async def test_lista_lo_cargado(self, client):
        await _crear(client)
        await _crear(client, nombre='Haras del Sur', localidad='La Plata')
        res = await client.get('/barrios-cerrados')
        assert res.json()['total'] == 2

    async def test_cada_fila_trae_sus_refs_por_portal(self, client):
        """La pregunta original — "cómo figura en cada portal" — se contesta en
        el listado, no escondida detrás de otro request por barrio."""
        barrio = await _crear(client)
        await client.post(f'/barrios-cerrados/{barrio["id"]}/probe')
        res = await client.get('/barrios-cerrados')
        fila = res.json()['barrios'][0]
        assert {r['portal'] for r in fila['portal_refs']} == {'remax', 'mudafy'}


class TestProbe:
    async def test_el_probe_persiste_una_fila_por_portal(self, client, sb):
        barrio = await _crear(client)
        res = await client.post(f'/barrios-cerrados/{barrio["id"]}/probe')
        assert {r['portal'] for r in res.json()['portal_refs']} == {'remax', 'mudafy'}
        assert len(sb.refs) == 2

    async def test_el_probe_no_duplica_al_repetirse(self, client, sb):
        barrio = await _crear(client)
        await client.post(f'/barrios-cerrados/{barrio["id"]}/probe')
        await client.post(f'/barrios-cerrados/{barrio["id"]}/probe')
        assert len(sb.refs) == 2

    async def test_probe_de_un_barrio_inexistente(self, client):
        res = await client.post(f'/barrios-cerrados/{uuid.uuid4()}/probe')
        assert res.json()['portal_refs'] == []
        assert res.json()['error']


class TestConfirmacion:
    async def test_se_puede_confirmar_una_ref(self, client):
        barrio = await _crear(client)
        await client.post(f'/barrios-cerrados/{barrio["id"]}/probe')
        res = await client.patch(
            f'/barrios-cerrados/{barrio["id"]}/refs/remax', json={'confirmed': True})
        assert res.json()['ref']['confirmed'] is True

    async def test_se_puede_corregir_la_ref_a_mano(self, client):
        """El escape hatch que hace usable todo esto: cuando el portal resuelve
        mal y el operador SABE el handle correcto, lo escribe."""
        barrio = await _crear(client)
        await client.post(f'/barrios-cerrados/{barrio["id"]}/probe')
        res = await client.patch(
            f'/barrios-cerrados/{barrio["id"]}/refs/remax',
            json={'ref': 'in::::::9999:', 'confirmed': True})
        assert res.json()['ref']['ref'] == 'in::::::9999:'

    async def test_un_reprobe_conserva_la_confirmacion_si_la_ref_no_cambio(
            self, client, sb):
        barrio = await _crear(client)
        await client.post(f'/barrios-cerrados/{barrio["id"]}/probe')
        await client.patch(
            f'/barrios-cerrados/{barrio["id"]}/refs/remax', json={'confirmed': True})
        await client.post(f'/barrios-cerrados/{barrio["id"]}/probe')
        remax = next(r for r in sb.refs if r['portal'] == 'remax')
        assert remax['confirmed'] is True

    async def test_un_reprobe_DESCONFIRMA_si_la_ref_cambio(self, client, sb):
        """El caso que importa: el "sí, ese es mi barrio" del humano era sobre
        OTRA ref. Arrastrarlo deja al scraper filtrando por un handle que se
        movió, y a la UI sin marcarlo para revisión."""
        barrio = await _crear(client)
        await client.post(f'/barrios-cerrados/{barrio["id"]}/probe')
        await client.patch(
            f'/barrios-cerrados/{barrio["id"]}/refs/remax',
            json={'ref': 'in::::::1111:', 'confirmed': True})
        await client.post(f'/barrios-cerrados/{barrio["id"]}/probe')
        remax = next(r for r in sb.refs if r['portal'] == 'remax')
        assert remax['ref'] == 'in::::::2439:'
        assert remax['confirmed'] is False


class TestEdicionYBaja:
    async def test_renombrar_recalcula_los_alias(self, client):
        barrio = await _crear(client)
        res = await client.patch(
            f'/barrios-cerrados/{barrio["id"]}', json={'nombre': 'Los Ceibos'})
        assert 'los ceibos' in res.json()['barrio']['aliases_efectivos']

    async def test_desactivar_no_borra(self, client, sb):
        barrio = await _crear(client)
        await client.patch(f'/barrios-cerrados/{barrio["id"]}', json={'activo': False})
        assert len(sb.barrios) == 1
        assert sb.barrios[0]['activo'] is False

    async def test_borrar_es_idempotente(self, client):
        res = await client.delete(f'/barrios-cerrados/{uuid.uuid4()}')
        assert res.json()['deleted'] is True


class TestSinSupabase:
    async def test_responde_con_error_no_con_500(self, client):
        from app.api.v1 import barrios_cerrados  # noqa: F401

        client._transport.app.state.supabase = None
        res = await client.get('/barrios-cerrados')
        assert res.status_code == 200
        assert res.json()['error']
