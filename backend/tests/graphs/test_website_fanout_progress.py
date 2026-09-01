"""El fan-out de inmobiliarias tiene que reportar `132 de 260`, no 260 filas.

Con 260 inmobiliarias tildadas, `route_after_review` emite 260 `Send` de una.
Antes cada rama reportaba su propio `web:<dominio>` — 520 eventos y una lista
de 260 ítems en el cliente — y NADIE sabía cuántas faltaban, porque las ramas
de un fan-out no comparten estado hasta el fan-in.

Estos tests fijan el contrato del contador compartido:
  - `route_after_review` publica el total del fan-out
  - cada rama emite un `progress` agregado sobre `inmobiliarias` con done/total
  - un sitio que explota TAMBIÉN avanza la barra (si no, queda en 258/260)
  - con agregado activo no se emite un evento por sitio
  - el semáforo acota cuántos sitios corren a la vez
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.core.config import settings
from app.graphs.extraction import nodes
from app.graphs.extraction.nodes import route_after_review, run_website_scraper
from app.models.property import Agency


@pytest.fixture(autouse=True)
def _clean_registry():
    nodes._website_progress.clear()
    nodes._website_semaphore = None
    yield
    nodes._website_progress.clear()
    nodes._website_semaphore = None


def _manual(n: int) -> list[dict[str, str]]:
    return [{'nombre': f'Inmo {i}', 'url': f'https://inmo{i}.com.ar'} for i in range(n)]


def _capture_events(monkeypatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    async def _dispatch(name: str, data: dict[str, Any], config: Any = None) -> None:
        seen.append({'name': name, **data})

    monkeypatch.setattr(nodes, 'adispatch_custom_event', _dispatch)
    return seen


class _FakeService:
    def __init__(self, *, fail: bool = False, on_enter: Any = None) -> None:
        self.fail = fail
        self.on_enter = on_enter

    async def scrape_website(self, url: str, on_progress: Any) -> list[dict[str, str]]:
        await on_progress(f'web:{url}', 'running', 0)
        if self.on_enter is not None:
            await self.on_enter()
        if self.fail:
            raise RuntimeError('sitio caído')
        return [{'url': url, 'text': 'x' * 200}]


def test_route_publishes_the_fanout_total(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'MAX_WEBSITE_URLS', 0)
    state = {
        'job_id': 'job-260', 'agencies': [], 'selected_agency_ids': [],
        'manual_sources': _manual(260),
    }
    sends = route_after_review(state)

    assert len(sends) == 260
    assert nodes._website_progress_total('job-260') == 260


def test_route_without_websites_clears_the_counter(monkeypatch) -> None:
    """Una corrida previa no puede dejar un total viejo colgado: la barra
    arrancaría en `0 de 260` para una búsqueda que no scrapea ningún sitio."""
    monkeypatch.setattr(settings, 'MAX_WEBSITE_URLS', 0)
    nodes._website_progress['job-1'] = {'total': 260, 'done': 7, 'announced': 1}

    assert route_after_review(
        {'job_id': 'job-1', 'agencies': [], 'selected_agency_ids': [], 'manual_sources': []}
    ) == 'no_websites'
    assert nodes._website_progress_total('job-1') == 0


@pytest.mark.asyncio
async def test_first_branch_announces_zero_of_total(monkeypatch) -> None:
    seen = _capture_events(monkeypatch)
    monkeypatch.setattr(nodes, 'get_apify_service', lambda: _FakeService())
    nodes._reset_website_progress('job-1', 3)

    await run_website_scraper({'url': 'https://a.com', 'nombre': 'A', 'job_id': 'job-1'}, {})

    agg = [e for e in seen if e.get('source') == 'inmobiliarias']
    assert (agg[0]['done'], agg[0]['total']) == (0, 3)
    assert (agg[1]['done'], agg[1]['total']) == (1, 3)
    # Sólo la primera rama anuncia el arranque.
    await run_website_scraper({'url': 'https://b.com', 'nombre': 'B', 'job_id': 'job-1'}, {})
    zeros = [e for e in seen if e.get('source') == 'inmobiliarias' and e['done'] == 0]
    assert len(zeros) == 1


@pytest.mark.asyncio
async def test_aggregate_replaces_per_site_events(monkeypatch) -> None:
    seen = _capture_events(monkeypatch)
    monkeypatch.setattr(nodes, 'get_apify_service', lambda: _FakeService())
    nodes._reset_website_progress('job-1', 2)

    await run_website_scraper({'url': 'https://a.com', 'nombre': 'A', 'job_id': 'job-1'}, {})

    assert not [e for e in seen if str(e.get('source', '')).startswith('web:')]


@pytest.mark.asyncio
async def test_per_site_events_survive_without_an_aggregate(monkeypatch) -> None:
    """Sin contador (job sin id) los eventos por sitio son el único feedback."""
    seen = _capture_events(monkeypatch)
    monkeypatch.setattr(nodes, 'get_apify_service', lambda: _FakeService())

    await run_website_scraper({'url': 'https://a.com', 'nombre': 'A'}, {})

    assert [e for e in seen if str(e.get('source', '')).startswith('web:')]


@pytest.mark.asyncio
async def test_a_failing_site_still_advances_the_bar(monkeypatch) -> None:
    seen = _capture_events(monkeypatch)
    monkeypatch.setattr(nodes, 'get_apify_service', lambda: _FakeService(fail=True))
    nodes._reset_website_progress('job-1', 2)

    out = await run_website_scraper({'url': 'https://a.com', 'nombre': 'A', 'job_id': 'job-1'}, {})

    assert out['website_pages'] == []
    assert [e for e in seen if e.get('source') == 'inmobiliarias' and e['done'] == 1]


@pytest.mark.asyncio
async def test_last_branch_reports_done_and_frees_the_registry(monkeypatch) -> None:
    seen = _capture_events(monkeypatch)
    monkeypatch.setattr(nodes, 'get_apify_service', lambda: _FakeService())
    nodes._reset_website_progress('job-1', 2)

    await run_website_scraper({'url': 'https://a.com', 'nombre': 'A', 'job_id': 'job-1'}, {})
    await run_website_scraper({'url': 'https://b.com', 'nombre': 'B', 'job_id': 'job-1'}, {})

    last = [e for e in seen if e.get('source') == 'inmobiliarias'][-1]
    assert (last['done'], last['total'], last['status']) == (2, 2, 'done')
    # Sin limpieza, un proceso largo acumula una entrada por búsqueda.
    assert 'job-1' not in nodes._website_progress


@pytest.mark.asyncio
async def test_concurrency_is_capped(monkeypatch) -> None:
    """Sin tope, 260 sitios abren ~1500 conexiones a la vez y el proceso muere."""
    _capture_events(monkeypatch)
    monkeypatch.setattr(settings, 'WEBSITE_SCRAPE_CONCURRENCY', 3)

    live = 0
    peak = 0

    async def _enter() -> None:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0)
        live -= 1

    monkeypatch.setattr(nodes, 'get_apify_service', lambda: _FakeService(on_enter=_enter))
    nodes._reset_website_progress('job-1', 20)

    await asyncio.gather(*(
        run_website_scraper({'url': f'https://s{i}.com', 'nombre': f'S{i}', 'job_id': 'job-1'}, {})
        for i in range(20)
    ))

    assert peak <= 3


def test_agency_websites_count_toward_the_total(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'MAX_WEBSITE_URLS', 0)
    monkeypatch.setattr(settings, 'SCRAPE_GOOGLEMAPS_ONLY', True)
    agencies = [Agency(id=f'a{i}', nombre=f'A{i}', sitio_web=f'https://a{i}.com') for i in range(5)]
    state = {
        'job_id': 'job-1', 'agencies': agencies,
        'selected_agency_ids': [a.id for a in agencies],
        'manual_sources': _manual(2),
    }

    sends = route_after_review(state)

    assert len(sends) == 7
    assert nodes._website_progress_total('job-1') == 7
