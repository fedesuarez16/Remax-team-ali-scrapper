"""Test-first for the bulk import: `GET /barrios-cerrados/import/preview` and
`POST /barrios-cerrados/import`.

TWO STEPS, ON PURPOSE. Preview reads Argenprop and returns candidates; import
writes the subset the operator picked. A one-shot "import everything" would be
faster and wrong, for three reasons this file pins:

  * Argenprop does not know the LOCALIDAD (it labels Grand Bell "Partido de La
    Plata"; the barrio is in City Bell). The default is the partido — correct
    but coarse — and the operator is the only one who can refine it.
  * The autocomplete CAPS at 30 rows with no error, so a big partido comes back
    truncated looking complete. The operator has to see that.
  * The catalogue is already hand-curated. An import that silently re-adds
    barrios someone deactivated is an import that fights its user.
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


class _Query:
    def __init__(self, store: list[dict], mode: str) -> None:
        self._store = store
        self._mode = mode
        self._filters: list[tuple[str, object]] = []
        self._payload = None

    def select(self, *_a, **_kw) -> '_Query':
        return self

    def insert(self, payload) -> '_Query':
        self._payload = payload
        return self

    def update(self, payload) -> '_Query':
        self._payload = payload
        return self

    def delete(self) -> '_Query':
        return self

    def eq(self, field: str, value) -> '_Query':
        self._filters.append((field, value))
        return self

    def order(self, *_a, **_kw) -> '_Query':
        return self

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
        matched = [r for r in self._store
                   if all(r.get(f) == v for f, v in self._filters)]
        if self._mode == 'delete':
            for r in matched:
                self._store.remove(r)
        return _Res(matched)


class _Table:
    def __init__(self, store: list[dict]) -> None:
        self._store = store

    def select(self, *a, **kw) -> _Query:
        return _Query(self._store, 'select')

    def insert(self, payload) -> _Query:
        return _Query(self._store, 'insert').insert(payload)

    def update(self, payload) -> _Query:
        return _Query(self._store, 'update').update(payload)

    def delete(self) -> _Query:
        return _Query(self._store, 'delete')


class _FakeSupabase:
    def __init__(self) -> None:
        self.barrios: list[dict] = []
        self.refs: list[dict] = []

    def table(self, name: str) -> _Table:
        if name == 'barrios_cerrados':
            return _Table(self.barrios)
        if name == 'barrio_cerrado_portal_refs':
            return _Table(self.refs)
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
def descubrimiento(monkeypatch):
    """A stable discovery result — `test_barrio_discovery.py` owns the parsing."""
    from app.services.barrio_probe import BarrioCandidate, BarrioDiscovery

    estado = {
        'resultado': BarrioDiscovery(
            barrios=[
                BarrioCandidate('Grand Bell', 'La Plata',
                                'Grand Bell, Partido de La Plata', 'GRAND-BELL'),
                BarrioCandidate('Haras Del Sur', 'La Plata',
                                'Haras Del Sur, Partido de La Plata', 'HARAS-DEL-SUR'),
            ],
            total_api=8, truncated=False,
        ),
        'pedidos': [],
    }

    async def _fake(partido: str, localidad: str | None = None, **kw) -> BarrioDiscovery:
        estado['pedidos'].append((partido, localidad))
        estado.setdefault('deep', []).append(kw.get('deep', False))
        return estado['resultado']

    from app.api.v1 import barrios_cerrados
    monkeypatch.setattr(barrios_cerrados, 'discover_argenprop_barrios', _fake)
    return estado


class TestPreview:
    async def test_devuelve_los_candidatos(self, client):
        res = await client.get('/barrios-cerrados/import/preview',
                               params={'partido': 'La Plata'})
        assert [b['nombre'] for b in res.json()['barrios']] == [
            'Grand Bell', 'Haras Del Sur']

    async def test_el_preview_no_escribe_nada(self, client, sb):
        """Es la mitad "revisá antes" del flujo. Si escribiera, no habría
        revisión: habría un import con un nombre distinto."""
        await client.get('/barrios-cerrados/import/preview', params={'partido': 'La Plata'})
        assert sb.barrios == []

    async def test_el_partido_es_obligatorio(self, client):
        res = await client.get('/barrios-cerrados/import/preview')
        assert res.status_code == 422

    async def test_la_localidad_se_pasa_al_descubrimiento(self, client, descubrimiento):
        await client.get('/barrios-cerrados/import/preview',
                         params={'partido': 'La Plata', 'localidad': 'City Bell, La Plata'})
        assert descubrimiento['pedidos'][-1] == ('La Plata', 'City Bell, La Plata')

    async def test_marca_los_que_ya_estan_cargados(self, client):
        """Sin esto el operador re-importa lo que ya tenía y no se entera."""
        await client.post('/barrios-cerrados',
                          json={'nombre': 'Grand Bell', 'localidad': 'City Bell, La Plata'})
        res = await client.get('/barrios-cerrados/import/preview',
                               params={'partido': 'La Plata'})
        por_nombre = {b['nombre']: b for b in res.json()['barrios']}
        assert por_nombre['Grand Bell']['ya_existe'] is True
        assert por_nombre['Haras Del Sur']['ya_existe'] is False

    async def test_el_duplicado_se_detecta_sin_importar_acentos_ni_kind(self, client):
        """"Club de Campo Los Ceibos" y "los ceibos" son el mismo lugar. El
        cotejo va por identidad normalizada, no por string crudo."""
        await client.post('/barrios-cerrados',
                          json={'nombre': 'Barrio Cerrado grand bell',
                                'localidad': 'City Bell, La Plata'})
        res = await client.get('/barrios-cerrados/import/preview',
                               params={'partido': 'La Plata'})
        por_nombre = {b['nombre']: b for b in res.json()['barrios']}
        assert por_nombre['Grand Bell']['ya_existe'] is True

    async def test_el_truncamiento_se_avisa(self, client, descubrimiento):
        """El fallo silencioso: la API corta en 30 sin marcador. Un import que
        no lo dice carga un tercio de Pilar y reporta éxito."""
        from app.services.barrio_probe import BarrioDiscovery

        descubrimiento['resultado'] = BarrioDiscovery([], 30, True)
        res = await client.get('/barrios-cerrados/import/preview',
                               params={'partido': 'Pilar'})
        assert res.json()['truncated'] is True
        assert 'Pilar' in res.json()['warning'] or res.json()['warning']

    async def test_sin_truncamiento_no_hay_warning(self, client):
        res = await client.get('/barrios-cerrados/import/preview',
                               params={'partido': 'La Plata'})
        assert not res.json().get('warning')

    async def test_un_error_del_portal_se_reporta(self, client, descubrimiento):
        from app.services.barrio_probe import BarrioDiscovery

        descubrimiento['resultado'] = BarrioDiscovery([], 0, False, error='portal caído')
        res = await client.get('/barrios-cerrados/import/preview',
                               params={'partido': 'La Plata'})
        assert res.json()['error'] == 'portal caído'


class TestImport:
    async def test_carga_los_barrios_elegidos(self, client, sb):
        res = await client.post('/barrios-cerrados/import', json={'barrios': [
            {'nombre': 'Grand Bell', 'localidad': 'City Bell, La Plata',
             'argenprop_ref': 'GRAND-BELL'},
        ]})
        assert res.json()['creados'] == 1
        assert sb.barrios[0]['nombre'] == 'Grand Bell'

    async def test_siembra_la_ref_de_argenprop(self, client, sb):
        """El import ya SABE el `CodigoBarrio` — tirarlo y hacer que el probe
        lo vuelva a pedir es una llamada de red por barrio a cambio de nada."""
        await client.post('/barrios-cerrados/import', json={'barrios': [
            {'nombre': 'Grand Bell', 'localidad': 'City Bell, La Plata',
             'argenprop_ref': 'GRAND-BELL'},
        ]})
        ref = next(r for r in sb.refs if r['portal'] == 'argenprop')
        assert ref['ref'] == 'GRAND-BELL'
        assert ref['strategy'] == 'native'

    async def test_la_ref_sembrada_no_viene_confirmada(self, client, sb):
        """Enumerar no es confirmar. Sigue siendo un humano el que dice "sí,
        ese es mi barrio" — los homónimos son la norma."""
        await client.post('/barrios-cerrados/import', json={'barrios': [
            {'nombre': 'Grand Bell', 'localidad': 'La Plata',
             'argenprop_ref': 'GRAND-BELL'},
        ]})
        assert sb.refs[0]['confirmed'] is False

    async def test_un_barrio_ya_cargado_se_saltea(self, client, sb):
        await client.post('/barrios-cerrados',
                          json={'nombre': 'Grand Bell', 'localidad': 'City Bell, La Plata'})
        res = await client.post('/barrios-cerrados/import', json={'barrios': [
            {'nombre': 'Grand Bell', 'localidad': 'City Bell, La Plata'},
            {'nombre': 'Haras Del Sur', 'localidad': 'La Plata'},
        ]})
        assert res.json()['creados'] == 1
        assert res.json()['salteados'] == 1
        assert len(sb.barrios) == 2

    async def test_un_barrio_sin_localidad_se_rechaza_sin_frenar_el_resto(self, client, sb):
        """Un import parcial es mucho mejor que un all-or-nothing: el operador
        arregla la fila rota, no las veinte buenas."""
        res = await client.post('/barrios-cerrados/import', json={'barrios': [
            {'nombre': 'Roto'},
            {'nombre': 'Grand Bell', 'localidad': 'La Plata'},
        ]})
        assert res.json()['creados'] == 1
        assert len(res.json()['errores']) == 1
        assert 'Roto' in res.json()['errores'][0]

    async def test_una_lista_vacia_no_es_un_error(self, client):
        res = await client.post('/barrios-cerrados/import', json={'barrios': []})
        assert res.json()['creados'] == 0
        assert not res.json().get('error')

    async def test_los_creados_vuelven_decorados(self, client):
        """Mismo shape que el POST de a uno, así la UI no ramifica."""
        res = await client.post('/barrios-cerrados/import', json={'barrios': [
            {'nombre': 'Grand Bell', 'localidad': 'City Bell, La Plata'},
        ]})
        creado = res.json()['barrios'][0]
        assert creado['zona'] == 'Grand Bell, City Bell, La Plata'
        assert 'grand bell' in creado['aliases_efectivos']


