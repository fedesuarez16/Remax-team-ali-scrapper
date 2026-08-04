"""Test-first for surfacing per-search Apify spend in the CRM.

Two seams:
- `_write_job_terminal` persists the ledger onto the job row when the search
  ends (both `done` and `error` — a failed search still burned credits);
- `GET /api/v1/search-history` joins that cost back onto each saved search, so
  the historial can render it. The join is best-effort: the sidebar must never
  break over an optional number (e.g. migration not applied yet).
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.api.test_search_history import _FakeSupabase, _FakeTable


class _CapturingSupabase:
    """Records the update payload `_write_job_terminal` sends."""

    def __init__(self, *, reject_keys: set[str] | None = None) -> None:
        self.payloads: list[dict[str, Any]] = []
        self._reject_keys = reject_keys or set()

    def table(self, name: str) -> '_CapturingSupabase':
        assert name == 'scraping_jobs'
        return self

    def update(self, payload: dict[str, Any]) -> '_CapturingSupabase':
        self._pending = payload
        return self

    def eq(self, *_a: Any, **_kw: Any) -> '_CapturingSupabase':
        return self

    async def execute(self) -> Any:
        payload = self._pending
        if self._reject_keys & set(payload):
            # Models PostgREST rejecting an unknown column.
            raise RuntimeError('column "apify_cost_usd" does not exist')
        self.payloads.append(payload)

        class _Res:
            data: list[dict[str, Any]] = []

        return _Res()


# ── job write-back ───────────────────────────────────────────────────────────


async def test_terminal_write_persists_total_and_breakdown() -> None:
    from app.api.v1.scraping import _write_job_terminal

    sb = _CapturingSupabase()
    ledger = {'zonaprop': {'usd': 0.03, 'runs': 3}, 'argenprop': {'usd': 0.01, 'runs': 1}}
    await _write_job_terminal(sb, 'job-1', 'done', 42, ledger)

    payload = sb.payloads[-1]
    assert payload['estado'] == 'done'
    assert payload['prop_count'] == 42
    assert payload['apify_cost_usd'] == 0.04
    assert payload['apify_cost_breakdown'] == ledger


async def test_failed_search_still_records_what_it_burned() -> None:
    from app.api.v1.scraping import _write_job_terminal

    sb = _CapturingSupabase()
    await _write_job_terminal(sb, 'job-1', 'error', 0, {'zonaprop': {'usd': 0.02, 'runs': 2}})

    assert sb.payloads[-1]['apify_cost_usd'] == 0.02


async def test_empty_ledger_records_zero_not_null() -> None:
    """A cache-served or mercadolibre-only search cost $0 — that is a FACT worth
    storing, and it's what makes the cache's value visible. NULL means unknown."""
    from app.api.v1.scraping import _write_job_terminal

    sb = _CapturingSupabase()
    await _write_job_terminal(sb, 'job-1', 'done', 10, {})

    assert sb.payloads[-1]['apify_cost_usd'] == 0.0
    assert sb.payloads[-1]['apify_cost_breakdown'] == {}


async def test_no_ledger_omits_the_cost_columns_entirely() -> None:
    from app.api.v1.scraping import _write_job_terminal

    sb = _CapturingSupabase()
    await _write_job_terminal(sb, 'job-1', 'done', 10)

    assert 'apify_cost_usd' not in sb.payloads[-1]


async def test_write_falls_back_when_cost_columns_are_missing() -> None:
    """Migration not applied yet must not cost us the estado/prop_count write."""
    from app.api.v1.scraping import _write_job_terminal

    sb = _CapturingSupabase(reject_keys={'apify_cost_usd'})
    await _write_job_terminal(sb, 'job-1', 'done', 7, {'zonaprop': {'usd': 0.01, 'runs': 1}})

    assert len(sb.payloads) == 1
    payload = sb.payloads[0]
    assert payload['estado'] == 'done'
    assert payload['prop_count'] == 7
    assert 'apify_cost_usd' not in payload


# ── resume continuity ────────────────────────────────────────────────────────


class _JobRowSupabase:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def table(self, name: str) -> '_JobRowSupabase':
        assert name == 'scraping_jobs'
        return self

    def select(self, *_a: Any) -> '_JobRowSupabase':
        return self

    def eq(self, *_a: Any) -> '_JobRowSupabase':
        return self

    async def execute(self) -> Any:
        class _Res:
            pass

        res = _Res()
        res.data = [self._row] if self._row else []  # type: ignore[attr-defined]
        return res


async def test_resume_seeds_the_ledger_from_what_the_job_already_spent() -> None:
    """Resume is a fresh request: an empty ledger would overwrite (lose) the
    original run's spend on the terminal write."""
    from app.api.v1.scraping import _seed_cost_ledger

    sb = _JobRowSupabase({'apify_cost_breakdown': {'zonaprop': {'usd': 0.03, 'runs': 2}}})
    assert await _seed_cost_ledger(sb, 'job-1') == {'zonaprop': {'usd': 0.03, 'runs': 2}}


