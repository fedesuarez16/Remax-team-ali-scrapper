"""Una propiedad de otra zona no coincide con lo que el usuario pidió.

`_matches_filters` decidía qué entra en `matched` mirando precio y ambientes.
La zona no la miraba NUNCA. Como el track de inmobiliarias tampoco filtra por
zona al scrapear — a diferencia de los portales, que tienen `_item_matches_zona`
en el origen — una propiedad de Mar del Plata llegaba marcada como COINCIDENTE
en una búsqueda del casco de La Plata, y salía arriba de todo mezclada con las
buenas.

Ese era el "me devuelve de cualquier zona": no es que aparecieran al final, es
que estaban marcadas como que cumplían.

Lo que NO cambia: la regla de "un dato ausente nunca excluye". Una propiedad
sin dirección legible sigue contando como coincidente, igual que una sin precio
— no hay evidencia de que NO sea de la zona, y esta función decide qué se
muestra primero, no qué se tira. Las que no coinciden siguen guardadas y
visibles en el desplegable.
"""
import pytest

from app.graphs.extraction.nodes import _matches_filters
from app.models.property import NormalizedProperty, ScrapingFilters


def _prop(direccion: str, precio: float | None = 90000.0) -> NormalizedProperty:
    return NormalizedProperty(
        titulo='Depto', direccion=direccion, precio=precio,
        tipo_operacion='venta', fuente='googlemaps',
        url_origen='https://inmo.com.ar/f/1',
    )


_CASCO = ScrapingFilters(
    zona='La Plata', zona_pedida='La Plata',
    precio_min=75000, precio_max=110000,
)


# ── La zona, que antes no se miraba ───────────────────────────────────────────

def test_una_propiedad_de_la_zona_coincide() -> None:
    assert _matches_filters(_prop('Calle 50 456, La Plata'), _CASCO)


def test_una_propiedad_de_otra_zona_no_coincide() -> None:
    """El caso que llenaba la lista."""
    assert not _matches_filters(_prop('Av. Colón 1200, Mar del Plata'), _CASCO)


def test_otra_zona_no_se_salva_por_tener_buen_precio() -> None:
    """Antes sí: precio en rango era todo lo que se pedía."""
    assert not _matches_filters(_prop('Palermo, CABA', precio=95000.0), _CASCO)


def test_los_acentos_y_mayusculas_no_deciden() -> None:
    f = ScrapingFilters(zona='Gonnet', zona_pedida='Gonnet')
    assert _matches_filters(_prop('Belgrano 123, GONNET'), f)


def test_se_acepta_cualquiera_de_las_zonas_pedidas() -> None:
    """Una búsqueda multi-zona coincide con cualquiera de ellas."""
    f = ScrapingFilters(zonas=['City Bell', 'Gonnet'])

    assert _matches_filters(_prop('Cantilo 1234, City Bell'), f)
    assert _matches_filters(_prop('Belgrano 123, Gonnet'), f)
    assert not _matches_filters(_prop('Av. Colón 1200, Mar del Plata'), f)


def test_las_localidades_tambien_cuentan() -> None:
    """El camino de polígono llena `localidades` en vez de `zona`."""
    f = ScrapingFilters(localidades=['Berisso'])

    assert _matches_filters(_prop('Montevideo 820, Berisso'), f)


# ── Lo que NO cambia ──────────────────────────────────────────────────────────

def test_sin_direccion_sigue_coincidiendo() -> None:
    """"Un dato ausente nunca excluye" — la regla que ya gobernaba precio y
    ambientes. Sin dirección no hay evidencia de que NO sea de la zona, y esta
    función decide el orden, no qué se descarta."""
    assert _matches_filters(_prop(''), _CASCO)


def test_sin_zona_pedida_no_se_filtra_por_zona() -> None:
    f = ScrapingFilters(precio_min=75000, precio_max=110000)

    assert _matches_filters(_prop('Av. Colón 1200, Mar del Plata'), f)


def test_el_precio_sigue_filtrando() -> None:
    assert not _matches_filters(_prop('Calle 50 456, La Plata', precio=400000.0), _CASCO)


def test_sin_precio_sigue_coincidiendo() -> None:
    """"Consultar precio" es habitual y no puede costar la propiedad."""
    assert _matches_filters(_prop('Calle 50 456, La Plata', precio=None), _CASCO)


def test_sin_filtros_todo_coincide() -> None:
    assert _matches_filters(_prop('Donde sea'), None)


@pytest.mark.parametrize('direccion', [
    'Calle 50 456, La Plata, Buenos Aires',
    'la plata, calle 7 nº 1200',
])
def test_variantes_de_escritura_de_la_zona(direccion: str) -> None:
    assert _matches_filters(_prop(direccion), _CASCO)
