"""Test-first for `GET /api/v1/scraping/{job_id}/properties` returning the
job's stored `polygon` (T-2.4) — written BEFORE the job select/response gains
the field, so the polygon-related assertions MUST fail until T-2.5 lands.

Fake `app.state.supabase` mirrors the fluent
`.table(name).select(...).eq(...).execute()` chain, plus the
`search_property_results` join and `properties` fallback paths used by
`get_job_properties`.
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
    def __init__(self, jobs: dict, search_results=None, properties=None) -> None:
        def jobs_resolver(filters):
            job_id = filters.get('id')
            row = jobs.get(job_id)
            return _Res([row] if row else [])

        def search_results_resolver(filters):
            return _Res(search_results if search_results is not None else [])

        def properties_resolver(filters):
            return _Res(properties if properties is not None else [])

        self._tables = {
            'scraping_jobs': jobs_resolver,
            'search_property_results': search_results_resolver,
            'properties': properties_resolver,
        }

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(name, self._tables)


def _make_app(fake_sb: _FakeSupabase) -> FastAPI:
    from app.api.v1 import scraping

    app = FastAPI()
    app.include_router(scraping.router)
    app.state.supabase = fake_sb
    return app


async def test_job_properties_returns_stored_polygon() -> None:
    polygon = [[-34.58, -58.43], [-34.59, -58.44]]
    fake_sb = _FakeSupabase(
        jobs={'job-1': {'id': 'job-1', 'query_raw': 'Propiedades en Palermo', 'polygon': polygon}},
        properties=[{'id': 'p1', 'scraping_job_id': 'job-1'}],
    )
    app = _make_app(fake_sb)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.get('/job-1/properties')

    assert resp.status_code == 200
    data = resp.json()
    assert data['polygon'] == polygon


async def test_job_properties_polygon_null_when_absent() -> None:
    fake_sb = _FakeSupabase(
        jobs={'job-1': {'id': 'job-1', 'query_raw': 'Propiedades en Palermo', 'polygon': None}},
        properties=[],
    )
    app = _make_app(fake_sb)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.get('/job-1/properties')

    assert resp.status_code == 200
    data = resp.json()
    assert data['polygon'] is None


async def test_job_properties_orders_matched_first_and_exposes_flag() -> None:
    """All linked properties come back; the ones matching the search criteria
    (matches_criteria) are ordered first and every row exposes the flag."""
    search_results = [
        {'property_id': 'p1', 'matches_criteria': False,
         'properties': {'id': 'p1', 'direccion': 'Calle Cara 1', 'precio': 900000}},
        {'property_id': 'p2', 'matches_criteria': True,
         'properties': {'id': 'p2', 'direccion': 'Calle Justa 2', 'precio': 200000}},
    ]
    fake_sb = _FakeSupabase(
        jobs={'job-1': {'id': 'job-1', 'query_raw': None, 'polygon': None}},
        search_results=search_results,
    )
    app = _make_app(fake_sb)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.get('/job-1/properties')

    assert resp.status_code == 200
    props = resp.json()['properties']
    assert [p['id'] for p in props] == ['p2', 'p1']
    assert props[0]['matches_criteria'] is True
    assert props[1]['matches_criteria'] is False


async def test_job_properties_404_on_missing_job() -> None:
    fake_sb = _FakeSupabase(jobs={})
    app = _make_app(fake_sb)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.get('/missing-job/properties')

    assert resp.status_code == 404
