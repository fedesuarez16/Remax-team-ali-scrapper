"""Dos inmobiliarias que publican la misma propiedad son UNA propiedad.

`deduplicate_properties` ya sabía colapsar la propiedad que varios portales
republican, y lo hacía con una regla fina: dos `url_origen` distintas dentro de
UN MISMO `fuente` son dos propiedades distintas, porque un portal ya
deduplicó su propio catálogo. Sólo una copia de OTRO `fuente` es republicación.

Esa regla se rompe sola en el track de inmobiliarias, y por un detalle de
etiquetado: TODAS las propiedades de sitios de inmobiliarias se guardan con
`fuente='googlemaps'`. Para la regla, los 552 sitios de una búsqueda son UN
catálogo — así que dos inmobiliarias publicando la misma propiedad pasan como
dos propiedades distintas y el operador ve la lista llena de repetidas.

El arreglo no toca la regla: corrige de qué habla. El "catálogo" que
deduplicó sus propias fichas no es la etiqueta `fuente`, es **quién publicó**:
un portal para las propiedades de portal, y el DOMINIO del sitio para las de
inmobiliaria. Dos dominios distintos con la misma clave es exactamente la
republicación que el dedup existe para colapsar.

Y cuando colapsa, no se queda con cualquiera: se queda con la mejor ficha. Una
copia con fotos le gana a una sin fotos, porque es la que el operador puede
mostrar.
"""
from typing import Any

import pytest

from app.services.dedup import catalogo_de, collapse_duplicates


def _row(
    nombre: str, direccion: str, precio: float, url: str,
    fuente: str = 'googlemaps', fotos: int = 0, score: float | None = None,
) -> dict[str, Any]:
    return {
        'id': nombre, 'direccion': direccion, 'precio': precio, 'moneda': 'USD',
        'tipo_operacion': 'venta', 'tipo_propiedad': 'departamento',
        'fuente': fuente, 'url_origen': url,
        'imagenes': [f'img{i}' for i in range(fotos)],
        'match_score': score,
    }


def _ids(rows: list[dict[str, Any]]) -> list[str]:
    return [r['id'] for r in collapse_duplicates(rows)]


# ── Quién es el catálogo ──────────────────────────────────────────────────────

def test_para_una_inmobiliaria_el_catalogo_es_su_dominio() -> None:
    assert catalogo_de('googlemaps', 'https://delbosque.com.ar/ficha/12') == 'delbosque.com.ar'


def test_el_dominio_ignora_www_y_mayusculas() -> None:
    """Si no, el mismo sitio contaría como dos catálogos y no colapsaría nada."""
    a = catalogo_de('googlemaps', 'https://WWW.DelBosque.com.ar/ficha/12')
    b = catalogo_de('googlemaps', 'http://delbosque.com.ar/otra')
    assert a == b


def test_para_un_portal_el_catalogo_sigue_siendo_la_fuente() -> None:
    """La regla de portales queda intacta: ZonaProp ya deduplicó lo suyo."""
    assert catalogo_de('zonaprop', 'https://zonaprop.com.ar/x') == 'zonaprop'


def test_sin_url_el_catalogo_cae_en_la_fuente() -> None:
    assert catalogo_de('googlemaps', None) == 'googlemaps'


# ── El colapso ────────────────────────────────────────────────────────────────

def test_dos_inmobiliarias_con_la_misma_propiedad_colapsan() -> None:
    """El caso que llenaba la lista de repetidas."""
    rows = [
        _row('a', 'Calle 50 456, La Plata', 120000, 'https://inmoA.com.ar/f/1'),
        _row('b', 'Calle 50 456, La Plata', 120000, 'https://inmoB.com.ar/f/9'),
    ]

    assert len(_ids(rows)) == 1


def test_una_inmobiliaria_con_dos_fichas_parecidas_NO_colapsa() -> None:
    """La regla fina que ya existía, ahora aplicada al dominio: si el mismo
    sitio publica dos fichas distintas, él sabe que son dos unidades. En una
    grilla numerada como La Plata dos deptos del mismo edificio comparten
    dirección y precio redondo."""
    rows = [
        _row('a', 'Calle 50 456, La Plata', 120000, 'https://inmoA.com.ar/f/1'),
        _row('b', 'Calle 50 456, La Plata', 120000, 'https://inmoA.com.ar/f/2'),
    ]

    assert len(_ids(rows)) == 2


