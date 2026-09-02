"""Colapsar la propiedad que varios publicadores dan por suya.

`nodes.deduplicate_properties` ya resolvía esto para los portales, con una
regla fina que conviene no perder: dos `url_origen` distintas dentro de UN
MISMO catálogo son dos propiedades distintas, porque ese catálogo ya
deduplicó lo suyo. Sólo una copia de OTRO catálogo es republicación.

Esa regla se rompía sola en el track de inmobiliarias por un detalle de
etiquetado: todas esas propiedades se guardan con `fuente='googlemaps'`, así
que para la regla los 552 sitios de una búsqueda eran UN catálogo y dos
inmobiliarias publicando la misma propiedad pasaban como dos.

Este módulo no cambia la regla — corrige de qué habla. El catálogo no es la
etiqueta `fuente`: es QUIÉN publicó. Un portal para las de portal, el dominio
del sitio para las de inmobiliaria.

Vive acá y no en `nodes.py` porque hace falta en los dos extremos — al guardar
y al servir los resultados — y tener dos implementaciones de esta regla es
garantizar que se separen.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from app.services.zona import address_fingerprint, normalize_address


def catalogo_de(fuente: str | None, url_origen: str | None) -> str:
    """Quién publicó esta ficha, a los efectos del dedup.

    Para una propiedad de inmobiliaria, el dominio del sitio: cada inmobiliaria
    es un catálogo propio que deduplicó sus fichas. Para un portal, la `fuente`
    de siempre. Sin URL no hay dominio que leer y se cae en la `fuente`.
    """
    fuente = (fuente or 'desconocida').strip().lower()
    if fuente not in ('googlemaps', 'manual', 'instagram'):
        return fuente
    host = urlparse(url_origen or '').netloc.lower()
    if host.startswith('www.'):
        host = host[4:]
    return host or fuente


def clave_de(row: Any) -> tuple[Any, ...] | None:
    """Qué hace que dos fichas sean la MISMA propiedad. `None` = indecidible.

    Misma composición que `nodes._dedup_key`: la dirección se reduce a un
    `calle número` canónico para que la misma propiedad publicada de tres
    formas colapse, y precio, moneda, operación y tipo quedan adentro porque
    son lo que distingue las varias unidades que legítimamente comparten una
    dirección.

    Sin dirección legible devuelve `None` y la fila NO se colapsa: una
    dirección vacía es el peor ancla posible — todas las propiedades sin
    dirección de un mismo precio se comerían entre sí.
    """
    direccion = _campo(row, 'direccion') or ''
    ancla = address_fingerprint(direccion) or normalize_address(direccion)
    if not ancla:
        return None
    return (
        ancla,
        _campo(row, 'precio'),
        _campo(row, 'moneda'),
        _campo(row, 'tipo_operacion'),
        _campo(row, 'tipo_propiedad'),
    )


def _campo(row: Any, nombre: str) -> Any:
    """Lee un campo de un dict (fila de la base) o de un modelo (nodo)."""
    return row.get(nombre) if isinstance(row, dict) else getattr(row, nombre, None)


def _calidad(row: Any) -> tuple[int, float]:
    """Cuál de dos copias merece sobrevivir.

    Primero fotos: la copia con imágenes es la que el operador puede mostrar, y
    quedarse con la pelada sería tirar el trabajo de haber conseguido la
    galería. Después coincidencia.
    """
    fotos = 1 if (_campo(row, 'imagenes') or []) else 0
    score = _campo(row, 'match_score')
    return (fotos, float(score) if isinstance(score, (int, float)) else -1.0)


def collapse_duplicates(rows: list[Any]) -> list[Any]:
    """Una fila por propiedad real, conservando el orden de entrada.

    El orden importa: el ranking corre después y espera la lista como venía.
    Cuando una copia posterior es mejor que la que ya estaba, se reemplaza EN
    SU LUGAR en vez de moverse al final.
    """
    salida: list[Any] = []
    posicion: dict[tuple[Any, ...], int] = {}
    catalogos: dict[tuple[Any, ...], set[str]] = {}
    fichas_vistas: set[tuple[str, str]] = set()

    for row in rows:
        catalogo = catalogo_de(_campo(row, 'fuente'), _campo(row, 'url_origen'))
        url = _campo(row, 'url_origen')
        # La MISMA ficha leída dos veces — la home de la inmobiliaria y su
        # página de listados publican el mismo link. No hace falta clave para
        # decidirlo: es literalmente la misma URL del mismo publicador.
        if url:
            if (catalogo, url) in fichas_vistas:
                continue
            fichas_vistas.add((catalogo, url))

        clave = clave_de(row)
        if clave is None:
            salida.append(row)
            continue
        vistos = catalogos.get(clave)

        # Mismo catálogo, otra ficha: él sabe que son dos unidades distintas.
        # Es la regla que salvó 33 de 54 listados de ZonaProp en una búsqueda
        # real, y sigue valiendo — ahora también entre fichas de una misma
        # inmobiliaria.
        if vistos is not None and catalogo in vistos:
            salida.append(row)
            continue

        if vistos is None:
            catalogos[clave] = {catalogo}
            posicion[clave] = len(salida)
            salida.append(row)
            continue

        vistos.add(catalogo)
        i = posicion[clave]
        if _calidad(row) > _calidad(salida[i]):
            salida[i] = row

    return salida
