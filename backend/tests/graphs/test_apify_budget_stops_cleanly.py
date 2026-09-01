"""Agotar el presupuesto de Apify frena la búsqueda; NO la rompe.

`route_after_review` abre un `Send` de Instagram por cada inmobiliaria con
handle. Cuando el tope de gasto corta, corta para TODAS a la vez: si cada rama
tratara el corte como el error que ya sabe tratar, una zona con 50
inmobiliarias escupiría 50 burbujas rojas idénticas y 50 entradas en `errors` —
una búsqueda que funcionó y devolvió resultados se leería como una que falló 50
veces.

Por eso `ApifyBudgetExceeded` es una excepción propia y se atrapa ANTES del
`except Exception` genérico de cada nodo:

  - la rama devuelve vacío y el fan-in se queda con lo que ya juntó
  - no entra en `errors` (eso marca la búsqueda como degradada, y no lo está:
    el tope es una decisión nuestra, no una falla)
  - el aviso al usuario sale UNA vez por job, no una por rama
"""
from __future__ import annotations

from typing import Any

import pytest

from app.graphs.extraction import nodes
from app.graphs.extraction.nodes import discover_agencies, run_instagram_scraper
from app.services.apify import ApifyBudgetExceeded

_CONFIG: Any = {'configurable': {'supabase': None}}


@pytest.fixture(autouse=True)
def _clean_registry():
    nodes._budget_notified.clear()
    yield
    nodes._budget_notified.clear()


def _capture_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    async def _dispatch(name: str, data: dict[str, Any], config: Any = None) -> None:
        seen.append({'name': name, **data})

    monkeypatch.setattr(nodes, 'adispatch_custom_event', _dispatch)
    return seen


class _BrokeService:
    """Todo run pedido a este servicio cae por tope agotado."""

    async def scrape_instagram_profile(self, handle: str, on_progress: Any) -> list[Any]:
        raise ApifyBudgetExceeded('gasto 1.05 USD supera el tope de 1.0 USD')

    async def scrape_agencies(self, zona: str, on_progress: Any) -> list[Any]:
        raise ApifyBudgetExceeded('gasto 1.05 USD supera el tope de 1.0 USD')


@pytest.fixture()
def broke(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nodes, 'get_apify_service', lambda: _BrokeService())


async def test_la_rama_de_instagram_devuelve_vacio_sin_marcar_error(
    broke: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _capture_events(monkeypatch)

    out = await run_instagram_scraper(
        {'handle': 'inmo_ar', 'nombre': 'Inmo', 'job_id': 'job-1'}, _CONFIG,
    )

    assert out['instagram_posts'] == []
    assert not out.get('errors'), 'el tope no es una falla de la búsqueda'


async def test_el_corte_no_emite_evento_de_error(
    broke: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _capture_events(monkeypatch)

    await run_instagram_scraper(
        {'handle': 'inmo_ar', 'nombre': 'Inmo', 'job_id': 'job-1'}, _CONFIG,
    )

    assert [e for e in seen if e['name'] == 'error'] == []


async def test_el_aviso_sale_una_sola_vez_por_job(
    broke: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lo que hace la diferencia entre un aviso y 50."""
    seen = _capture_events(monkeypatch)

    for i in range(5):
        await run_instagram_scraper(
            {'handle': f'inmo_{i}', 'nombre': f'Inmo {i}', 'job_id': 'job-1'}, _CONFIG,
        )

    avisos = [e for e in seen if e.get('source') == 'apify_budget']
    assert len(avisos) == 1
    assert avisos[0]['name'] == 'progress'


async def test_el_aviso_dice_los_numeros(
    broke: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"Se agotó el presupuesto" sin cifras no le dice al usuario si subir el
    tope o achicar la zona — es el mismo mensaje que ya viaja en la excepción."""
    seen = _capture_events(monkeypatch)

    await run_instagram_scraper(
        {'handle': 'inmo_ar', 'nombre': 'Inmo', 'job_id': 'job-1'}, _CONFIG,
    )

    aviso = next(e for e in seen if e.get('source') == 'apify_budget')
    assert '1.05' in aviso['message']
    assert '1.0' in aviso['message']


async def test_dos_jobs_distintos_avisan_cada_uno(
    broke: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El registro es por job: silenciar el aviso del segundo job por culpa del
    primero sería el mismo bug al revés."""
    seen = _capture_events(monkeypatch)

    await run_instagram_scraper({'handle': 'a', 'nombre': 'A', 'job_id': 'job-1'}, _CONFIG)
    await run_instagram_scraper({'handle': 'b', 'nombre': 'B', 'job_id': 'job-2'}, _CONFIG)

    assert len([e for e in seen if e.get('source') == 'apify_budget']) == 2


async def test_descubrir_inmobiliarias_tambien_corta_limpio(
    broke: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`discover_agencies` corre antes que Instagram, así que casi nunca es la
    que se queda sin presupuesto — pero cuando lo hace (una búsqueda reanudada
    contra un ledger ya cargado) tiene que cortar igual de limpio."""
    from app.models.property import ScrapingFilters
    seen = _capture_events(monkeypatch)

    out = await discover_agencies(
        {'filters': ScrapingFilters(zona='La Plata'), 'job_id': 'job-1'}, _CONFIG,
    )

    assert out['agencies'] == []
    assert not out.get('errors')
    assert [e for e in seen if e['name'] == 'error'] == []
    assert len([e for e in seen if e.get('source') == 'apify_budget']) == 1


async def test_un_error_de_verdad_sigue_siendo_un_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El camino de error que ya existía no se toca: un actor que explota por
    cualquier otro motivo tiene que seguir avisando en rojo."""
    class _Boom:
        async def scrape_instagram_profile(self, handle: str, on_progress: Any) -> list[Any]:
            raise RuntimeError('Apify run run-9 ended with status FAILED')

    monkeypatch.setattr(nodes, 'get_apify_service', lambda: _Boom())
    seen = _capture_events(monkeypatch)

    out = await run_instagram_scraper(
        {'handle': 'inmo_ar', 'nombre': 'Inmo', 'job_id': 'job-1'}, _CONFIG,
    )

    assert out['errors']
    assert [e for e in seen if e['name'] == 'error']
