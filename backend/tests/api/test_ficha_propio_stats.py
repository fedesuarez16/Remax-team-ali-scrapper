"""Test-first for `GET /api/v1/properties/ficha-propio/stats` — the auto counter.

Two numbers, both derived (never hand-maintained), so they can't drift:
- how many Fichas Propio exist  → `properties` rows with `fuente='manual'`
- what they cost so far         → sum of `llm_usage.cost_usd` scoped to ficha propio

Both must degrade to a rendered zero rather than a broken page: the counter is
informational, the Ficha Propio tab is not.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


class _Res:
    def __init__(self, data: list[dict[str, Any]], count: int | None = None) -> None:
        self.data = data
        self.count = count


class _FakeQuery:
    def __init__(self, rows: list[dict[str, Any]], *, exact_count: bool) -> None:
        self._rows = rows
        self._exact_count = exact_count
        self._filters: list[tuple[str, Any]] = []

    def eq(self, field: str, value: Any) -> '_FakeQuery':
        self._filters.append((field, value))
        return self

    async def execute(self) -> _Res:
        rows = [r for r in self._rows if all(r.get(f) == v for f, v in self._filters)]
        # PostgREST with count='exact' returns the count alongside the rows.
        return _Res(rows, count=len(rows) if self._exact_count else None)


class _FakeTable:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def select(self, *_a: Any, **kw: Any) -> _FakeQuery:
        return _FakeQuery(self._rows, exact_count=kw.get('count') == 'exact')


class _FakeSupabase:
    def __init__(self, properties: list[dict], usage: list[dict]) -> None:
        self._properties = properties
        self._usage = usage

    def table(self, name: str) -> _FakeTable:
        if name == 'properties':
            return _FakeTable(self._properties)
        if name == 'llm_usage':
            return _FakeTable(self._usage)
        raise AssertionError(f'unexpected table {name}')


class _BrokenUsageSupabase(_FakeSupabase):
    """`llm_usage` unreachable — models the migration not being applied yet."""

    def table(self, name: str) -> _FakeTable:
        if name == 'llm_usage':
            raise RuntimeError('relation "llm_usage" does not exist')
        return super().table(name)


def _client(sb: Any) -> AsyncClient:
    from app.api.v1 import properties

    app = FastAPI()
    app.include_router(properties.router, prefix='/properties')
    app.state.supabase = sb
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


_PROPS = [
    {'id': 'p1', 'fuente': 'manual'},
    {'id': 'p2', 'fuente': 'manual'},
    {'id': 'p3', 'fuente': 'zonaprop'},  # portal scrape — not a Ficha Propio
]
_USAGE = [
    {'scope': 'ficha_propio', 'cost_usd': 0.0021},
    {'scope': 'ficha_propio', 'cost_usd': 0.0034},
    {'scope': 'ficha_enrich', 'cost_usd': 0.9},  # enrich of a portal ficha — not ours
]


async def test_counts_only_fichas_propio_and_sums_only_their_spend() -> None:
    async with _client(_FakeSupabase(_PROPS, _USAGE)) as c:
        body = (await c.get('/properties/ficha-propio/stats')).json()

    assert body['total_fichas'] == 2
    assert body['gasto_usd'] == 0.0055
    assert body['llamadas'] == 2


async def test_empty_state_reports_zeros() -> None:
    async with _client(_FakeSupabase([], [])) as c:
        body = (await c.get('/properties/ficha-propio/stats')).json()

    assert body == {'total_fichas': 0, 'gasto_usd': 0.0, 'llamadas': 0}


async def test_cost_survives_an_unreachable_usage_table() -> None:
    """Migration not applied yet must still render the ficha count."""
    async with _client(_BrokenUsageSupabase(_PROPS, _USAGE)) as c:
        res = await c.get('/properties/ficha-propio/stats')

    assert res.status_code == 200
    body = res.json()
    assert body['total_fichas'] == 2
    assert body['gasto_usd'] == 0.0


async def test_no_supabase_reports_zeros_not_an_error() -> None:
    async with _client(None) as c:
        res = await c.get('/properties/ficha-propio/stats')

    assert res.status_code == 200
    assert res.json()['total_fichas'] == 0


async def test_stats_route_is_not_swallowed_by_the_property_id_catch_all() -> None:
    """`/{property_id}` would happily match 'ficha-propio' — declaration order matters."""
    async with _client(_FakeSupabase(_PROPS, _USAGE)) as c:
        body = (await c.get('/properties/ficha-propio/stats')).json()

    assert 'total_fichas' in body  # not a property lookup response
