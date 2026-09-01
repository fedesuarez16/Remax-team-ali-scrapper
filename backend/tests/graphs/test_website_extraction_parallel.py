"""Test-first: la extracción por LLM no puede ser secuencial.

`extract_website_properties_llm` corre UNA vez, después del fan-in de las 260
ramas: recibe ~1500 páginas juntas. El bucle era secuencial — una llamada a
Claude por página, ~4 s cada una — así que la búsqueda pasaba más de 90 minutos
con el stream abierto y moría antes de terminar. Ese era el "tarda mucho y
termina dando error".

Contrato que fijan estos tests:
  - las páginas se analizan en paralelo, acotado por WEBSITE_EXTRACT_CONCURRENCY
  - una página que falla vale [] y NO tira abajo la corrida
  - una llamada colgada se corta por timeout en vez de frenar todo
  - el progreso reporta `done`/`total` para que el cliente pueda dibujar la barra
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.core.config import settings
from app.graphs.extraction import nodes
from app.graphs.extraction.nodes import extract_website_properties_llm


class _ToolUse:
    type = 'tool_use'

    def __init__(self, propiedades: list[dict[str, Any]]) -> None:
        self.input = {'propiedades': propiedades}


class _Msg:
    usage = None

    def __init__(self, propiedades: list[dict[str, Any]]) -> None:
        self.content = [_ToolUse(propiedades)]


def _pages(n: int) -> list[dict[str, str]]:
    return [{'url': f'https://inmo.com/p{i}', 'text': 'x' * 200} for i in range(n)]


def _stub_llm(monkeypatch, behaviour) -> None:
    class _Messages:
        async def create(self, **kwargs: Any) -> Any:
            return await behaviour(kwargs)

    monkeypatch.setattr(nodes._client, 'messages', _Messages(), raising=False)
    monkeypatch.setattr(nodes, 'record_llm_usage', _noop_usage)
    monkeypatch.setattr(nodes, 'harvest_page_images', _noop_gallery)


async def _noop_usage(*args: Any, **kwargs: Any) -> None:
    return None


async def _noop_gallery(urls: Any) -> dict[str, list[str]]:
    return {}


def _capture(monkeypatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    async def _dispatch(name: str, data: dict[str, Any], config: Any = None) -> None:
        seen.append(data)

    monkeypatch.setattr(nodes, 'adispatch_custom_event', _dispatch)
    return seen


@pytest.mark.asyncio
async def test_pages_are_analyzed_in_parallel_within_the_cap(monkeypatch) -> None:
    _capture(monkeypatch)
    monkeypatch.setattr(settings, 'WEBSITE_EXTRACT_CONCURRENCY', 4)

    live = 0
    peak = 0

    async def behaviour(_kwargs: dict[str, Any]) -> Any:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01)
        live -= 1
        return _Msg([])

    _stub_llm(monkeypatch, behaviour)

    await extract_website_properties_llm(
        {'website_pages': _pages(20), 'job_id': 'job-1'}, {'configurable': {}}
    )

    assert peak > 1, 'la extracción sigue siendo secuencial'
    assert peak <= 4


@pytest.mark.asyncio
async def test_one_failing_page_does_not_sink_the_run(monkeypatch) -> None:
    _capture(monkeypatch)
    monkeypatch.setattr(settings, 'WEBSITE_EXTRACT_CONCURRENCY', 4)

    async def behaviour(kwargs: dict[str, Any]) -> Any:
        if 'p3' in kwargs['messages'][0]['content']:
            raise RuntimeError('rate limit')
        return _Msg([{'titulo': 'Depto', 'precio': 100000}])

    _stub_llm(monkeypatch, behaviour)

    out = await extract_website_properties_llm(
        {'website_pages': _pages(6), 'job_id': 'job-1'}, {'configurable': {}}
    )

    assert len(out['website_properties']) == 5


@pytest.mark.asyncio
async def test_a_hung_call_is_cut_by_timeout(monkeypatch) -> None:
    _capture(monkeypatch)
    monkeypatch.setattr(settings, 'WEBSITE_EXTRACT_CONCURRENCY', 4)
    monkeypatch.setattr(settings, 'WEBSITE_EXTRACT_TIMEOUT', 0.02)

    async def behaviour(kwargs: dict[str, Any]) -> Any:
        if 'p0' in kwargs['messages'][0]['content']:
            await asyncio.sleep(5)
        return _Msg([{'titulo': 'Depto', 'precio': 100000}])

    _stub_llm(monkeypatch, behaviour)

    out = await asyncio.wait_for(
        extract_website_properties_llm(
            {'website_pages': _pages(3), 'job_id': 'job-1'}, {'configurable': {}}
        ),
        timeout=2,
    )

    assert len(out['website_properties']) == 2


@pytest.mark.asyncio
async def test_progress_carries_done_and_total(monkeypatch) -> None:
    seen = _capture(monkeypatch)
    monkeypatch.setattr(settings, 'WEBSITE_EXTRACT_CONCURRENCY', 4)

    async def behaviour(_kwargs: dict[str, Any]) -> Any:
        return _Msg([])

    _stub_llm(monkeypatch, behaviour)

    await extract_website_properties_llm(
        {'website_pages': _pages(10), 'job_id': 'job-1'}, {'configurable': {}}
    )

    extraccion = [e for e in seen if e.get('source') == 'extraccion']
    assert extraccion[0]['done'] == 0 and extraccion[0]['total'] == 10
    assert extraccion[-1]['status'] == 'done'
    assert (extraccion[-1]['done'], extraccion[-1]['total']) == (10, 10)
    # Un evento cada 5 páginas: con 1500 páginas, uno por página es ruido que
    # el cliente no llega a renderizar.
    assert len(extraccion) < 10
