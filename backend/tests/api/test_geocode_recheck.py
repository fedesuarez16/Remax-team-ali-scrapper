import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1 import properties


@pytest.mark.parametrize('query', [
    'recheck=true', 'recheck=true&job_id=invalid', 'limit=0', 'limit=1001',
])
async def test_invalid_recheck_does_not_schedule_writes(monkeypatch, query):
    backfill = AsyncMock()
    monkeypatch.setattr(properties, '_run_backfill', backfill)
    app = FastAPI()
    app.state.supabase = object()
    app.include_router(properties.router, prefix='/properties')
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(f'/properties/geocode/backfill?{query}')
    assert response.status_code == 422
    backfill.assert_not_called()


async def test_job_preview_is_scheduled_with_dry_run(monkeypatch):
    backfill = AsyncMock()
    monkeypatch.setattr(properties, '_run_backfill', backfill)
    app = FastAPI()
    sb = object()
    app.state.supabase = sb
    app.include_router(properties.router, prefix='/properties')
    job = '00000000-0000-0000-0000-000000000001'
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(
            f'/properties/geocode/backfill?job_id={job}&recheck=true&dry_run=true&limit=200',
        )
        await asyncio.sleep(0)
    assert response.status_code == 200
    backfill.assert_awaited_once_with(
        sb, limit=200, force=False, job_id=job, recheck=True, dry_run=True,
    )
