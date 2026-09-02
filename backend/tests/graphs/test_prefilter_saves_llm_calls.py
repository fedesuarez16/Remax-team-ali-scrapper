"""El filtro tiene que ahorrar llamadas de verdad, no sólo existir.

Un predicado correcto que nadie consulta antes de pagar no ahorra nada. Estos
tests fijan que el loop lo use ANTES de la llamada, y que descartar una página
no rompa lo que el usuario ve: la barra de progreso tiene que seguir llegando
al total, o una búsqueda que descartó 500 páginas quedaría clavada en 1000/1500
para siempre.
"""
from typing import Any

import pytest

from app.graphs.extraction import nodes
from app.graphs.extraction.nodes import extract_website_properties_llm
from app.models.property import NormalizedProperty, ScrapingFilters
from app.services.llm_costs import use_llm_ledger

_CONFIG: Any = {'configurable': {'supabase': None}}

_FICHA = (
    'Departamento de 3 ambientes con cochera y balcón al frente, en el casco '
    'urbano de La Plata. USD 120.000. Consultá disponibilidad con un asesor.'
)
_INSTITUCIONAL = (
    'Quiénes somos. Somos una inmobiliaria con más de 30 años de trayectoria '
    'en la ciudad. Nuestro equipo de profesionales matriculados te acompaña '
    'en cada paso del camino, con la seriedad que nos caracteriza.'
)


@pytest.fixture(autouse=True)
def _clean():
    nodes._budget_notified.clear()
    with use_llm_ledger({}):
        yield
    nodes._budget_notified.clear()


@pytest.fixture()
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    async def _fake_extract(page: dict[str, str], sb: Any, job_id: Any) -> list[NormalizedProperty]:
        seen.append(page['url'])
        return [NormalizedProperty(
            titulo='Depto', direccion='Calle 50 100, La Plata', precio=120000.0,
            tipo_operacion='venta', url_origen=page['url'], fuente='googlemaps',
        )]

    async def _noop(*_a: Any, **_kw: Any) -> None:
        return None

    monkeypatch.setattr(nodes, '_extract_page_properties', _fake_extract)
    monkeypatch.setattr(nodes, '_upsert_properties', _noop)
    monkeypatch.setattr(nodes, '_link_job_properties', _noop)
    return seen


def _events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    async def _dispatch(name: str, data: dict[str, Any], config: Any = None) -> None:
        seen.append({'name': name, **data})

    monkeypatch.setattr(nodes, 'adispatch_custom_event', _dispatch)
    return seen


def _state(pages: list[dict[str, str]]) -> dict[str, Any]:
    return {'website_pages': pages, 'job_id': 'job-1', 'filters': ScrapingFilters(zona='La Plata')}


_MIXTO = [
    {'url': 'https://a.com/props', 'text': _FICHA},
    {'url': 'https://a.com/nosotros', 'text': _INSTITUCIONAL},
    {'url': 'https://b.com/venta', 'text': _FICHA},
    {'url': 'https://b.com/contacto', 'text': _INSTITUCIONAL},
]


async def test_las_paginas_sin_propiedades_no_llegan_al_llm(
    calls: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _events(monkeypatch)

    await extract_website_properties_llm(_state(_MIXTO), _CONFIG)

    assert calls == ['https://a.com/props', 'https://b.com/venta']


async def test_las_fichas_se_extraen_igual(
    calls: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _events(monkeypatch)

    out = await extract_website_properties_llm(_state(_MIXTO), _CONFIG)

    assert len(out['website_properties']) == 2


async def test_la_barra_llega_al_total_igual(
    calls: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si las descartadas no avanzaran el contador, una búsqueda que filtró 500
    de 1500 quedaría clavada en 1000/1500 sin terminar nunca."""
    seen = _events(monkeypatch)

    await extract_website_properties_llm(_state(_MIXTO), _CONFIG)

    final = [e for e in seen if e.get('source') == 'extraccion' and e.get('status') == 'done']
    assert final, 'la extracción nunca reportó su cierre'
    assert final[-1]['done'] == len(_MIXTO)


async def test_el_cierre_dice_cuantas_se_saltearon(
    calls: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El ahorro tiene que ser visible. Un filtro que trabaja en silencio es un
    filtro que nadie va a poder ajustar cuando descarte de más."""
    seen = _events(monkeypatch)

    await extract_website_properties_llm(_state(_MIXTO), _CONFIG)

    final = [e for e in seen if e.get('source') == 'extraccion' and e.get('status') == 'done'][-1]
    assert '2 páginas sin propiedades' in final['message']


async def test_sin_paginas_utiles_no_se_llama_al_llm_ni_una_vez(
    calls: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _events(monkeypatch)
    solo_basura = [{'url': f'https://x{i}.com/nosotros', 'text': _INSTITUCIONAL} for i in range(10)]

    out = await extract_website_properties_llm(_state(solo_basura), _CONFIG)

    assert calls == []
    assert out['website_properties'] == []
