"""Test-first for `stream_scraping` reading the persisted job row and
injecting `localidades`+`polygon` into the graph's initial `inputs` dict
(T-7.3/7.4) — written BEFORE `stream_scraping` reads the job row, so the
localidades/polygon assertions MUST fail until T-7.4 lands.

Mocks `app.api.v1.scraping.build_graph` with a fake graph whose
`astream_events` captures the `inputs` dict it was called with, then yields
nothing (closes the SSE stream immediately via the `done` sentinel put by
`_run_graph_into_queue`).
"""
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


class _Res:
    def __init__(self, data) -> None:
        self.data = data


class _FakeQuery:
    def __init__(self, resolver) -> None:
        self._resolver = resolver
        self._filters: dict = {}

    def select(self, *_args, **_kwargs) -> '_FakeQuery':
        return self

    def eq(self, key: str, value) -> '_FakeQuery':
        self._filters[key] = value
        return self

    async def execute(self) -> _Res:
        return self._resolver(self._filters)


class _FakeTable:
    def __init__(self, name: str, tables: dict) -> None:
        self._name = name
        self._tables = tables

    def select(self, *args, **kwargs) -> _FakeQuery:
        return _FakeQuery(self._tables[self._name]).select(*args, **kwargs)


class _FakeSupabase:
    def __init__(self, jobs: dict) -> None:
        def jobs_resolver(filters):
            job_id = filters.get('id')
            row = jobs.get(job_id)
            return _Res([row] if row else [])

        self._tables = {'scraping_jobs': jobs_resolver}

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(name, self._tables)


class _FakeGraph:
    def __init__(self, captured: dict) -> None:
        self._captured = captured

    async def astream_events(self, inputs, config, version='v2'):
        self._captured['inputs'] = inputs
        return
        yield  # pragma: no cover - makes this an async generator


def _make_app(fake_sb) -> FastAPI:
    from app.api.v1 import scraping

    app = FastAPI()
    app.include_router(scraping.router)
    app.state.supabase = fake_sb
    app.state.checkpointer = None
    return app


async def test_stream_injects_localidades_and_polygon_from_job_row(monkeypatch) -> None:
    from app.api.v1 import scraping

    polygon = [[-34.58, -58.43], [-34.59, -58.44], [-34.60, -58.42]]
    fake_sb = _FakeSupabase(jobs={
        'job-1': {'id': 'job-1', 'localidades': ['CABA'], 'polygon': polygon},
    })
    captured: dict = {}
    monkeypatch.setattr(scraping, 'build_graph', lambda checkpointer=None: _FakeGraph(captured))
    app = _make_app(fake_sb)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        async with client.stream('GET', '/job-1/stream', params={'query': 'Propiedades en Palermo'}) as resp:
            async for _ in resp.aiter_lines():
                pass

    assert captured['inputs'].get('localidades') == ['CABA']
    assert captured['inputs'].get('polygon') == polygon
    assert captured['inputs'].get('query') == 'Propiedades en Palermo'
    assert captured['inputs'].get('job_id') == 'job-1'


async def test_stream_chat_path_job_row_missing_leaves_inputs_unchanged(monkeypatch) -> None:
    from app.api.v1 import scraping

    fake_sb = _FakeSupabase(jobs={})  # chat-originated job, no row (or sb=None in prod)
    captured: dict = {}
    monkeypatch.setattr(scraping, 'build_graph', lambda checkpointer=None: _FakeGraph(captured))
    app = _make_app(fake_sb)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        async with client.stream('GET', '/job-1/stream', params={'query': 'Propiedades en Palermo'}) as resp:
            async for _ in resp.aiter_lines():
                pass

    assert captured['inputs'] == {'query': 'Propiedades en Palermo', 'job_id': 'job-1'}


async def test_stream_no_supabase_leaves_inputs_unchanged(monkeypatch) -> None:
    from app.api.v1 import scraping

    captured: dict = {}
    monkeypatch.setattr(scraping, 'build_graph', lambda checkpointer=None: _FakeGraph(captured))

    app = FastAPI()
    app.include_router(scraping.router)
    app.state.supabase = None
    app.state.checkpointer = None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        async with client.stream('GET', '/job-1/stream', params={'query': 'Propiedades en Palermo'}) as resp:
            async for _ in resp.aiter_lines():
                pass

    assert captured['inputs'] == {'query': 'Propiedades en Palermo', 'job_id': 'job-1'}
