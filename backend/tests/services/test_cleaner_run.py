"""Test-first for the BOT LIMPIADOR's full pass — `cleaner.run_cleanup`.

Walks every scraped property that has a `url_origen`, asks `check_url` for a
verdict and deletes the WHOLE property row when the listing is provably gone.

Non-negotiable invariants encoded here:
- `unknown` NEVER deletes. A throttled/blocked portal must not wipe the DB.
- `dry_run=True` reports what it WOULD delete and touches nothing.
- Every deleted row is snapshotted into the run record before it disappears,
  so a mistake is auditable instead of silent.
- One failing row (network, delete error) never aborts the rest of the run.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.services import cleaner
from app.services.cleaner import CheckResult


# ── fluent Supabase fake ─────────────────────────────────────────────────────


class _Res:
    def __init__(self, data: list[dict], count: int | None = None) -> None:
        self.data = data
        self.count = count


class _Not:
    """Backs the `.not_.is_(...)` chain PostgREST exposes."""

    def __init__(self, query: '_FakeQuery') -> None:
        self._query = query

    def is_(self, field: str, value: object) -> '_FakeQuery':
        self._query._filters.append(('not_is', field, value))
        return self._query


class _FakeQuery:
    def __init__(self, db: '_FakeSupabase', table: str, mode: str) -> None:
        self._db = db
        self._table = table
        self._mode = mode
        self._filters: list[tuple[str, str, object]] = []
        self._payload: dict[str, Any] | None = None
        self._limit: int | None = None
        self._order: tuple[str, bool] | None = None
        self._count: str | None = None

    # -- builders --
    def select(self, *_a: object, count: str | None = None, **_kw: object) -> '_FakeQuery':
        self._count = count
        return self

    def insert(self, payload: dict) -> '_FakeQuery':
        self._payload = payload
        return self

    def update(self, payload: dict) -> '_FakeQuery':
        self._payload = payload
        return self

    def upsert(self, payload: dict, **_kw: object) -> '_FakeQuery':
        self._payload = payload
        return self

    def delete(self) -> '_FakeQuery':
        return self

    def eq(self, field: str, value: object) -> '_FakeQuery':
        self._filters.append(('eq', field, value))
        return self

    def is_(self, field: str, value: object) -> '_FakeQuery':
        self._filters.append(('is', field, value))
        return self

    def in_(self, field: str, values: object) -> '_FakeQuery':
        self._filters.append(('in', field, list(values)))  # type: ignore[arg-type]
        return self

    @property
    def not_(self) -> _Not:
        return _Not(self)

    def order(self, field: str, desc: bool = False, **_kw: object) -> '_FakeQuery':
        self._order = (field, desc)
        return self

    def limit(self, n: int) -> '_FakeQuery':
        self._limit = n
        return self

    def range(self, start: int, end: int) -> '_FakeQuery':
        self._limit = end - start + 1
        return self

    # -- execution --
    def _match(self, row: dict) -> bool:
        for op, field, value in self._filters:
            current = row.get(field)
            if op == 'eq' and current != value:
                return False
            if op == 'is' and value == 'null' and current is not None:
                return False
            if op == 'not_is' and value == 'null' and current is None:
                return False
            if op == 'in' and current not in value:  # type: ignore[operator]
                return False
        return True

    async def execute(self) -> _Res:
        store = self._db.store(self._table)
        self._db.calls.append((self._table, self._mode, list(self._filters)))

        if self._mode == 'insert':
            row = dict(self._payload or {})
            row.setdefault('id', f'id-{uuid.uuid4().hex[:8]}')
            store.append(row)
            return _Res([row])

        if self._mode == 'update':
            matched = [r for r in store if self._match(r)]
            for r in matched:
                r.update(self._payload or {})
            return _Res(matched)

        if self._mode == 'delete':
            matched = [r for r in store if self._match(r)]
            for r in matched:
                store.remove(r)
            return _Res(matched)

        rows = [r for r in store if self._match(r)]
        if self._order:
            field, desc = self._order
            rows = sorted(rows, key=lambda r: r.get(field) or '', reverse=desc)
        total = len(rows)
        if self._limit is not None:
            rows = rows[: self._limit]
        return _Res(rows, count=total if self._count else None)


class _FakeTable:
    def __init__(self, db: '_FakeSupabase', name: str) -> None:
        self._db = db
        self._name = name

    def select(self, *a: object, **kw: object) -> _FakeQuery:
        return _FakeQuery(self._db, self._name, 'select').select(*a, **kw)

    def insert(self, payload: dict) -> _FakeQuery:
        return _FakeQuery(self._db, self._name, 'insert').insert(payload)

    def update(self, payload: dict) -> _FakeQuery:
        return _FakeQuery(self._db, self._name, 'update').update(payload)

    def upsert(self, payload: dict, **kw: object) -> _FakeQuery:
        return _FakeQuery(self._db, self._name, 'insert').upsert(payload, **kw)

    def delete(self) -> _FakeQuery:
        return _FakeQuery(self._db, self._name, 'delete')


class _FakeSupabase:
    def __init__(self, **tables: list[dict]) -> None:
        self._tables: dict[str, list[dict]] = {k: v for k, v in tables.items()}
        self.calls: list[tuple[str, str, list]] = []

    def store(self, name: str) -> list[dict]:
        return self._tables.setdefault(name, [])

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(self, name)


class _DeleteFailsSupabase(_FakeSupabase):
    def table(self, name: str) -> Any:
        table = super().table(name)
        if name != 'properties':
            return table
        original_delete = table.delete

        def delete() -> Any:
            query = original_delete()

            async def boom() -> _Res:
                raise RuntimeError('delete exploded')

            query.execute = boom  # type: ignore[method-assign]
            return query

        table.delete = delete  # type: ignore[method-assign]
        return table


# ── helpers ──────────────────────────────────────────────────────────────────


def _prop(url: str | None, *, titulo: str = 'Depto', checked_days_ago: int | None = None) -> dict:
    checked = (
        (datetime.now(timezone.utc) - timedelta(days=checked_days_ago)).isoformat()
        if checked_days_ago is not None
        else None
    )
    return {
        'id': f'p-{uuid.uuid4().hex[:8]}',
        'titulo': titulo,
        'direccion': 'Calle 1 123',
        'fuente': 'zonaprop',
        'url_origen': url,
        'ultima_verificacion': checked,
        'created_at': datetime.now(timezone.utc).isoformat(),
    }


def _verdicts(mapping: dict[str, str]):
    """Build a checker that answers from a {url: verdict} table."""

    async def checker(url: str, *, client: object) -> CheckResult:
        return CheckResult(verdict=mapping.get(url, 'alive'), reason=f'fake:{mapping.get(url)}')

    return checker


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    cleaner.reset_state()


# ── deletion semantics ───────────────────────────────────────────────────────


async def test_dead_listing_deletes_the_whole_property() -> None:
    dead = _prop('https://portal.com/gone')
    sb = _FakeSupabase(properties=[dead])

    await cleaner.run_cleanup(sb, checker=_verdicts({'https://portal.com/gone': 'dead'}))

    assert sb.store('properties') == []


async def test_alive_listing_is_kept() -> None:
    alive = _prop('https://portal.com/live')
    sb = _FakeSupabase(properties=[alive])

    await cleaner.run_cleanup(sb, checker=_verdicts({'https://portal.com/live': 'alive'}))

    assert [r['id'] for r in sb.store('properties')] == [alive['id']]


async def test_unknown_verdict_never_deletes() -> None:
    """THE invariant: a portal blocking us must not empty the database."""
    rows = [_prop(f'https://portal.com/{i}') for i in range(5)]
    sb = _FakeSupabase(properties=list(rows))
    checker = _verdicts({r['url_origen']: 'unknown' for r in rows})

    summary = await cleaner.run_cleanup(sb, checker=checker)

    assert len(sb.store('properties')) == 5
    assert summary['deleted'] == 0
    assert summary['unknown'] == 5


async def test_only_the_dead_ones_are_deleted_in_a_mixed_batch() -> None:
    alive = _prop('https://portal.com/live')
    dead = _prop('https://portal.com/gone')
    unknown = _prop('https://portal.com/blocked')
    sb = _FakeSupabase(properties=[alive, dead, unknown])

    summary = await cleaner.run_cleanup(sb, checker=_verdicts({
        'https://portal.com/live': 'alive',
        'https://portal.com/gone': 'dead',
        'https://portal.com/blocked': 'unknown',
    }))

    assert sorted(r['id'] for r in sb.store('properties')) == sorted([alive['id'], unknown['id']])
    assert summary == {
        **summary,
        'checked': 3, 'alive': 1, 'dead': 1, 'unknown': 1, 'deleted': 1,
    }


async def test_checker_exception_counts_as_unknown_and_keeps_the_row() -> None:
    row = _prop('https://portal.com/boom')
    sb = _FakeSupabase(properties=[row])

    async def exploding_checker(url: str, *, client: object) -> CheckResult:
        raise RuntimeError('network stack on fire')

    summary = await cleaner.run_cleanup(sb, checker=exploding_checker)

    assert len(sb.store('properties')) == 1
    assert summary['unknown'] == 1
    assert summary['deleted'] == 0


async def test_a_failing_delete_does_not_abort_the_run() -> None:
    rows = [_prop(f'https://portal.com/{i}') for i in range(3)]
    sb = _DeleteFailsSupabase(properties=list(rows))
    checker = _verdicts({r['url_origen']: 'dead' for r in rows})

    summary = await cleaner.run_cleanup(sb, checker=checker)

    assert summary['checked'] == 3
    assert summary['deleted'] == 0  # every delete failed, but the run completed


# ── dry run ──────────────────────────────────────────────────────────────────


async def test_dry_run_reports_without_deleting() -> None:
    dead = _prop('https://portal.com/gone')
    sb = _FakeSupabase(properties=[dead])

    summary = await cleaner.run_cleanup(
        sb, dry_run=True, checker=_verdicts({'https://portal.com/gone': 'dead'}),
    )

    assert len(sb.store('properties')) == 1
    assert summary['dead'] == 1
    assert summary['deleted'] == 0
    assert summary['dry_run'] is True


async def test_dry_run_still_lists_what_it_would_have_deleted() -> None:
    """El punto de simular es ver QUÉ se borraría, no sólo cuántas."""
    dead = _prop('https://portal.com/gone', titulo='PH en Tolosa')
    sb = _FakeSupabase(properties=[dead])

    summary = await cleaner.run_cleanup(
        sb, dry_run=True, checker=_verdicts({'https://portal.com/gone': 'dead'}),
    )

    assert [p['titulo'] for p in summary['eliminadas']] == ['PH en Tolosa']
    assert sb.store('cleanup_runs')[0]['eliminadas'][0]['motivo']


async def test_dry_run_does_not_stamp_the_verification_timestamp() -> None:
    row = _prop('https://portal.com/live')
    sb = _FakeSupabase(properties=[row])

    await cleaner.run_cleanup(sb, dry_run=True, checker=_verdicts({}))

    assert sb.store('properties')[0]['ultima_verificacion'] is None


# ── bookkeeping ──────────────────────────────────────────────────────────────


async def test_surviving_rows_get_their_verification_timestamp_stamped() -> None:
    row = _prop('https://portal.com/live')
    sb = _FakeSupabase(properties=[row])

    await cleaner.run_cleanup(sb, checker=_verdicts({}))

    assert sb.store('properties')[0]['ultima_verificacion'] is not None


async def test_never_checked_rows_are_prioritized_over_recently_checked_ones() -> None:
    fresh = _prop('https://portal.com/fresh', checked_days_ago=1)
    never = _prop('https://portal.com/never')
    sb = _FakeSupabase(properties=[fresh, never])

    seen: list[str] = []

    async def checker(url: str, *, client: object) -> CheckResult:
        seen.append(url)
        return CheckResult(verdict='alive', reason='ok')

    await cleaner.run_cleanup(sb, limit=1, checker=checker)

    assert seen == ['https://portal.com/never']


async def test_limit_caps_the_batch() -> None:
    sb = _FakeSupabase(properties=[_prop(f'https://portal.com/{i}') for i in range(10)])

    summary = await cleaner.run_cleanup(sb, limit=4, checker=_verdicts({}))

    assert summary['checked'] == 4


async def test_properties_without_a_url_are_skipped() -> None:
    sb = _FakeSupabase(properties=[_prop(None), _prop('https://portal.com/live')])

    summary = await cleaner.run_cleanup(sb, checker=_verdicts({}))

    assert summary['checked'] == 1


async def test_run_is_recorded_with_a_snapshot_of_every_deleted_property() -> None:
    dead = _prop('https://portal.com/gone', titulo='Casa en Gonnet')
    sb = _FakeSupabase(properties=[dead])

    await cleaner.run_cleanup(sb, checker=_verdicts({'https://portal.com/gone': 'dead'}))

    runs = sb.store('cleanup_runs')
    assert len(runs) == 1
    snapshot = runs[0]['eliminadas']
    assert [p['titulo'] for p in snapshot] == ['Casa en Gonnet']
    assert snapshot[0]['url_origen'] == 'https://portal.com/gone'
    assert snapshot[0]['motivo']


async def test_run_record_stores_the_origin_of_the_run() -> None:
    sb = _FakeSupabase(properties=[_prop('https://portal.com/live')])

    await cleaner.run_cleanup(sb, checker=_verdicts({}), origen='scheduled')

    assert sb.store('cleanup_runs')[0]['origen'] == 'scheduled'


async def test_failing_to_record_the_run_does_not_break_the_cleanup() -> None:
    dead = _prop('https://portal.com/gone')

    class _NoRunsTable(_FakeSupabase):
        def table(self, name: str) -> Any:
            if name == 'cleanup_runs':
                raise RuntimeError('table missing')
            return super().table(name)

    sb = _NoRunsTable(properties=[dead])
    summary = await cleaner.run_cleanup(sb, checker=_verdicts({'https://portal.com/gone': 'dead'}))

    assert summary['deleted'] == 1


# ── concurrency / lifecycle ──────────────────────────────────────────────────


async def test_state_is_exposed_and_marks_the_run_finished() -> None:
    sb = _FakeSupabase(properties=[_prop('https://portal.com/live')])

    await cleaner.run_cleanup(sb, checker=_verdicts({}))
    state = cleaner.cleanup_state()

    assert state['running'] is False
    assert state['started_at'] and state['finished_at']
    assert state['checked'] == 1


async def test_a_second_run_while_one_is_in_flight_is_a_noop() -> None:
    import asyncio

    sb = _FakeSupabase(properties=[_prop(f'https://portal.com/{i}') for i in range(3)])
    gate = asyncio.Event()

    async def slow_checker(url: str, *, client: object) -> CheckResult:
        await gate.wait()
        return CheckResult(verdict='alive', reason='ok')

    first = asyncio.ensure_future(cleaner.run_cleanup(sb, checker=slow_checker))
    await asyncio.sleep(0)  # let the first run take the lock
    second = await cleaner.run_cleanup(sb, checker=_verdicts({}))
    gate.set()
    await first

    assert second['skipped'] is True


async def test_without_supabase_the_run_is_skipped() -> None:
    summary = await cleaner.run_cleanup(None, checker=_verdicts({}))

    assert summary['skipped'] is True
