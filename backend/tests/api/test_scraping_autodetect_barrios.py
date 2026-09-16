"""El catálogo de barrios cerrados se aplica solo, desde el texto de la query.

POR QUÉ EXISTE ESTO. `barrios_cerrados` llegaba únicamente como ids explícitos
en el body de `POST /scraping/start`, y NADA en el frontend los manda — el body
lleva `query`, `polygon`, `localidades` y `source_selection`, y nada más. El
catálogo entero, con su probe de refs por portal, estaba construido y
desconectado del buscador: dar de alta un club de campo no podía cambiar el
resultado de una sola búsqueda.

Medido en vivo (2026-09-09) sobre "casas en venta en club de campo miralagos":
la query llegaba como texto libre, `zona_candidates` la devolvía como UNA sola
cadena sin localidad (`['club de campo miralagos']`), ningún portal la resolvía
y la búsqueda terminaba en cero. Con la fila del catálogo aplicada,
`barrio_zona` empalma la localidad (`'miralagos, La Plata'`), la cadena tiene un
segundo eslabón y las refs confirmadas del probe entran a jugar.

Los ids explícitos siguen ganando: son una elección del operador sobre una fila
concreta, y adivinar por texto encima de eso sería pisarla.
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

    def eq(self, field: str, value) -> '_Query':
        self._filters.append((field, value))
        return self

    def in_(self, field: str, values) -> '_Query':
        self._filters.append((field, list(values)))
        return self

    async def execute(self) -> _Res:
        self._sb.selects.append((self._tabla, self._cols))
        for col in self._cols.split(','):
            if col.strip() in self._sb.columnas_faltantes:
                raise RuntimeError(f'column "{col.strip()}" does not exist')
        if self._tabla in self._sb.tablas_rotas:
            raise RuntimeError(f'{self._tabla} unavailable')
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
        self.tablas_rotas: set[str] = set()

    def table(self, name: str) -> _Query:
        return _Query(self, name)


def _job(query_raw: str, barrios=None) -> dict:
    return {
        'id': 'job-1', 'localidades': None, 'polygon': None,
        'source_selection': None, 'barrios_cerrados': barrios,
        'query_raw': query_raw,
    }


@pytest.fixture()
def sb() -> _FakeSupabase:
    catalogo = _FakeSupabase()
    catalogo.rows['barrios_cerrados'] = [
        {'id': 'b1', 'nombre': 'miralagos', 'localidad': 'La Plata',
         'kind': 'club_de_campo', 'aliases': [], 'activo': True},
        {'id': 'b2', 'nombre': 'Grand Bell', 'localidad': 'City Bell, La Plata',
         'kind': 'barrio_cerrado', 'aliases': [], 'activo': True},
    ]
    return catalogo


class TestElTextoDeLaQueryAplicaElCatalogo:
    async def test_el_nombre_pelado_encuentra_la_fila(self, sb):
        from app.api.v1.scraping import _read_job_inputs

        sb.rows['scraping_jobs'] = [_job('casas en venta en miralagos')]
        inputs = await _read_job_inputs(sb, 'job-1')
        assert [b['nombre'] for b in inputs['barrios_cerrados']] == ['miralagos']

    async def test_el_nombre_con_su_tipo_adelante_tambien(self, sb):
        """`barrio_aliases` genera la forma con el kind prefijado justamente
        porque nadie escribe el nombre pelado: se dice "el club de campo
        Miralagos", no "Miralagos" a secas."""
        from app.api.v1.scraping import _read_job_inputs

        sb.rows['scraping_jobs'] = [_job('casas en venta en club de campo miralagos')]
        inputs = await _read_job_inputs(sb, 'job-1')
        assert [b['nombre'] for b in inputs['barrios_cerrados']] == ['miralagos']

    async def test_la_fila_viaja_con_sus_refs_confirmadas(self, sb):
        """Sin las refs el probe vuelve a ser decorativo: la búsqueda camina la
        cadena de candidatos y nunca usa la página nativa que ya encontró."""
        from app.api.v1.scraping import _read_job_inputs

        sb.rows['scraping_jobs'] = [_job('casas en miralagos')]
        sb.rows['barrio_cerrado_portal_refs'] = [
            {'barrio_id': 'b1', 'portal': 'remax', 'strategy': 'native',
             'ref': 'in::::::20009:', 'confirmed': True},
            {'barrio_id': 'b2', 'portal': 'argenprop', 'strategy': 'native',
             'ref': 'grand-bell', 'confirmed': True},
        ]
        inputs = await _read_job_inputs(sb, 'job-1')
        refs = inputs['barrios_cerrados'][0]['portal_refs']
        assert [r['portal'] for r in refs] == ['remax']

    async def test_una_query_que_no_nombra_ningun_barrio_no_inyecta_nada(self, sb):
        """La clave AUSENTE, no una lista vacía: `route_after_parse` lee
        `state.get('barrios_cerrados') or []` y una búsqueda de localidad tiene
        que quedar exactamente como estaba."""
        from app.api.v1.scraping import _read_job_inputs

        sb.rows['scraping_jobs'] = [_job('casas en venta en City Bell')]
        inputs = await _read_job_inputs(sb, 'job-1')
        assert 'barrios_cerrados' not in inputs

    async def test_dos_barrios_nombrados_se_buscan_los_dos(self, sb):
        """El fan-out por barrio ya existe (`route_after_parse` itera la
        lista). Nombrar dos es una búsqueda de dos, no una ambigüedad."""
        from app.api.v1.scraping import _read_job_inputs

        sb.rows['scraping_jobs'] = [_job('casas en miralagos o en Grand Bell')]
        inputs = await _read_job_inputs(sb, 'job-1')
        assert sorted(b['nombre'] for b in inputs['barrios_cerrados']) == [
            'Grand Bell', 'miralagos']