class TestRutas:
    async def test_import_no_lo_captura_la_ruta_parametrica(self, client):
        """`/import` y `/{barrio_id}` compiten. Si gana la paramétrica, el
        preview se convierte en "probar el barrio llamado import"."""
        res = await client.get('/barrios-cerrados/import/preview',
                               params={'partido': 'La Plata'})
        assert res.status_code == 200
        assert 'barrios' in res.json()


class TestElWarningEsAccionable:
    async def test_sin_deep_sugiere_el_barrido(self, client, descubrimiento):
        """"Está incompleta" no le sirve a nadie sin el siguiente paso."""
        from app.services.barrio_probe import BarrioDiscovery

        descubrimiento['resultado'] = BarrioDiscovery([], 30, True)
        res = await client.get('/barrios-cerrados/import/preview',
                               params={'partido': 'Pilar'})
        assert 'deep=true' in res.json()['warning']

    async def test_con_deep_ya_no_lo_sugiere(self, client, descubrimiento):
        """Repetir "probá con deep" cuando deep YA corrió manda al operador a
        un loop. Medido: La Plata reporta truncated y el barrido no agrega
        nada (19 en ambos casos) — el tope no prueba que falte algo."""
        from app.services.barrio_probe import BarrioDiscovery

        descubrimiento['resultado'] = BarrioDiscovery([], 30, True)
        res = await client.get('/barrios-cerrados/import/preview',
                               params={'partido': 'Pilar', 'deep': 'true'})
        assert 'deep=true' not in res.json()['warning']
        assert 'a mano' in res.json()['warning']

    async def test_el_flag_deep_llega_al_descubrimiento(self, client, descubrimiento):
        await client.get('/barrios-cerrados/import/preview',
                         params={'partido': 'Pilar', 'deep': 'true'})
        assert descubrimiento['deep'][-1] is True