async def test_resume_seed_tolerates_legacy_and_missing_rows() -> None:
    from app.api.v1.scraping import _seed_cost_ledger

    assert await _seed_cost_ledger(_JobRowSupabase(None), 'job-1') == {}
    assert await _seed_cost_ledger(_JobRowSupabase({'apify_cost_breakdown': None}), 'job-1') == {}
    assert await _seed_cost_ledger(None, 'job-1') == {}


# ── history join ─────────────────────────────────────────────────────────────


class _SupabaseWithJobs(_FakeSupabase):
    def __init__(self, rows: list[dict], jobs: list[dict]) -> None:
        super().__init__(rows=rows)
        self._jobs = jobs

    def table(self, name: str):  # type: ignore[no-untyped-def]
        if name == 'scraping_jobs':
            return _FakeTable(self._jobs, insert_defaults={})
        return super().table(name)


def _client(sb: Any) -> AsyncClient:
    from app.api.v1 import search_history

    app = FastAPI()
    app.include_router(search_history.router, prefix='/search-history')
    app.state.supabase = sb
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


async def test_history_entries_carry_their_search_cost() -> None:
    sb = _SupabaseWithJobs(
        rows=[
            {'id': 'h1', 'query': 'casas villa elisa', 'job_id': 'job-1', 'created_at': '2026-08-01T10:00:00Z'},
            {'id': 'h2', 'query': 'ph la plata', 'job_id': 'job-2', 'created_at': '2026-08-01T09:00:00Z'},
        ],
        jobs=[
            {'id': 'job-1', 'apify_cost_usd': 0.0412, 'apify_cost_breakdown': {'zonaprop': {'usd': 0.0412, 'runs': 3}}},
            {'id': 'job-2', 'apify_cost_usd': 0.0, 'apify_cost_breakdown': {}},
        ],
    )
    async with _client(sb) as c:
        body = (await c.get('/search-history')).json()

    by_id = {h['id']: h for h in body['history']}
    assert by_id['h1']['apify_cost_usd'] == 0.0412
    assert by_id['h1']['apify_cost_breakdown'] == {'zonaprop': {'usd': 0.0412, 'runs': 3}}
    assert by_id['h2']['apify_cost_usd'] == 0.0


async def test_entry_without_job_is_left_alone() -> None:
    sb = _SupabaseWithJobs(
        rows=[{'id': 'h1', 'query': 'chat search', 'job_id': None, 'created_at': '2026-08-01T10:00:00Z'}],
        jobs=[],
    )
    async with _client(sb) as c:
        body = (await c.get('/search-history')).json()

    assert body['history'][0].get('apify_cost_usd') is None


async def test_history_survives_a_broken_jobs_lookup() -> None:
    """Cost is a nice-to-have; the history listing is not."""
    sb = _FakeSupabase(  # `.table('scraping_jobs')` raises AssertionError here
        rows=[{'id': 'h1', 'query': 'q', 'job_id': 'job-1', 'created_at': '2026-08-01T10:00:00Z'}],
    )
    async with _client(sb) as c:
        res = await c.get('/search-history')

    assert res.status_code == 200
    assert res.json()['history'][0]['id'] == 'h1'
