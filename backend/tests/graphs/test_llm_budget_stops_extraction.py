"""Agotado el presupuesto de tokens, la extracción para y entrega lo que tiene.

`extract_website_properties_llm` dispara una llamada a Haiku por página con un
`asyncio.gather` sobre las ~1500 que trae el fan-in. Ese loop es el que hacía
que una búsqueda costara ~USD 4: nadie contaba mientras corría.

El corte va ANTES de la llamada, adentro de `extract()`. Ahí, y no en el
`gather`, porque cancelar el gather tiraría también las páginas que ya estaban
en vuelo y pagadas. Una página que llega con el presupuesto agotado devuelve
vacío y sale — las demás drenan igual de rápido, sin gastar.

Y lo extraído hasta el corte NO se pierde: cada página se persiste apenas sale
(ver test_website_properties_persist_incrementally), así que el tope de tokens
y el botón de detener se apoyan en la misma garantía.
"""
from typing import Any

import pytest

from app.graphs.extraction import nodes
from tests.conftest import listing_text
from app.graphs.extraction.nodes import extract_website_properties_llm
from app.models.property import NormalizedProperty, ScrapingFilters
from app.services.llm_costs import use_llm_ledger

_CONFIG: Any = {'configurable': {'supabase': None}}


@pytest.fixture(autouse=True)
def _clean_registry():
    nodes._budget_notified.clear()
    yield
    nodes._budget_notified.clear()


@pytest.fixture(autouse=True)
def _ledger():
    """En producción lo monta `_run_graph_into_queue` alrededor de TODA la
    búsqueda; acá el nodo se llama suelto, así que hay que montarlo a mano."""
    with use_llm_ledger({}):
        yield


@pytest.fixture()
def cap_one_usd(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, 'LLM_MAX_USD_PER_SEARCH', 1.0)
    # Secuencial: con concurrencia, varias páginas pasan el guard antes de que
    # la primera anote su gasto, y el test mediría la carrera en vez del tope.
    monkeypatch.setattr(settings, 'WEBSITE_EXTRACT_CONCURRENCY', 1)


def _prop(idx: int) -> NormalizedProperty:
    return NormalizedProperty(
        titulo=f'Depto {idx}', direccion=f'Calle {idx} 100, La Plata',
        precio=100000.0 + idx, tipo_operacion='venta',
        url_origen=f'https://inmo{idx}.com.ar/ficha/{idx}', fuente='googlemaps',
    )


def _state(n: int) -> dict[str, Any]:
    return {
        'website_pages': [{'url': f'https://inmo{i}.com.ar/p', 'text': listing_text()} for i in range(n)],
        'job_id': 'job-1',
        'filters': ScrapingFilters(zona='La Plata'),
    }


@pytest.fixture()
def spending(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Cada 'llamada al LLM' cuesta USD 0.30 y devuelve una propiedad."""
    seen: dict[str, list[Any]] = {'calls': []}

    async def _fake_extract(page: dict[str, str], sb: Any, job_id: Any) -> list[NormalizedProperty]:
        from app.services.llm_costs import SCOPE_EXTRACT_WEBSITE, book_llm_cost
        idx = int(page['url'].split('inmo')[1].split('.')[0])
        seen['calls'].append(idx)
        book_llm_cost(SCOPE_EXTRACT_WEBSITE, 0.30)
        return [_prop(idx)]

    async def _noop(*_a: Any, **_kw: Any) -> None:
        return None

    monkeypatch.setattr(nodes, '_extract_page_properties', _fake_extract)
    monkeypatch.setattr(nodes, '_upsert_properties', _noop)
    monkeypatch.setattr(nodes, '_link_job_properties', _noop)
    return seen


def _capture_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    async def _dispatch(name: str, data: dict[str, Any], config: Any = None) -> None:
        seen.append({'name': name, **data})

    monkeypatch.setattr(nodes, 'adispatch_custom_event', _dispatch)
    return seen


async def test_deja_de_llamar_al_llm_al_agotar_el_presupuesto(
    cap_one_usd: None, spending: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A USD 0.30 por página, el dólar se agota en la cuarta: 0.30, 0.60, 0.90,
    1.20 — y de ahí en adelante no se llama más."""
    _capture_events(monkeypatch)

    await extract_website_properties_llm(_state(20), _CONFIG)

    assert len(spending['calls']) == 4


async def test_devuelve_lo_extraido_hasta_el_corte(
    cap_one_usd: None, spending: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lo que se pagó se entrega. Cortar no puede significar tirar el trabajo
    ya facturado."""
    _capture_events(monkeypatch)

    out = await extract_website_properties_llm(_state(20), _CONFIG)

    assert len(out['website_properties']) == 4


async def test_avisa_una_sola_vez(
    cap_one_usd: None, spending: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """16 páginas encuentran el presupuesto agotado. 16 avisos serían ruido."""
    seen = _capture_events(monkeypatch)

    await extract_website_properties_llm(_state(20), _CONFIG)

    avisos = [e for e in seen if e.get('source') == 'llm_budget']
    assert len(avisos) == 1
    assert avisos[0]['name'] == 'progress'


async def test_el_aviso_de_tokens_no_pisa_al_de_apify(
    cap_one_usd: None, spending: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Son dos hechos distintos y el registro de avisos es compartido: si la
    llave fuera sólo el job, quedarse sin créditos de Apify silenciaría el
    aviso de haberse quedado sin tokens."""
    seen = _capture_events(monkeypatch)
    nodes._claim_budget_notice('job-1', 'apify')

    await extract_website_properties_llm(_state(20), _CONFIG)

    assert len([e for e in seen if e.get('source') == 'llm_budget']) == 1


async def test_sin_tope_se_extraen_todas(
    spending: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, 'LLM_MAX_USD_PER_SEARCH', 0.0)
    _capture_events(monkeypatch)

    out = await extract_website_properties_llm(_state(20), _CONFIG)

    assert len(spending['calls']) == 20
    assert len(out['website_properties']) == 20
