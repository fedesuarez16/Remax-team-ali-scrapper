"""Test-first for `POST /api/v1/scraping/start` accepting an optional
`polygon` (T-2.2) — written BEFORE `StartScrapingRequest` gains the field, so
this file MUST fail (captured insert payload has no `polygon` key) until
T-2.3 lands.

Uses a lightweight fake `app.state.supabase` mirroring the fluent
`.table(name).insert(dict).execute()` chain used by `start_scraping`, capturing
whatever payload is passed to `.insert(...)`.

Design decision (T-2.2 red-assertion choice, documented per orchestrator
instruction): we assert `polygon` key is PRESENT in the insert payload with
value `None` when omitted from the request (not absent from the dict) — this
matches the design's literal implementation note ("add `'polygon': body.polygon`
to the insert dict... present even when `None`").
"""
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


class _FakeInsertQuery:
    def __init__(self, captured: list[dict], payload: dict) -> None:
        self._captured = captured
        self._payload = payload

    async def execute(self):
        self._captured.append(self._payload)
        return type('Res', (), {'data': [self._payload]})()


class _FakeTable:
    def __init__(self, captured: list[dict]) -> None:
        self._captured = captured

    def insert(self, payload: dict) -> _FakeInsertQuery:
        return _FakeInsertQuery(self._captured, payload)


class _FakeSupabase:
    def __init__(self) -> None:
        self.captured_inserts: list[dict] = []

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(self.captured_inserts)


def _make_app(fake_sb: _FakeSupabase) -> FastAPI:
    from app.api.v1 import scraping

    app = FastAPI()
    app.include_router(scraping.router)
    app.state.supabase = fake_sb
    return app


async def test_start_with_polygon_passes_it_to_insert() -> None:
    fake_sb = _FakeSupabase()
    app = _make_app(fake_sb)
    transport = ASGITransport(app=app)
    polygon = [[-34.58, -58.43], [-34.59, -58.44], [-34.60, -58.42]]
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.post('/start', json={'query': 'Propiedades en Palermo', 'polygon': polygon})

    assert resp.status_code == 200
    assert len(fake_sb.captured_inserts) == 1
    assert fake_sb.captured_inserts[0]['polygon'] == polygon


async def test_start_without_polygon_omits_or_nulls_it() -> None:
    fake_sb = _FakeSupabase()
    app = _make_app(fake_sb)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.post('/start', json={'query': 'Propiedades en Palermo'})

    assert resp.status_code == 200
    assert len(fake_sb.captured_inserts) == 1
    assert fake_sb.captured_inserts[0].get('polygon') is None


# ── localidades (T-7.1/7.2): additive field, persists alongside polygon ─────

async def test_start_with_localidades_persists_it_to_insert() -> None:
    fake_sb = _FakeSupabase()
    app = _make_app(fake_sb)
    transport = ASGITransport(app=app)
    polygon = [[-34.58, -58.43], [-34.59, -58.44], [-34.60, -58.42]]
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.post('/start', json={
            'query': 'Propiedades en Palermo', 'polygon': polygon, 'localidades': ['CABA'],
        })

    assert resp.status_code == 200
    assert len(fake_sb.captured_inserts) == 1
    assert fake_sb.captured_inserts[0]['localidades'] == ['CABA']


async def test_start_without_localidades_omits_or_nulls_it() -> None:
    fake_sb = _FakeSupabase()
    app = _make_app(fake_sb)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.post('/start', json={'query': 'Propiedades en Palermo'})

    assert resp.status_code == 200
    assert len(fake_sb.captured_inserts) == 1
    inserted = fake_sb.captured_inserts[0].get('localidades')
    assert inserted is None or inserted == []
