"""Test-first: el stream no puede morir por estar callado.

Una búsqueda con 260 inmobiliarias pasa minutos sin emitir un solo evento
mientras el fan-out corre. Railway/Vercel cortan una conexión ociosa y el
cliente lo muestra como `Error:` con la búsqueda todavía viva del lado del
servidor — que es exactamente el síntoma reportado.

El generador tiene que mandar un frame de comentario SSE (`: keepalive`)
mientras espera. Es un comentario: ni `EventSource` ni el lector de `data:` del
cliente lo ven, así que no puede confundirse con un evento real.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.core.config import settings


class _SlowGraph:
    """Tarda más que el intervalo de keepalive antes de emitir `done`."""

    async def astream_events(self, _inputs: Any, _config: Any, version: str = 'v2'):
        await asyncio.sleep(0.05)
        yield {'event': 'on_custom_event', 'name': 'done',
               'data': {'event': 'done', 'job_id': 'job-1', 'total_count': 3}}


async def _drain(monkeypatch: Any) -> list[str]:
    from app.api.v1 import scraping

    monkeypatch.setattr(settings, 'SSE_KEEPALIVE_SECONDS', 0.01)

    queue: asyncio.Queue[Any] = asyncio.Queue()
    asyncio.ensure_future(
        scraping._run_graph_into_queue(_SlowGraph(), {}, {}, queue, None, 'job-1', {})
    )
    return [chunk async for chunk in scraping._stream_graph_events(queue, None, 'job-1', {})]


@pytest.mark.asyncio
async def test_idle_stream_emits_keepalive_comments(monkeypatch) -> None:
    chunks = await _drain(monkeypatch)

    assert any(c == ': keepalive\n\n' for c in chunks), chunks


@pytest.mark.asyncio
async def test_keepalive_does_not_shadow_real_events(monkeypatch) -> None:
    chunks = await _drain(monkeypatch)

    real = [c for c in chunks if c != ': keepalive\n\n']
    assert len(real) == 1
    assert 'event: done' in real[0]
    # Un keepalive no gasta número de secuencia: el `id:` tiene que seguir
    # siendo el contador de eventos reales para que el resume por Last-Event-ID
    # no se desalinee.
    assert real[0].startswith('id: 1\n')


@pytest.mark.asyncio
async def test_both_endpoints_share_one_generator() -> None:
    """El keepalive tiene que estar en /stream y en /resume. Estaban duplicados
    y sólo uno de los dos se arreglaba cada vez."""
    import inspect

    from app.api.v1 import scraping

    for fn in (scraping.stream_scraping, scraping.resume_scraping):
        assert '_stream_graph_events' in inspect.getsource(fn)
