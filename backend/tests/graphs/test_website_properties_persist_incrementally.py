"""Lo extraído se guarda a medida que sale, no al final.

`extract_website_properties_llm` juntaba las propiedades de las ~1500 páginas
en memoria y `save_website_properties` — el nodo SIGUIENTE — las escribía todas
juntas. Con 260 inmobiliarias esa fase son minutos largos, y hasta que termina
no hay una sola fila en la base: cortar ahí (el botón de detener, un deploy, un
timeout, un crash) tiraba el trabajo entero de la fase.

El mismo loop ya escribía el ledger de tokens página por página, y su comentario
dice exactamente por qué:

    "Writing inside the loop also means a crash mid-run keeps the rows for the
     pages already paid for."

O sea que el razonamiento ya estaba hecho — para el GASTO. Las propiedades, que
son lo que el usuario espera, seguían saliendo recién al final. Esto le aplica
el mismo criterio.

`_upsert_properties` escribe insert-ignore contra `properties_dedup_idx`, así
que `save_website_properties` puede seguir guardando la lista completa al
cerrar: lo ya escrito se descarta solo. Ese nodo queda como red de seguridad,
no como el único momento en que se persiste.
"""
from typing import Any

import pytest

from app.graphs.extraction import nodes
from tests.conftest import listing_text
from app.graphs.extraction.nodes import extract_website_properties_llm
from app.models.property import NormalizedProperty, ScrapingFilters

_PAGES = [
    {'url': f'https://inmo{i}.com.ar/propiedades', 'text': listing_text()}
    for i in range(4)
]


def _prop(idx: int) -> NormalizedProperty:
    return NormalizedProperty(
        titulo=f'Depto {idx}',
        direccion=f'Calle {idx} 100, La Plata',
        precio=100000.0 + idx,
        tipo_operacion='venta',
        url_origen=f'https://inmo{idx}.com.ar/ficha/{idx}',
        fuente='googlemaps',
    )


@pytest.fixture()
def wiring(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Registra CADA escritura con el momento en que ocurrió, que es lo único
    que separa "guardar incremental" de "guardar todo junto al final"."""
    seen: dict[str, list[Any]] = {'upserts': [], 'links': [], 'extracted': []}

    async def _fake_extract(page: dict[str, str], sb: Any, job_id: Any) -> list[NormalizedProperty]:
        idx = int(page['url'].split('inmo')[1].split('.')[0])
        seen['extracted'].append(idx)
        return [_prop(idx)]

    async def _fake_upsert(sb: Any, props: list[NormalizedProperty], job_id: Any) -> None:
        seen['upserts'].append([p.titulo for p in props])

    async def _fake_link(
        sb: Any, props: list[NormalizedProperty], job_id: Any, matched: Any = None,
    ) -> None:
        seen['links'].append([p.titulo for p in props])

    async def _noop_dispatch(name: str, data: dict[str, Any], config: Any = None) -> None:
        return None

    monkeypatch.setattr(nodes, '_extract_page_properties', _fake_extract)
    monkeypatch.setattr(nodes, '_upsert_properties', _fake_upsert)
    monkeypatch.setattr(nodes, '_link_job_properties', _fake_link)
    monkeypatch.setattr(nodes, 'adispatch_custom_event', _noop_dispatch)
    return seen


def _state() -> dict[str, Any]:
    return {
        'website_pages': list(_PAGES),
        'job_id': 'job-1',
        'filters': ScrapingFilters(zona='La Plata'),
    }


_CONFIG: Any = {'configurable': {'supabase': object()}}


async def test_cada_pagina_se_guarda_apenas_se_extrae(
    wiring: dict[str, list[Any]],
) -> None:
    """Una escritura por página, no una sola al final. Es la diferencia entre
    conservar 40 minutos de trabajo y perderlos."""
    await extract_website_properties_llm(_state(), _CONFIG)

    assert len(wiring['upserts']) == len(_PAGES)
    assert sorted(t for tanda in wiring['upserts'] for t in tanda) == [
        'Depto 0', 'Depto 1', 'Depto 2', 'Depto 3',
    ]


async def test_cada_tanda_tambien_se_linkea_al_job(
    wiring: dict[str, list[Any]],
) -> None:
    """`GET /{job_id}/properties` lee primero la tabla de links. Sin linkear
    incremental, una búsqueda detenida dependería del rescate por
    `scraping_job_id` — que existe, pero deja las propiedades sin el veredicto
    de criterios y las muestra todas como coincidentes."""
    await extract_website_properties_llm(_state(), _CONFIG)

    assert len(wiring['links']) == len(_PAGES)


async def test_el_nodo_sigue_devolviendo_todo_lo_extraido(
    wiring: dict[str, list[Any]],
) -> None:
    """Guardar en el camino no cambia lo que el nodo devuelve: el fan-in y el
    `property_batch` de `save_website_properties` siguen viendo la lista
    completa."""
    out = await extract_website_properties_llm(_state(), _CONFIG)

    assert sorted(p.titulo for p in out['website_properties']) == [
        'Depto 0', 'Depto 1', 'Depto 2', 'Depto 3',
    ]


async def test_una_pagina_sin_propiedades_no_escribe(
    wiring: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Escribir una tanda vacía es un round-trip a Postgres por cada página que
    no tenía nada, y con 1500 páginas eso se nota."""
    async def _nada(page: dict[str, str], sb: Any, job_id: Any) -> list[NormalizedProperty]:
        return []

    monkeypatch.setattr(nodes, '_extract_page_properties', _nada)

    await extract_website_properties_llm(_state(), _CONFIG)

    assert wiring['upserts'] == []
    assert wiring['links'] == []


async def test_si_falla_el_guardado_de_una_pagina_la_extraccion_sigue(
    wiring: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El guardado incremental es una mejora de resiliencia; convertirlo en un
    punto nuevo de falla total sería peor que no tenerlo. Una página que no se
    puede persistir no puede llevarse puesta a las otras 1499 — la lista que el
    nodo devuelve la rescata igual en `save_website_properties`."""
    async def _boom(sb: Any, props: list[NormalizedProperty], job_id: Any) -> None:
        if props and props[0].titulo == 'Depto 2':
            raise RuntimeError('conexión caída')
        wiring['upserts'].append([p.titulo for p in props])

    monkeypatch.setattr(nodes, '_upsert_properties', _boom)

    out = await extract_website_properties_llm(_state(), _CONFIG)

    assert len(out['website_properties']) == len(_PAGES)
    assert sorted(t for tanda in wiring['upserts'] for t in tanda) == [
        'Depto 0', 'Depto 1', 'Depto 3',
    ]
