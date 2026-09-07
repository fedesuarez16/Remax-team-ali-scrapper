"""`POST /scraping/start` accepts gated communities from the catalogue, and the
stream hands the graph the ROWS, not the ids.

WHY ROWS AND NOT IDS. `route_after_parse` needs `nombre`, `localidad` and
`aliases` to build a fan-out unit — the localidad is what gives the search a
chain to degrade through, and without it a country is unsearchable on any
portal that does not resolve it natively. Resolving the ids inside the graph
would put a Supabase read in a pure routing function; resolving them at the
edge keeps the graph a function of its inputs, same as `localidades`.

The `_read_job_inputs` fallback matters as much as the happy path: this ships
before the column exists everywhere, and a search that 500s because a SELECT
named an unapplied column is a worse regression than one that ignores a barrio.
"""
from __future__ import annotations

import pytest


class _Res:
    def __init__(self, data) -> None:
        self.data = data


class _Query:
    def __init__(self, sb: '_FakeSupabase', tabla: str) -> None:
        self._sb = sb
        self._tabla = tabla
        self._cols = ''
        self._filters: list[tuple[str, object]] = []

    def select(self, cols: str = '*', *_a, **_kw) -> '_Query':
        self._cols = cols
        return self

    def insert(self, payload) -> '_Query':
        self._sb.rows.setdefault(self._tabla, []).append(dict(payload))
        return self

    def eq(self, field: str, value) -> '_Query':
        self._filters.append((field, value))
        return self

    def in_(self, field: str, values) -> '_Query':
        self._filters.append((field, list(values)))
        return self

    async def execute(self) -> _Res:
        self._sb.selects.append((self._tabla, self._cols))
        # Emulate PostgREST rejecting a column the deployment has not migrated.
        for col in self._cols.split(','):
            if col.strip() in self._sb.columnas_faltantes:
                raise RuntimeError(f'column "{col.strip()}" does not exist')
        rows = self._sb.rows.get(self._tabla, [])
        for field, value in self._filters:
            if isinstance(value, list):
                rows = [r for r in rows if r.get(field) in value]
            else:
                rows = [r for r in rows if r.get(field) == value]
        return _Res(rows)


class _FakeSupabase:
    def __init__(self) -> None:
        self.rows: dict[str, list[dict]] = {}
        self.selects: list[tuple[str, str]] = []
        self.columnas_faltantes: set[str] = set()

    def table(self, name: str) -> _Query:
        return _Query(self, name)


@pytest.fixture()
def sb() -> _FakeSupabase:
    return _FakeSupabase()


class TestStartPersisteLosBarrios:
    async def test_los_ids_van_a_la_fila_del_job(self, sb):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.v1 import scraping

        app = FastAPI()
        app.include_router(scraping.router, prefix='/scraping')
        app.state.supabase = sb
        client = AsyncClient(transport=ASGITransport(app=app), base_url='http://test')

        res = await client.post('/scraping/start', json={
            'query': 'casas en venta', 'barrios_cerrados': ['b1', 'b2']})
        assert res.status_code == 200
        assert sb.rows['scraping_jobs'][0]['barrios_cerrados'] == ['b1', 'b2']

    async def test_sin_barrios_la_columna_queda_en_null(self, sb):
        """`None`, no `[]`: la fila legacy y la fila sin barrios tienen que
        leerse igual, y `_read_job_inputs` omite las claves falsy."""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.v1 import scraping

        app = FastAPI()
        app.include_router(scraping.router, prefix='/scraping')
        app.state.supabase = sb
        client = AsyncClient(transport=ASGITransport(app=app), base_url='http://test')

        await client.post('/scraping/start', json={'query': 'casas'})
        assert sb.rows['scraping_jobs'][0]['barrios_cerrados'] is None


class TestElStreamInyectaLasFilas:
    async def test_los_ids_se_resuelven_a_filas_del_catalogo(self, sb):
        from app.api.v1.scraping import _read_job_inputs

        sb.rows['scraping_jobs'] = [{
            'id': 'job-1', 'localidades': None, 'polygon': None,
            'source_selection': None, 'barrios_cerrados': ['b1'],
        }]
        sb.rows['barrios_cerrados'] = [
            {'id': 'b1', 'nombre': 'Grand Bell', 'localidad': 'City Bell, La Plata',
             'aliases': [], 'activo': True},
            {'id': 'b2', 'nombre': 'Otro', 'localidad': 'La Plata',
             'aliases': [], 'activo': True},
        ]
        inputs = await _read_job_inputs(sb, 'job-1')
        assert [b['nombre'] for b in inputs['barrios_cerrados']] == ['Grand Bell']

    async def test_un_job_sin_barrios_no_toca_el_catalogo(self, sb):
        """Un SELECT de más por búsqueda, en el camino que NUNCA usa barrios,
        es puro costo. La clave ausente también deja `inputs` byte-idéntico al
        de antes de este cambio, que es lo que protege al camino de chat."""
        from app.api.v1.scraping import _read_job_inputs

        sb.rows['scraping_jobs'] = [{
            'id': 'job-1', 'localidades': ['City Bell'], 'polygon': None,
            'source_selection': None, 'barrios_cerrados': None,
        }]
        inputs = await _read_job_inputs(sb, 'job-1')
        assert 'barrios_cerrados' not in inputs
        assert ('barrios_cerrados', '*') not in sb.selects


class TestLaColumnaPuedeNoExistirTodavia:
    async def test_el_select_degrada_en_vez_de_romper(self, sb):
        """Sin la migración aplicada, PostgREST rechaza el SELECT entero. Una
        búsqueda que muere por eso es peor regresión que una que ignora los
        barrios — mismo razonamiento que el fallback de `source_selection`."""
        from app.api.v1.scraping import _read_job_inputs

        sb.columnas_faltantes = {'barrios_cerrados'}
        sb.rows['scraping_jobs'] = [{
            'id': 'job-1', 'localidades': ['City Bell'],
            'polygon': None, 'source_selection': None,
        }]
        inputs = await _read_job_inputs(sb, 'job-1')
        assert inputs['localidades'] == ['City Bell']
        assert 'barrios_cerrados' not in inputs
