"""Entre dos propiedades que coinciden parecido, primero la que tiene fotos.

Una propiedad sin foto es casi invendible en la primera pasada: el operador la
saltea. Pero ordenar por foto ANTES que por coincidencia sería peor todavía —
un 40% con fotos le ganaría a un 95% sin fotos, y el usuario dejaría de ver lo
que realmente pidió.

Por eso la foto entra como DESEMPATE dentro de la banda de coincidencia, no por
encima de ella. Las bandas son de a 10 puntos sobre `match_score` (0-100):

    1. las que no cumplen los criterios, al final (como ya era)
    2. mejor banda de coincidencia primero  (90-99 antes que 80-89)
    3. dentro de la banda, con fotos primero
    4. dentro de eso, el orden exacto que dejó `rank_properties`

Una diferencia de un punto de score no debería decidir cuál de las dos ve
primero el operador; la presencia de fotos sí.
"""
from typing import Any

from app.api.v1.scraping import _photo_aware_sort


def _prop(nombre: str, score: float | None, fotos: int, matches: bool = True) -> dict[str, Any]:
    return {
        'id': nombre, 'match_score': score, 'matches_criteria': matches,
        'imagenes': [f'https://img/{nombre}/{i}.jpg' for i in range(fotos)],
    }


def _orden(props: list[dict[str, Any]]) -> list[str]:
    _photo_aware_sort(props)
    return [p['id'] for p in props]


def test_dentro_de_la_misma_banda_gana_la_que_tiene_fotos() -> None:
    props = [_prop('sin-fotos', 94, 0), _prop('con-fotos', 91, 6)]

    assert _orden(props) == ['con-fotos', 'sin-fotos']


def test_una_banda_mejor_le_gana_a_las_fotos() -> None:
    """El límite del criterio: la coincidencia sigue mandando. Un 95 sin fotos
    va antes que un 60 con fotos — el operador pidió eso."""
    props = [_prop('bajo-con-fotos', 60, 8), _prop('alto-sin-fotos', 95, 0)]

    assert _orden(props) == ['alto-sin-fotos', 'bajo-con-fotos']


def test_dentro_de_la_banda_y_con_fotos_manda_el_score() -> None:
    """El desempate no revuelve lo que `rank_properties` ya ordenó."""
    props = [_prop('b', 92, 3), _prop('a', 97, 3)]

    assert _orden(props) == ['a', 'b']


def test_las_que_no_cumplen_criterios_van_al_final_igual() -> None:
    """Regla previa, intacta: tener fotos no asciende a una que no cumple."""
    props = [
        _prop('no-cumple-con-fotos', 99, 10, matches=False),
        _prop('cumple-sin-fotos', 30, 0),
    ]

    assert _orden(props) == ['cumple-sin-fotos', 'no-cumple-con-fotos']


def test_sin_score_no_explota() -> None:
    """Una búsqueda sin `query_raw` no pasa por `rank_properties`, así que
    ninguna propiedad tiene `match_score`. Ahí el único criterio es la foto."""
    props = [_prop('sin-fotos', None, 0), _prop('con-fotos', None, 4)]

    assert _orden(props) == ['con-fotos', 'sin-fotos']


def test_una_lista_vacia_no_explota() -> None:
    props: list[dict[str, Any]] = []
    _photo_aware_sort(props)
    assert props == []


def test_una_propiedad_sin_la_clave_imagenes_cuenta_como_sin_fotos() -> None:
    """Filas viejas, o rescatadas por `scraping_job_id`, pueden no traerla."""
    props = [{'id': 'vieja', 'match_score': 90}, _prop('con-fotos', 90, 2)]

    assert _orden(props) == ['con-fotos', 'vieja']
