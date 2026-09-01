"""El caché de inmobiliarias también se filtra por zona al LEER.

La guarda en `_norm_googlemaps_agency` sólo protege lo que entra de acá en
adelante. Las inmobiliarias que ya están guardadas bajo `zona_norm` con la zona
equivocada — las que se persistieron mientras el normalizador no miraba la
dirección — siguen volviendo intactas, y volviendo SIEMPRE:

    cached = await _read_cached_agencies(sb, zona_norm)
    if len(cached) >= _AGENCY_CACHE_MIN_ROWS:   # 1
        return {'agencies': cached}             # ni llama a Apify

Con una sola fila fresca la búsqueda se sirve del caché entera, y el TTL es de
30 días. O sea que sin filtrar la lectura, arreglar el normalizador no cambia
nada de lo que el usuario ve hasta dentro de un mes.

Filtrar al leer lo arregla hoy y sin un DELETE en producción: las filas siguen
ahí, simplemente dejan de contestar por una zona que no es la suya. Es la misma
guarda y el mismo campo (`direccion`) que usa la escritura — si leyera con un
criterio distinto al que escribe, el caché se comportaría distinto según quién
lo llenó.
"""
from typing import Any

import pytest

from app.graphs.extraction.nodes import _read_cached_agencies


class _FakeSupabase:
    """Devuelve filas crudas sin importar el filtro; lo que se prueba es lo que
    hace el código DESPUÉS de recibirlas."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def table(self, _name: str) -> '_FakeSupabase':
        return self

    def select(self, *_a: Any, **_kw: Any) -> '_FakeSupabase':
        return self

    def eq(self, *_a: Any, **_kw: Any) -> '_FakeSupabase':
        return self

    def gte(self, *_a: Any, **_kw: Any) -> '_FakeSupabase':
        return self

    async def execute(self) -> Any:
        class _Res:
            data = self._rows
        return _Res()


def _row(nombre: str, direccion: str | None) -> dict[str, Any]:
    return {
        'id': f'id-{nombre}', 'nombre': nombre, 'direccion': direccion,
        'telefono': None, 'sitio_web': f'https://{nombre}.com', 'google_maps_url': None,
        'instagram_handle': None, 'calificacion': None, 'zona': 'La Plata',
    }


_SUCIO = [
    _row('del-bosque', 'Calle 50 456, La Plata, Buenos Aires'),
    _row('inmo-berisso', 'Av. Montevideo 820, Berisso, Buenos Aires'),
    _row('inmo-ensenada', 'Av. Bossinga 100, Ensenada, Buenos Aires'),
    _row('sin-datos', None),
]


async def test_el_cache_sucio_deja_de_contestar_por_una_zona_que_no_es_la_suya() -> None:
    sb = _FakeSupabase(_SUCIO)

    agencies = await _read_cached_agencies(sb, 'la plata', 'La Plata')

    assert [a.nombre for a in agencies] == ['del-bosque']


async def test_una_fila_sin_direccion_tampoco_sobrevive_a_la_lectura() -> None:
    """Mismo criterio que la escritura. Si la lectura fuera más permisiva, una
    fila que hoy no se guardaría seguiría contestando desde el caché."""
    sb = _FakeSupabase([_row('sin-datos', None)])

    assert await _read_cached_agencies(sb, 'la plata', 'La Plata') == []


async def test_sin_zona_la_lectura_no_filtra() -> None:
    """El `zona_norm` de la query ya acotó la fila; sin una zona legible que
    exigir, la guarda no tiene criterio y no debe inventarse uno."""
    sb = _FakeSupabase(_SUCIO)

    agencies = await _read_cached_agencies(sb, 'la plata', '')

    assert len(agencies) == len(_SUCIO)


async def test_el_cache_filtrado_a_cero_no_se_sirve_como_caché_lleno() -> None:
    """El contrato que hace que esto sirva de algo: si después de filtrar no
    queda ninguna, `discover_agencies` tiene que ver una lista vacía y salir a
    buscar de nuevo — no servir un caché que quedó en cero."""
    sb = _FakeSupabase([_row('inmo-berisso', 'Av. Montevideo 820, Berisso')])

    assert await _read_cached_agencies(sb, 'la plata', 'La Plata') == []


@pytest.mark.parametrize('sb', [None])
async def test_sin_supabase_sigue_devolviendo_vacio(sb: Any) -> None:
    assert await _read_cached_agencies(sb, 'la plata', 'La Plata') == []
