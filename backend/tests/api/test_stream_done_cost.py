"""Test-first for the operator-facing ask: every search must report what it
spent on Apify AT THE MOMENT IT FINISHES — not only later in the historial.

Both entry points (the map and /chat) consume the same SSE `done` event, so the
tally is stamped onto that payload in `scraping.py` rather than inside the graph
nodes: there are five `done` dispatch sites in `nodes.py`, and exactly one place
that owns the ledger.

`/resume` gets the same treatment, and its number must be the ACCUMULATED total
(seed from the row + what this round burned), because a resumed search is one
search to the operator.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _done_event(**data: Any) -> dict[str, Any]:
    return {'event': 'on_custom_event', 'name': 'done', 'data': {'event': 'done', **data}}


class _SpendingGraph:
    """Books actor spend the way a real node does (via the ContextVar ledger),
    then dispatches `done` — mirroring the real ordering."""

    def __init__(self, spend: list[tuple[str, float]], total_count: int = 7) -> None:
        self._spend = spend
        self._total_count = total_count

    async def astream_events(self, _inputs: Any, _config: Any, version: str = 'v2'):
        from app.services.apify import record_run_cost

        for source, usd in self._spend:
            record_run_cost(source, usd)
        yield _done_event(job_id='job-1', total_count=self._total_count)


class _NoSupabase:
    pass


def _make_app(graph: Any, monkeypatch: Any, sb: Any = None) -> FastAPI:
    from app.api.v1 import scraping

    monkeypatch.setattr(scraping, 'build_graph', lambda checkpointer=None: graph)
    app = FastAPI()
    app.include_router(scraping.router)
    app.state.supabase = sb
    app.state.checkpointer = None
    return app


def _parse_sse(raw: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    name = None
    for line in raw.splitlines():
        if line.startswith('event: '):
            name = line[len('event: '):]
        elif line.startswith('data: ') and name is not None:
            events.append((name, json.loads(line[len('data: '):])))
            name = None
    return events


async def _collect(app: FastAPI, method: str, url: str, **kwargs: Any) -> list[tuple[str, dict[str, Any]]]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        async with client.stream(method, url, **kwargs) as resp:
            body = ''.join([chunk async for chunk in resp.aiter_text()])
    return _parse_sse(body)


# ── /stream (map + /chat share this endpoint) ────────────────────────────────


async def test_done_event_reports_what_the_search_spent(monkeypatch) -> None:
    graph = _SpendingGraph([('zonaprop', 0.03), ('zonaprop', 0.01), ('argenprop', 0.02)])
    app = _make_app(graph, monkeypatch)

    events = await _collect(app, 'GET', '/job-1/stream', params={'query': 'Casas en City Bell'})

    name, data = events[-1]
    assert name == 'done'
    assert data['apify_cost_usd'] == 0.06
    assert data['apify_cost_breakdown'] == {
        'zonaprop': {'usd': 0.04, 'runs': 2},
        'argenprop': {'usd': 0.02, 'runs': 1},
    }
    # The pre-existing payload must survive untouched.
    assert data['total_count'] == 7
    assert data['job_id'] == 'job-1'


async def test_free_search_reports_zero_not_a_missing_field(monkeypatch) -> None:
    """A cache hit / MercadoLibre-only search really cost nothing. Saying so is
    the whole point — an absent field would render as "unknown" in the UI."""
    app = _make_app(_SpendingGraph([]), monkeypatch)

    events = await _collect(app, 'GET', '/job-1/stream', params={'query': 'Casas en City Bell'})

    _, data = events[-1]
    assert data['apify_cost_usd'] == 0.0
    assert data['apify_cost_breakdown'] == {}


async def test_non_terminal_events_are_left_alone(monkeypatch) -> None:
    """Only `done` carries the tally: a progress row with a half-formed cost
    would show the operator a number that is not the search's cost."""
    class _ProgressThenDone:
        async def astream_events(self, _inputs: Any, _config: Any, version: str = 'v2'):
            from app.services.apify import record_run_cost

            record_run_cost('zonaprop', 0.03)
            yield {
                'event': 'on_custom_event', 'name': 'progress',
                'data': {'event': 'progress', 'source': 'zonaprop', 'status': 'done',
                         'count': 12, 'message': ''},
            }
            yield _done_event(job_id='job-1', total_count=12)

    app = _make_app(_ProgressThenDone(), monkeypatch)

    events = await _collect(app, 'GET', '/job-1/stream', params={'query': 'q'})

    progress = next(d for n, d in events if n == 'progress')
    assert 'apify_cost_usd' not in progress
    assert next(d for n, d in events if n == 'done')['apify_cost_usd'] == 0.03


# ── /resume ──────────────────────────────────────────────────────────────────


class _JobRowSupabase:
    """Serves `apify_cost_breakdown` for the resume seed and swallows the
    terminal update."""

    def __init__(self, breakdown: dict[str, Any] | None) -> None:
        self._breakdown = breakdown
        self.updates: list[dict[str, Any]] = []
        self._pending: dict[str, Any] | None = None
        self._mode = 'select'

    def table(self, _name: str) -> '_JobRowSupabase':
        return self

    def select(self, *_a: Any) -> '_JobRowSupabase':
        self._mode = 'select'
        return self

    def update(self, payload: dict[str, Any]) -> '_JobRowSupabase':
        self._mode = 'update'
        self._pending = payload
        return self

    def eq(self, *_a: Any) -> '_JobRowSupabase':
        return self

    async def execute(self) -> Any:
        class _Res:
            data: list[dict[str, Any]] = []

        res = _Res()
        if self._mode == 'update':
            self.updates.append(self._pending or {})
            return res
        res.data = [{'apify_cost_breakdown': self._breakdown}] if self._breakdown is not None else []
        return res


async def test_resume_done_event_reports_the_accumulated_total(monkeypatch) -> None:
    sb = _JobRowSupabase({'zonaprop': {'usd': 0.03, 'runs': 2}})
    app = _make_app(_SpendingGraph([('googlemaps', 0.02)]), monkeypatch, sb=sb)

    events = await _collect(
        app, 'POST', '/job-1/resume', json={'selected_agency_ids': ['a1']},
    )

    _, data = events[-1]
    assert data['apify_cost_usd'] == 0.05
    assert data['apify_cost_breakdown'] == {
        'zonaprop': {'usd': 0.03, 'runs': 2},
        'googlemaps': {'usd': 0.02, 'runs': 1},
    }