def test_precios_distintos_no_colapsan() -> None:
    """El precio está en la clave a propósito: distingue unidades que
    legítimamente comparten dirección."""
    rows = [
        _row('a', 'Calle 50 456, La Plata', 120000, 'https://inmoA.com.ar/f/1'),
        _row('b', 'Calle 50 456, La Plata', 185000, 'https://inmoB.com.ar/f/9'),
    ]

    assert len(_ids(rows)) == 2


def test_una_copia_de_portal_y_una_de_inmobiliaria_colapsan() -> None:
    rows = [
        _row('portal', 'Calle 50 456, La Plata', 120000, 'https://zonaprop.com.ar/x', fuente='zonaprop'),
        _row('inmo', 'Calle 50 456, La Plata', 120000, 'https://inmoA.com.ar/f/1'),
    ]

    assert len(_ids(rows)) == 1


# ── Cuál sobrevive ────────────────────────────────────────────────────────────

def test_sobrevive_la_que_tiene_fotos() -> None:
    """Colapsar no es elegir al azar: la copia con fotos es la que el operador
    puede mostrar."""
    rows = [
        _row('sin-fotos', 'Calle 50 456, La Plata', 120000, 'https://inmoA.com.ar/f/1', fotos=0),
        _row('con-fotos', 'Calle 50 456, La Plata', 120000, 'https://inmoB.com.ar/f/9', fotos=5),
    ]

    assert _ids(rows) == ['con-fotos']


def test_a_igualdad_de_fotos_sobrevive_la_de_mejor_coincidencia() -> None:
    rows = [
        _row('peor', 'Calle 50 456, La Plata', 120000, 'https://inmoA.com.ar/f/1', fotos=3, score=60),
        _row('mejor', 'Calle 50 456, La Plata', 120000, 'https://inmoB.com.ar/f/9', fotos=3, score=95),
    ]

    assert _ids(rows) == ['mejor']


def test_el_orden_de_los_que_sobreviven_se_respeta() -> None:
    """Colapsar no puede reordenar: `_photo_aware_sort` corre después y espera
    la lista tal como venía."""
    rows = [
        _row('primera', 'Calle 7 100, La Plata', 90000, 'https://a.com/1'),
        _row('segunda', 'Calle 8 200, La Plata', 95000, 'https://b.com/1'),
        _row('tercera', 'Calle 9 300, La Plata', 99000, 'https://c.com/1'),
    ]

    assert _ids(rows) == ['primera', 'segunda', 'tercera']


def test_la_misma_ficha_leida_dos_veces_colapsa() -> None:
    """La home de la inmobiliaria y su página de listados publican el mismo
    link, y las dos se scrapean. No hace falta comparar direcciones para
    decidirlo: es la misma URL del mismo publicador."""
    rows = [
        _row('a', 'Calle 50 456, La Plata', 120000, 'https://inmoA.com.ar/f/1'),
        _row('b', 'Calle 50 456, La Plata', 120000, 'https://inmoA.com.ar/f/1'),
    ]

    assert _ids(rows) == ['a']


def test_la_misma_url_colapsa_aunque_no_haya_direccion() -> None:
    rows = [
        _row('a', '', 120000, 'https://inmoA.com.ar/f/1'),
        _row('b', '', 120000, 'https://inmoA.com.ar/f/1'),
    ]

    assert _ids(rows) == ['a']


def test_una_lista_vacia_no_explota() -> None:
    assert collapse_duplicates([]) == []


def test_sin_direccion_no_se_colapsa_todo_junto() -> None:
    """Una dirección vacía es el peor ancla posible: si colapsara por clave,
    todas las propiedades sin dirección de un mismo precio se comerían entre
    sí. Sin ancla no hay evidencia de que sean la misma."""
    rows = [
        _row('a', '', 120000, 'https://inmoA.com.ar/f/1'),
        _row('b', '', 120000, 'https://inmoB.com.ar/f/9'),
    ]

    assert len(_ids(rows)) == 2
