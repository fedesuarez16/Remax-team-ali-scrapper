"""Test-first for `/api/v1/cleanup` — the BOT LIMPIADOR's control surface.

- `POST /run` fires a manual pass in the background (same fire-and-forget shape
  as `/properties/geocode/backfill`) and answers immediately.
- `GET /status` reports the live counters plus the configured cadence.
- `GET/PUT /schedule` reads and sets "cada X días".
- `GET /runs` is the audit trail — what each pass deleted and why.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.services import cleaner

from tests.services.test_cleaner_run import _FakeSupabase, _prop


def _make_app(fake_sb: Any) -> FastAPI:
    from app.api.v1 import cleanup

    app = FastAPI()
    app.include_router(cleanup.router, prefix='/cleanup')
    app.state.supabase = fake_sb
    return app


def _client(fake_sb: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=_make_app(fake_sb)), base_url='http://test')


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    cleaner.reset_state()


# ── POST /run ────────────────────────────────────────────────────────────────


async def test_run_returns_immediately_and_schedules_the_pass(monkeypatch) -> None:
    from app.api.v1 import cleanup

    seen: dict[str, Any] = {}

    async def fake_run(sb: Any, **kwargs: Any) -> dict:
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(cleanup, '_run_cleanup', fake_run)

    async with _client(_FakeSupabase()) as client:
        resp = await client.post('/cleanup/run', json={'limit': 25, 'dry_run': True})
    await asyncio.sleep(0)

    assert resp.status_code == 200
    assert resp.json()['started'] is True
    assert seen['limit'] == 25
    assert seen['dry_run'] is True
    assert seen['origen'] == 'manual'


async def test_run_defaults_to_a_real_pass(monkeypatch) -> None:
    from app.api.v1 import cleanup

    seen: dict[str, Any] = {}

    async def fake_run(sb: Any, **kwargs: Any) -> dict:
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(cleanup, '_run_cleanup', fake_run)

    async with _client(_FakeSupabase()) as client:
        await client.post('/cleanup/run', json={})
    await asyncio.sleep(0)

    assert seen['dry_run'] is False


async def test_run_without_supabase_reports_the_error() -> None:
    async with _client(None) as client:
        resp = await client.post('/cleanup/run', json={})

    assert resp.status_code == 200
    body = resp.json()
    assert body['started'] is False
    assert 'error' in body


# ── GET /status ──────────────────────────────────────────────────────────────


async def test_status_reports_state_and_schedule() -> None:
    sb = _FakeSupabase(cleanup_schedule=[{
        'id': cleaner.SCHEDULE_ID, 'enabled': True, 'interval_days': 30, 'last_run_at': None,
    }])
    async with _client(sb) as client:
        resp = await client.get('/cleanup/status')

    body = resp.json()
    assert body['state']['running'] is False
    assert body['schedule']['interval_days'] == 30


async def test_status_without_supabase_still_answers() -> None:
    async with _client(None) as client:
        resp = await client.get('/cleanup/status')

    assert resp.status_code == 200
    assert resp.json()['schedule']['enabled'] is False


# ── GET/PUT /schedule ────────────────────────────────────────────────────────


async def test_put_schedule_persists_the_cadence() -> None:
    sb = _FakeSupabase()
    async with _client(sb) as client:
        resp = await client.put('/cleanup/schedule', json={'enabled': True, 'interval_days': 7})

    assert resp.status_code == 200
    assert resp.json()['schedule']['interval_days'] == 7
    assert (await cleaner.read_schedule(sb))['enabled'] is True


async def test_put_schedule_rejects_an_invalid_interval() -> None:
    sb = _FakeSupabase()
    async with _client(sb) as client:
        resp = await client.put('/cleanup/schedule', json={'enabled': True, 'interval_days': 0})

    assert resp.status_code == 200
    body = resp.json()
    assert body['schedule'] is None
    assert 'error' in body


async def test_get_schedule_returns_the_stored_cadence() -> None:
    sb = _FakeSupabase(cleanup_schedule=[{
        'id': cleaner.SCHEDULE_ID, 'enabled': True, 'interval_days': 14, 'last_run_at': None,
    }])
    async with _client(sb) as client:
        resp = await client.get('/cleanup/schedule')

    assert resp.json()['schedule']['interval_days'] == 14


async def test_put_schedule_without_supabase_reports_the_error() -> None:
    async with _client(None) as client:
        resp = await client.put('/cleanup/schedule', json={'enabled': True, 'interval_days': 7})

    assert resp.json()['schedule'] is None
    assert 'error' in resp.json()


# ── POST /check-links ────────────────────────────────────────────────────────


async def test_check_links_returns_the_two_lists(monkeypatch) -> None:
    from app.services import cleaner as cleaner_module

    async def fake_check(url: str, *, client: Any):
        verdict = 'dead' if 'muerta' in url else 'alive'
        return cleaner_module.CheckResult(verdict, f'fake:{verdict}')

    monkeypatch.setattr(cleaner_module, 'check_url', fake_check)

    async with _client(_FakeSupabase()) as client:
        resp = await client.post('/cleanup/check-links', json={
            'urls': ['https://portal.com/viva', 'https://portal.com/muerta'],
        })

    body = resp.json()
    assert [i['url'] for i in body['activos']] == ['https://portal.com/viva']
    assert [i['url'] for i in body['rotos']] == ['https://portal.com/muerta']
    assert body['total'] == 2


async def test_check_links_accepts_a_pasted_block_of_text(monkeypatch) -> None:
    from app.services import cleaner as cleaner_module

    async def fake_check(url: str, *, client: Any):
        return cleaner_module.CheckResult('alive', 'ok')

    monkeypatch.setattr(cleaner_module, 'check_url', fake_check)

    async with _client(_FakeSupabase()) as client:
        resp = await client.post('/cleanup/check-links', json={
            'urls': 'https://portal.com/a\nhttps://portal.com/b\n\nhttps://portal.com/c',
        })

    assert resp.json()['total'] == 3


async def test_check_links_works_without_supabase(monkeypatch) -> None:
    """Verificar una lista pegada no toca la base — no puede depender de ella."""
    from app.services import cleaner as cleaner_module

    async def fake_check(url: str, *, client: Any):
        return cleaner_module.CheckResult('alive', 'ok')

    monkeypatch.setattr(cleaner_module, 'check_url', fake_check)

    async with _client(None) as client:
        resp = await client.post('/cleanup/check-links', json={'urls': ['https://portal.com/a']})

    assert resp.status_code == 200
    assert len(resp.json()['activos']) == 1


async def test_check_links_rejects_an_empty_payload() -> None:
    async with _client(_FakeSupabase()) as client:
        resp = await client.post('/cleanup/check-links', json={'urls': []})

    assert resp.status_code == 200
    assert 'error' in resp.json()


async def test_check_links_rejects_too_many_links() -> None:
    from app.services.cleaner import MAX_LINKS

    async with _client(_FakeSupabase()) as client:
        resp = await client.post('/cleanup/check-links', json={
            'urls': [f'https://portal.com/{i}' for i in range(MAX_LINKS + 1)],
        })

    assert 'error' in resp.json()
    assert str(MAX_LINKS) in resp.json()['error']


# ── GET /runs ────────────────────────────────────────────────────────────────


async def test_runs_lists_the_audit_trail_most_recent_first() -> None:
    sb = _FakeSupabase(cleanup_runs=[
        {'id': 'r1', 'origen': 'manual', 'eliminadas': [], 'started_at': '2026-07-01T00:00:00+00:00'},
        {'id': 'r2', 'origen': 'scheduled', 'eliminadas': [], 'started_at': '2026-07-20T00:00:00+00:00'},
    ])
    async with _client(sb) as client:
        resp = await client.get('/cleanup/runs')

    assert [r['id'] for r in resp.json()['runs']] == ['r2', 'r1']


async def test_runs_without_supabase_returns_an_empty_list() -> None:
    async with _client(None) as client:
        resp = await client.get('/cleanup/runs')

    assert resp.json()['runs'] == []


async def test_runs_failure_returns_error_without_raising() -> None:
    class _Raising:
        def table(self, _name: str) -> object:
            raise RuntimeError('boom')

    async with _client(_Raising()) as client:
        resp = await client.get('/cleanup/runs')

    assert resp.status_code == 200
    assert resp.json()['runs'] == []
    assert 'error' in resp.json()


# ── POST /delete-links ───────────────────────────────────────────────────────


async def test_delete_links_removes_the_properties_behind_the_dead_ones(monkeypatch) -> None:
    """El botón "borrar rotos": la lista de rotos deja de ser sólo un informe."""
    from app.services import cleaner as cleaner_module

    async def fake_check(url: str, *, client: Any):
        verdict = 'dead' if 'muerta' in url else 'alive'
        return cleaner_module.CheckResult(verdict, f'fake:{verdict}')

    monkeypatch.setattr(cleaner_module, 'check_url', fake_check)

    dead = _prop('https://portal.com/muerta')
    alive = _prop('https://portal.com/viva')
    sb = _FakeSupabase(properties=[dead, alive])

    async with _client(sb) as client:
        resp = await client.post('/cleanup/delete-links', json={
            'urls': ['https://portal.com/muerta'],
        })

    body = resp.json()
    assert resp.status_code == 200
    assert [i['url_origen'] for i in body['eliminadas']] == ['https://portal.com/muerta']
    assert [r['id'] for r in sb.store('properties')] == [alive['id']]


async def test_delete_links_never_deletes_what_is_not_provably_dead(monkeypatch) -> None:
    """El front manda lo que vio hace un rato: el backend NO le cree de una."""
    from app.services import cleaner as cleaner_module

    async def fake_check(url: str, *, client: Any):
        return cleaner_module.CheckResult('unknown', 'el portal nos bloqueó')

    monkeypatch.setattr(cleaner_module, 'check_url', fake_check)

    sb = _FakeSupabase(properties=[_prop('https://portal.com/dudosa')])

    async with _client(sb) as client:
        resp = await client.post('/cleanup/delete-links', json={
            'urls': ['https://portal.com/dudosa'],
        })

    body = resp.json()
    assert body['eliminadas'] == []
    assert [i['url'] for i in body['conservadas']] == ['https://portal.com/dudosa']
    assert len(sb.store('properties')) == 1


async def test_delete_links_accepts_a_pasted_block_of_text(monkeypatch) -> None:
    from app.services import cleaner as cleaner_module

    async def fake_check(url: str, *, client: Any):
        return cleaner_module.CheckResult('dead', 'fake:dead')

    monkeypatch.setattr(cleaner_module, 'check_url', fake_check)

    sb = _FakeSupabase(properties=[
        _prop('https://portal.com/a'), _prop('https://portal.com/b'),
    ])

    async with _client(sb) as client:
        resp = await client.post('/cleanup/delete-links', json={
            'urls': 'https://portal.com/a\nhttps://portal.com/b',
        })

    assert len(resp.json()['eliminadas']) == 2
    assert sb.store('properties') == []


async def test_delete_links_rejects_an_empty_payload() -> None:
    async with _client(_FakeSupabase()) as client:
        resp = await client.post('/cleanup/delete-links', json={'urls': []})

    assert resp.status_code == 200
    assert 'error' in resp.json()


async def test_delete_links_needs_supabase() -> None:
    async with _client(None) as client:
        resp = await client.post('/cleanup/delete-links', json={
            'urls': ['https://portal.com/a'],
        })

    assert resp.json()['eliminadas'] == []
    assert 'error' in resp.json()
