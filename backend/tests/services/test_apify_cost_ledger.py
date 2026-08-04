"""Test-first for the per-search Apify spend ledger.

Every Apify run object carries `usageTotalUsd`, but `_run_actor` used to read
`status` off the poll response and throw the rest away. These tests pin the
ledger contract:

- the tally is scoped to a SEARCH, not to a run and not to a service instance
  (`get_apify_service()` builds a fresh `ApifyService` on every call, and a
  single job calls it several times);
- ZonaProp fires one run PER PAGE, so a search's cost is a SUM;
- sources that never touch an actor (mercadolibre, remax) and cache hits leave
  no entry at all — that absence is the signal that the search was free.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services import apify as apify_mod
from app.services.apify import ApifyService, ledger_total_usd, use_cost_ledger


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    """Models the three calls `_run_actor` makes: POST start, GET status, GET dataset.

    `runs` is consumed in order — one entry per actor run the test expects.
    """

    def __init__(self, runs: list[dict[str, Any]]) -> None:
        self._runs = runs
        self.started = 0

    async def post(self, url: str, params: Any = None, json: Any = None) -> _FakeResponse:
        self.started += 1
        return _FakeResponse({'data': {'id': f'run-{self.started}'}})

    async def get(self, url: str, params: Any = None) -> _FakeResponse:
        run = self._runs[self.started - 1]
        if url.endswith('/dataset/items'):
            return _FakeResponse(run.get('items', []))
        data: dict[str, Any] = {'id': f'run-{self.started}', 'status': run.get('status', 'SUCCEEDED')}
        if 'usageTotalUsd' in run:
            data['usageTotalUsd'] = run['usageTotalUsd']
        return _FakeResponse({'data': data})


@pytest.fixture(autouse=True)
def _no_poll_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apify_mod, '_POLL_INTERVAL', 0.0)


def _service(runs: list[dict[str, Any]]) -> ApifyService:
    svc = ApifyService(api_token='dummy-token')
    svc._client = _FakeClient(runs)  # type: ignore[assignment]
    return svc


async def test_run_actor_books_usage_into_the_active_ledger() -> None:
    svc = _service([{'usageTotalUsd': 0.0123, 'items': [{'a': 1}]}])
    ledger: dict[str, dict[str, Any]] = {}

    with use_cost_ledger(ledger):
        items = await svc._run_actor('zonaprop', 'actor-id', {})

    assert items == [{'a': 1}]  # dataset still returned untouched
    assert ledger == {'zonaprop': {'usd': 0.0123, 'runs': 1}}


async def test_paginated_source_sums_every_run_into_one_entry() -> None:
    svc = _service([
        {'usageTotalUsd': 0.01, 'items': []},
        {'usageTotalUsd': 0.02, 'items': []},
        {'usageTotalUsd': 0.005, 'items': []},
    ])
    ledger: dict[str, dict[str, Any]] = {}

    with use_cost_ledger(ledger):
        for _ in range(3):
            await svc._run_actor('zonaprop', 'actor-id', {})

    assert ledger['zonaprop']['runs'] == 3
    assert ledger['zonaprop']['usd'] == pytest.approx(0.035)


async def test_sources_are_tallied_separately_and_totalled() -> None:
    svc = _service([
        {'usageTotalUsd': 0.01, 'items': []},
        {'usageTotalUsd': 0.04, 'items': []},
    ])
    ledger: dict[str, dict[str, Any]] = {}

    with use_cost_ledger(ledger):
        await svc._run_actor('zonaprop', 'actor-a', {})
        await svc._run_actor('googlemaps', 'actor-b', {})

    assert set(ledger) == {'zonaprop', 'googlemaps'}
    assert ledger_total_usd(ledger) == pytest.approx(0.05)


async def test_free_sources_leave_no_entry() -> None:
    """mercadolibre/remax never reach `_run_actor` — an empty ledger means $0,
    and that is exactly how a cache-served search must read."""
    assert ledger_total_usd({}) == 0.0


async def test_missing_usage_field_still_counts_the_run() -> None:
    svc = _service([{'items': []}])  # no usageTotalUsd at all
    ledger: dict[str, dict[str, Any]] = {}

    with use_cost_ledger(ledger):
        await svc._run_actor('argenprop', 'actor-id', {})

    assert ledger == {'argenprop': {'usd': 0.0, 'runs': 1}}


async def test_failed_run_books_its_spend_before_raising() -> None:
    """Apify bills aborted/failed runs too — dropping that spend would under-report."""
    svc = _service([{'status': 'FAILED', 'usageTotalUsd': 0.007}])
    ledger: dict[str, dict[str, Any]] = {}

    with use_cost_ledger(ledger), pytest.raises(RuntimeError):
        await svc._run_actor('zonaprop', 'actor-id', {})

    assert ledger == {'zonaprop': {'usd': 0.007, 'runs': 1}}


async def test_no_active_ledger_is_a_noop() -> None:
    """Ficha/importer paths call actors outside a search — must not blow up."""
    svc = _service([{'usageTotalUsd': 0.02, 'items': [{'a': 1}]}])
    assert await svc._run_actor('instagram', 'actor-id', {}) == [{'a': 1}]


async def test_ledgers_do_not_leak_between_searches() -> None:
    svc = _service([{'usageTotalUsd': 0.01, 'items': []}, {'usageTotalUsd': 0.02, 'items': []}])

    first: dict[str, dict[str, Any]] = {}
    with use_cost_ledger(first):
        await svc._run_actor('zonaprop', 'actor-id', {})

    second: dict[str, dict[str, Any]] = {}
    with use_cost_ledger(second):
        await svc._run_actor('zonaprop', 'actor-id', {})

    assert first['zonaprop']['usd'] == pytest.approx(0.01)
    assert second['zonaprop']['usd'] == pytest.approx(0.02)


async def test_total_is_rounded_to_four_decimals() -> None:
    assert ledger_total_usd({'a': {'usd': 0.000123456, 'runs': 1}}) == 0.0001
