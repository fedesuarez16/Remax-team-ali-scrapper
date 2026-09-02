"""Una inmobiliaria pertenece a la zona donde ESTÁ, no a la que la encontró.

`zona_norm` guarda la zona que se BUSCÓ cuando la agencia se descubrió. No es
dónde está: es cómo apareció. Filtrar el caché con `.eq('zona_norm', ...)`
convertía un accidente de descubrimiento en el criterio de pertenencia.

Medido sobre la base real (1000 agencias, 2026-09-02):

    'City Bell'               devolvía  19 | la dirección dice  41 | perdía  22
    'Tolosa'                  devolvía  10 | la dirección dice  24 | perdía  14
    'casco urbano, La Plata'  devolvía  12 | la dirección dice 231 | perdía 219

El 95% de las inmobiliarias del casco estaban guardadas y no se veían. Dos
causas, las dos del mismo error:

  - `Barreira Bienes Raíces` tiene dirección en City Bell y `zona_norm`
    'melchor romero', porque esa fue la búsqueda que la encontró.
  - `Dacal` está fichada como 'casco de la plata' y la búsqueda normalizó
    'casco urbano, La Plata' a 'casco urbano, la plata'. Dos strings para el
    mismo lugar.

Ahora la pertenencia la decide la MISMA guarda que ya se usaba después —
`agency_matches_zona` sobre la dirección. `zona_norm` queda para lo único que
sabe: si esta zona ya se buscó alguna vez.
"""
from typing import Any

import pytest

from app.graphs.extraction.nodes import _read_cached_agencies


class _FakeSupabase:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.filtros_eq: list[tuple[str, Any]] = []

    def table(self, _n: str) -> '_FakeSupabase':
        return self

    def select(self, *_a: Any, **_kw: Any) -> '_FakeSupabase':
        return self

    def eq(self, campo: str, valor: Any) -> '_FakeSupabase':
        self.filtros_eq.append((campo, valor))
        return self

    def gte(self, *_a: Any, **_kw: Any) -> '_FakeSupabase':
        return self

    def limit(self, *_a: Any, **_kw: Any) -> '_FakeSupabase':
        return self

    async def execute(self) -> Any:
        class _Res:
            data = self._rows
        return _Res()


def _row(nombre: str, direccion: str, zona_norm: str) -> dict[str, Any]:
    return {
        'id': nombre, 'nombre': nombre, 'direccion': direccion, 'zona_norm': zona_norm,
        'telefono': None, 'sitio_web': f'https://{nombre}.com', 'google_maps_url': None,
        'instagram_handle': None, 'calificacion': None, 'zona': '',
    }


# Filas con la forma exacta de las reales que se estaban perdiendo.
_BASE = [
    _row('barreira', 'C. 14 709, B1896 City Bell, Buenos Aires', 'melchor romero'),
    _row('daufi', '467 esquina 19 n 1295, B1896 City Bell, Buenos Aires', 'city bell'),
    _row('dacal', 'C. 10 602, La Plata, Provincia de Buenos Aires', 'casco de la plata'),
    _row('lejos', 'Av. Colón 1200, Mar del Plata, Buenos Aires', 'mar del plata'),
]


async def test_entra_la_que_esta_en_la_zona_aunque_la_haya_encontrado_otra_busqueda() -> None:
    """El caso de `Barreira`: dirección en City Bell, fichada bajo Melchor
    Romero. 22 como esa se perdían en cada búsqueda de City Bell."""
    sb = _FakeSupabase(_BASE)

    nombres = [a.nombre for a in await _read_cached_agencies(sb, 'city bell', 'City Bell')]

    assert sorted(nombres) == ['barreira', 'daufi']


async def test_las_variantes_de_nombre_de_la_zona_dejan_de_importar() -> None:
    """El caso de `Dacal`: fichada como 'casco de la plata', buscada como
    'casco urbano, la plata'. La dirección no tiene ese problema."""
    sb = _FakeSupabase(_BASE)

    nombres = [a.nombre for a in await _read_cached_agencies(sb, 'casco urbano, la plata', 'casco urbano, La Plata')]

    assert 'dacal' in nombres


async def test_la_de_otra_zona_sigue_afuera() -> None:
    """Aflojar el filtro no puede volverlo inútil."""
    sb = _FakeSupabase(_BASE)

    nombres = [a.nombre for a in await _read_cached_agencies(sb, 'city bell', 'City Bell')]

    assert 'lejos' not in nombres


async def test_ya_no_se_filtra_por_zona_norm_en_la_query() -> None:
    """El contrato explícito: `zona_norm` no decide pertenencia. Si volviera a
    la query, los 219 del casco se pierden de nuevo en silencio."""
    sb = _FakeSupabase(_BASE)

    await _read_cached_agencies(sb, 'city bell', 'City Bell')

    assert not any(campo == 'zona_norm' for campo, _ in sb.filtros_eq)


async def test_sin_zona_legible_no_se_devuelve_todo() -> None:
    """Sin criterio no se puede afirmar pertenencia, y devolver las 1000
    agencias de la base sería peor que devolver ninguna."""
    sb = _FakeSupabase(_BASE)

    assert await _read_cached_agencies(sb, 'city bell', '') == []


async def test_sin_supabase_sigue_devolviendo_vacio() -> None:
    assert await _read_cached_agencies(None, 'city bell', 'City Bell') == []
