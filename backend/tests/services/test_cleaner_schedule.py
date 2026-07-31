"""Test-first for the BOT LIMPIADOR's automatic schedule — "cada 7 días",
"cada 30 días", "cada X días".

The cadence lives in the DB (`cleanup_schedule`), not in memory, so a backend
restart neither loses the setting nor re-triggers a cleanup that already ran.
The in-process loop only asks one question every tick: *is it due?*
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import cleaner
from app.services.cleaner import CheckResult

from tests.services.test_cleaner_run import _FakeSupabase, _prop


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


def _schedule(**over: object) -> dict:
    base = {'enabled': True, 'interval_days': 7, 'last_run_at': None}
    return {**base, **over}


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    cleaner.reset_state()


# ── is_due ───────────────────────────────────────────────────────────────────


def test_disabled_schedule_is_never_due() -> None:
    schedule = _schedule(enabled=False, last_run_at=(NOW - timedelta(days=999)).isoformat())
    assert cleaner.is_due(schedule, now=NOW) is False


def test_enabled_schedule_that_never_ran_is_due_immediately() -> None:
    assert cleaner.is_due(_schedule(), now=NOW) is True


def test_not_due_before_the_interval_elapses() -> None:
    schedule = _schedule(interval_days=7, last_run_at=(NOW - timedelta(days=6)).isoformat())
    assert cleaner.is_due(schedule, now=NOW) is False


def test_due_once_the_interval_elapsed() -> None:
    schedule = _schedule(interval_days=7, last_run_at=(NOW - timedelta(days=7, minutes=1)).isoformat())
    assert cleaner.is_due(schedule, now=NOW) is True


def test_thirty_day_cadence_is_respected() -> None:
    schedule = _schedule(interval_days=30, last_run_at=(NOW - timedelta(days=29)).isoformat())
    assert cleaner.is_due(schedule, now=NOW) is False
    assert cleaner.is_due({**schedule, 'last_run_at': (NOW - timedelta(days=31)).isoformat()}, now=NOW) is True


def test_arbitrary_cadence_is_respected() -> None:
    schedule = _schedule(interval_days=3, last_run_at=(NOW - timedelta(days=4)).isoformat())
    assert cleaner.is_due(schedule, now=NOW) is True


def test_a_naive_last_run_timestamp_is_treated_as_utc() -> None:
    """Postgres may hand back a timestamp without an offset; comparing it to an
    aware `now` would raise and freeze the scheduler forever."""
    schedule = _schedule(last_run_at=(NOW - timedelta(days=10)).replace(tzinfo=None).isoformat())
    assert cleaner.is_due(schedule, now=NOW) is True


def test_an_unparseable_last_run_timestamp_makes_it_due_rather_than_stuck() -> None:
    assert cleaner.is_due(_schedule(last_run_at='no-soy-una-fecha'), now=NOW) is True


# ── next_run_at ──────────────────────────────────────────────────────────────


def test_next_run_is_last_run_plus_the_interval() -> None:
    last = NOW - timedelta(days=2)
    schedule = _schedule(interval_days=7, last_run_at=last.isoformat())
    assert cleaner.next_run_at(schedule) == (last + timedelta(days=7)).isoformat()


def test_next_run_is_none_when_disabled() -> None:
    assert cleaner.next_run_at(_schedule(enabled=False)) is None


def test_next_run_is_none_when_it_never_ran() -> None:
    assert cleaner.next_run_at(_schedule()) is None


# ── read / save ──────────────────────────────────────────────────────────────


async def test_reading_an_unconfigured_schedule_returns_safe_defaults() -> None:
    schedule = await cleaner.read_schedule(_FakeSupabase())

    assert schedule['enabled'] is False  # automatic deletion is opt-in
    assert schedule['interval_days'] == cleaner.DEFAULT_INTERVAL_DAYS
    assert schedule['last_run_at'] is None


async def test_reading_without_supabase_returns_defaults() -> None:
    schedule = await cleaner.read_schedule(None)

    assert schedule['enabled'] is False


async def test_saving_then_reading_round_trips() -> None:
    sb = _FakeSupabase()
    await cleaner.save_schedule(sb, enabled=True, interval_days=30)

    schedule = await cleaner.read_schedule(sb)
    assert schedule['enabled'] is True
    assert schedule['interval_days'] == 30


async def test_saving_exposes_the_next_run() -> None:
    sb = _FakeSupabase(cleanup_schedule=[{
        'id': cleaner.SCHEDULE_ID, 'enabled': False, 'interval_days': 7,
        'last_run_at': (NOW - timedelta(days=1)).isoformat(),
    }])

    saved = await cleaner.save_schedule(sb, enabled=True, interval_days=7)
    assert saved['next_run_at'] == (NOW - timedelta(days=1) + timedelta(days=7)).isoformat()


async def test_interval_below_one_day_is_rejected() -> None:
    with pytest.raises(ValueError):
        await cleaner.save_schedule(_FakeSupabase(), enabled=True, interval_days=0)


async def test_absurd_interval_is_rejected() -> None:
    with pytest.raises(ValueError):
        await cleaner.save_schedule(_FakeSupabase(), enabled=True, interval_days=5000)


async def test_non_numeric_interval_is_rejected() -> None:
    with pytest.raises(ValueError):
        await cleaner.save_schedule(_FakeSupabase(), enabled=True, interval_days='siete')  # type: ignore[arg-type]


# ── scheduler tick ───────────────────────────────────────────────────────────


async def _alive(url: str, *, client: object) -> CheckResult:
    return CheckResult(verdict='alive', reason='ok')


async def test_tick_runs_the_cleanup_when_due() -> None:
    sb = _FakeSupabase(
        properties=[_prop('https://portal.com/live')],
        cleanup_schedule=[{'id': cleaner.SCHEDULE_ID, 'enabled': True, 'interval_days': 7,
                           'last_run_at': None}],
    )

    result = await cleaner.scheduler_tick(sb, checker=_alive)

    assert result['ran'] is True
    assert result['summary']['checked'] == 1


async def test_tick_is_a_noop_when_not_due() -> None:
    sb = _FakeSupabase(
        properties=[_prop('https://portal.com/live')],
        cleanup_schedule=[{
            'id': cleaner.SCHEDULE_ID, 'enabled': True, 'interval_days': 7,
            'last_run_at': datetime.now(timezone.utc).isoformat(),
        }],
    )

    result = await cleaner.scheduler_tick(sb, checker=_alive)

    assert result['ran'] is False


async def test_tick_is_a_noop_when_disabled() -> None:
    sb = _FakeSupabase(
        properties=[_prop('https://portal.com/live')],
        cleanup_schedule=[{'id': cleaner.SCHEDULE_ID, 'enabled': False, 'interval_days': 7,
                           'last_run_at': None}],
    )

    result = await cleaner.scheduler_tick(sb, checker=_alive)

    assert result['ran'] is False


async def test_tick_stamps_last_run_so_the_next_tick_stands_down() -> None:
    sb = _FakeSupabase(
        properties=[_prop('https://portal.com/live')],
        cleanup_schedule=[{'id': cleaner.SCHEDULE_ID, 'enabled': True, 'interval_days': 7,
                           'last_run_at': None}],
    )

    await cleaner.scheduler_tick(sb, checker=_alive)
    second = await cleaner.scheduler_tick(sb, checker=_alive)

    assert second['ran'] is False
    assert sb.store('cleanup_schedule')[0]['last_run_at'] is not None


async def test_a_scheduled_run_is_labelled_as_such_in_the_history() -> None:
    sb = _FakeSupabase(
        properties=[_prop('https://portal.com/live')],
        cleanup_schedule=[{'id': cleaner.SCHEDULE_ID, 'enabled': True, 'interval_days': 7,
                           'last_run_at': None}],
    )

    await cleaner.scheduler_tick(sb, checker=_alive)

    assert sb.store('cleanup_runs')[0]['origen'] == 'scheduled'


async def test_tick_without_supabase_is_a_noop() -> None:
    assert (await cleaner.scheduler_tick(None, checker=_alive))['ran'] is False


async def test_tick_swallows_a_broken_schedule_table() -> None:
    class _Raising:
        def table(self, _name: str) -> object:
            raise RuntimeError('boom')

    result = await cleaner.scheduler_tick(_Raising(), checker=_alive)
    assert result['ran'] is False