class TestLosHomonimosNoSeAdivinan:
    async def test_dos_filas_con_el_mismo_nombre_no_aplican_ninguna(self, sb):
        """Argenprop solo sirve un "Los Ceibos" en Tigre, La Plata, Córdoba,
        Corrientes y González Catán. Si el catálogo tiene dos filas con el
        mismo nombre, el texto no alcanza para elegir — y elegir mal manda la
        búsqueda a otra provincia. La degradación honesta es una búsqueda de
        zona normal, que es lo que había antes de este cambio.
        """
        from app.api.v1.scraping import _read_job_inputs

        sb.rows['barrios_cerrados'] = [
            {'id': 'c1', 'nombre': 'Los Ceibos', 'localidad': 'City Bell, La Plata',
             'aliases': [], 'activo': True},
            {'id': 'c2', 'nombre': 'Los Ceibos', 'localidad': 'Tigre',
             'aliases': [], 'activo': True},
        ]
        sb.rows['scraping_jobs'] = [_job('casas en venta en Los Ceibos')]
        inputs = await _read_job_inputs(sb, 'job-1')
        assert 'barrios_cerrados' not in inputs


class TestLaEleccionExplicitaGana:
    async def test_los_ids_del_body_ganan_sobre_el_texto(self, sb):
        """Los ids son una elección del operador sobre una fila concreta.
        Adivinar por texto encima de eso la pisaría."""
        from app.api.v1.scraping import _read_job_inputs

        sb.rows['scraping_jobs'] = [_job('casas en miralagos', barrios=['b2'])]
        inputs = await _read_job_inputs(sb, 'job-1')
        assert [b['nombre'] for b in inputs['barrios_cerrados']] == ['Grand Bell']


class TestLasFilasInactivasNoParticipan:
    async def test_un_barrio_dado_de_baja_no_se_autodetecta(self, sb):
        from app.api.v1.scraping import _read_job_inputs

        sb.rows['barrios_cerrados'] = [
            {'id': 'b1', 'nombre': 'miralagos', 'localidad': 'La Plata',
             'aliases': [], 'activo': False},
        ]
        sb.rows['scraping_jobs'] = [_job('casas en miralagos')]
        inputs = await _read_job_inputs(sb, 'job-1')
        assert 'barrios_cerrados' not in inputs


class TestElCatalogoCaidoNoRompeLaBusqueda:
    async def test_si_el_catalogo_no_se_puede_leer_la_busqueda_sigue(self, sb):
        """Perder la autodetección cuesta precisión; perder la búsqueda cuesta
        todo. Mismo criterio que el resto de los reads de este módulo."""
        from app.api.v1.scraping import _read_job_inputs

        sb.tablas_rotas = {'barrios_cerrados'}
        sb.rows['scraping_jobs'] = [{
            **_job('casas en miralagos'), 'localidades': ['La Plata'],
        }]
        inputs = await _read_job_inputs(sb, 'job-1')
        assert inputs['localidades'] == ['La Plata']
        assert 'barrios_cerrados' not in inputs

    async def test_sin_query_raw_no_se_autodetecta(self, sb):
        """Una fila legacy sin `query_raw` no tiene texto contra el que
        matchear; leerla como cadena vacía haría matchear a cualquiera."""
        from app.api.v1.scraping import _read_job_inputs

        sb.rows['scraping_jobs'] = [_job('')]
        inputs = await _read_job_inputs(sb, 'job-1')
        assert 'barrios_cerrados' not in inputs
