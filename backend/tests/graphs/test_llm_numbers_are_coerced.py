"""Un número basura del LLM no puede matar la búsqueda.

Caso real (job 342cc50e, 2026-09-01): un post de Instagram volvió con
`piso='<UNKNOWN>'`. Pydantic rechazó el int, la excepción subió por el nodo,
LangGraph la propagó y la corrida entera murió — 8 minutos y 552 sitios de
inmobiliarias tirados por UN post.

Son dos fallas encadenadas y las dos se arreglan acá:

1. Los valores del LLM entraban CRUDOS al modelo. El tool schema pide un
   entero, pero un modelo puede mandar '<UNKNOWN>', 'N/A', 'PB' o '' — y eso no
   es un caso raro, es el comportamiento normal de un LLM ante un dato que no
   encuentra. Un campo opcional que no se pudo leer vale None, no una
   excepción.

2. Una propiedad mala se llevaba puestas a todas las demás. `_extract_page_properties`
   ya prometía en su docstring que "nunca propaga: una página que falla vale []",
   pero el `try` sólo envolvía la llamada HTTP: armar los modelos quedaba
   afuera. La promesa estaba escrita y no implementada.
"""
from typing import Any

import pytest

from app.graphs.extraction.nodes import _llm_float, _llm_int


# ── Coerción ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('basura', ['<UNKNOWN>', 'N/A', 'null', 'None', '', '   ', '-', 'consultar'])
def test_lo_que_no_es_numero_vale_none(basura: str) -> None:
    """Lo que el LLM manda cuando no encontró el dato. Un opcional ausente es
    None; nada de esto justifica tirar la búsqueda."""
    assert _llm_int(basura) is None
    assert _llm_float(basura) is None


def test_none_sigue_siendo_none() -> None:
    assert _llm_int(None) is None
    assert _llm_float(None) is None


def test_un_entero_pasa_derecho() -> None:
    assert _llm_int(3) == 3
    assert _llm_int('3') == 3


def test_un_numero_con_unidad_se_lee() -> None:
    """'3°' es un piso y '2 ambientes' son 2. Tirarlos por el sufijo sería
    perder datos que están ahí."""
    assert _llm_int('3°') == 3
    assert _llm_int('2 ambientes') == 2


def test_un_float_con_formato_argentino_se_lee() -> None:
    """El LLM copia el precio como lo ve en la página."""
    assert _llm_float('120000') == 120000.0
    assert _llm_float('120.000') == 120000.0
    assert _llm_float('120000.50') == pytest.approx(120000.50)
    assert _llm_float(95000.0) == 95000.0


def test_un_float_no_se_trunca_a_entero() -> None:
    assert _llm_int('3.9') == 3
    assert _llm_float('3.9') == pytest.approx(3.9)


def test_un_negativo_no_pasa() -> None:
    """No existe el piso -2 en estos datos; un signo suelto es ruido de parseo."""
    assert _llm_int('-3') is None
    assert _llm_float('-1000') is None


# ── El caso que rompió producción ─────────────────────────────────────────────

async def test_un_post_con_piso_desconocido_no_mata_la_busqueda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.graphs.extraction import nodes

    async def _noop(*_a: Any, **_kw: Any) -> None:
        return None

    class _Block:
        type = 'tool_use'
        input = {
            'es_propiedad': True, 'descripcion': 'Depto 2 amb en La Plata',
            'direccion_zona': 'Calle 50 456, La Plata', 'precio': '120.000',
            'piso': '<UNKNOWN>', 'ambientes': 'N/A', 'm2': '',
        }

    class _Msg:
        content = [_Block()]
        usage = None

    class _Messages:
        async def create(self, **_kw: Any) -> _Msg:
            return _Msg()

    class _Client:
        messages = _Messages()

    monkeypatch.setattr(nodes, 'adispatch_custom_event', _noop)
    monkeypatch.setattr(nodes, '_client', _Client())
    monkeypatch.setattr(nodes, 'record_llm_usage', _noop)

    state = {
        'job_id': 'job-1',
        'instagram_posts': [{
            'titulo': 'Hermoso departamento de 2 ambientes en el casco urbano de La Plata',
            'url_origen': 'https://instagram.com/p/abc',
        }],
    }
    out = await nodes.extract_instagram_properties_llm(state, {'configurable': {'supabase': None}})

    props = out['instagram_properties']
    assert len(props) == 1, 'la propiedad se perdió en vez de salvarse'
    assert props[0].piso is None
    assert props[0].ambientes is None
    assert props[0].precio == 120000.0
