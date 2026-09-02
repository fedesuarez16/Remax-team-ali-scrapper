"""Buscar SÓLO en las inmobiliarias cargadas a mano en /sources.

Descubrir con Google Maps es lo que llena la búsqueda de inmobiliarias que
nadie eligió: 390 de una zona, con lo que cuesta scrapearlas y analizarlas. El
registro curado es lo contrario — alguien las cargó una por una porque las
conoce.

Hasta ahora eso se conseguía de rebote: elegir una `zona_inmobiliarias` apagaba
el descubrimiento como EFECTO SECUNDARIO. Un flag que hace dos cosas es un flag
que no se puede usar para una sola: no había forma de decir "sólo las cargadas,
en cualquier zona".

Y una segunda cosa, medida sobre la base real: las 248 fuentes cargadas tienen
`zona_norm` en NULL. Filtrar el registro por zona las borraba a todas — una
fuente que el operador cargó sin zona vale para toda búsqueda, no para ninguna.
"""
from typing import Any

import pytest

from app.graphs.extraction.nodes import _fetch_active_manual_sources, _read_selection


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

    async def execute(self) -> Any:
        class _Res:
            data = self._rows
        return _Res()


def _src(nombre: str, zona_norm: str | None) -> dict[str, Any]:
    return {'id': nombre, 'nombre': nombre, 'url': f'https://{nombre}.com', 'zona': '', 'zona_norm': zona_norm}


# Como está la base real: todas sin zona.
_SIN_ZONA = [_src(f'inmo{i}', None) for i in range(5)]


# ── La regresión: filtrar por zona borraba el registro entero ─────────────────

async def test_una_fuente_sin_zona_entra_en_cualquier_busqueda() -> None:
    """248 de 248 están así. Scopearlas por zona las dejaba en cero."""
    sb = _FakeSupabase(_SIN_ZONA)

    fuentes = await _fetch_active_manual_sources(sb, 'City Bell', incluir_sin_zona=True)

    assert len(fuentes) == 5


async def test_una_fuente_CON_zona_sigue_respetando_su_zona() -> None:
    """Lo que la zona sí decide: una cargada para City Bell no entra en una
    búsqueda de Tolosa."""
    sb = _FakeSupabase([_src('de-city-bell', 'city bell'), _src('sin-zona', None)])

    nombres = [f['nombre'] for f in await _fetch_active_manual_sources(sb, 'Tolosa', incluir_sin_zona=True)]

    assert nombres == ['sin-zona']


async def test_sin_zona_pedida_entran_todas() -> None:
    sb = _FakeSupabase([_src('de-city-bell', 'city bell'), _src('sin-zona', None)])

    assert len(await _fetch_active_manual_sources(sb, None)) == 2


# ── El flag nuevo ─────────────────────────────────────────────────────────────

def test_por_defecto_se_sigue_descubriendo() -> None:
    """El default no puede cambiarle la búsqueda a quien no pidió nada."""
    assert _read_selection({})['solo_fuentes_cargadas'] is False


def test_el_flag_se_lee_de_la_seleccion() -> None:
    sel = {'source_selection': {'solo_fuentes_cargadas': True}}

    assert _read_selection(sel)['solo_fuentes_cargadas'] is True


def test_con_el_flag_no_se_descubre_por_google_maps() -> None:
    """El punto de todo esto: sin descubrimiento, la única fuente de
    inmobiliarias es el registro que el operador cargó."""
    from app.graphs.extraction.nodes import _hay_que_descubrir_agencias

    sel = _read_selection({'source_selection': {'solo_fuentes_cargadas': True}})

    assert _hay_que_descubrir_agencias(sel) is False


def test_sin_el_flag_y_sin_zona_se_descubre_como_siempre() -> None:
    from app.graphs.extraction.nodes import _hay_que_descubrir_agencias

    assert _hay_que_descubrir_agencias(_read_selection({})) is True


def test_elegir_una_zona_sigue_apagando_el_descubrimiento() -> None:
    """El comportamiento que ya existía no se pierde: una corrida acotada a una
    zona consulta sólo el registro curado de esa zona."""
    from app.graphs.extraction.nodes import _hay_que_descubrir_agencias

    sel = _read_selection({'source_selection': {'zona_inmobiliarias': 'City Bell'}})

    assert _hay_que_descubrir_agencias(sel) is False


def test_sin_buscar_inmobiliarias_no_se_descubre_nada() -> None:
    from app.graphs.extraction.nodes import _hay_que_descubrir_agencias

    sel = _read_selection({'source_selection': {'buscar_inmobiliarias': False}})

    assert _hay_que_descubrir_agencias(sel) is False
