"""Test-first for `GET /{job_id}/properties` polygon classification (T-7.5/
7.6, domain: polygon-result-classification) — written BEFORE the endpoint
classifies rows, so every count/tag assertion MUST fail until T-7.6 lands.

Mirrors the `_FakeSupabase`/`_FakeQuery`/`_FakeTable` fixtures used by
`test_scraping_job_properties.py`.
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


# Square polygon: lat in [-34.60,-34.58], lng in [-58.44,-58.42]
_POLYGON = [
    [-34.60, -58.44],
    [-34.60, -58.42],
    [-34.58, -58.42],
    [-34.58, -58.44],
]

_INSIDE = {'lat': -34.59, 'lng': -58.43}
_OUTSIDE = {'lat': -34.50, 'lng': -58.30}


def _props(n_inside: int, n_outside: int, n_ungeocoded: int) -> list[dict]:
    rows = []
    for i in range(n_inside):
        rows.append({'id': f'in-{i}', 'lat': _INSIDE['lat'], 'lng': _INSIDE['lng']})
    for i in range(n_outside):
        rows.append({'id': f'out-{i}', 'lat': _OUTSIDE['lat'], 'lng': _OUTSIDE['lng']})
    for i in range(n_ungeocoded):
        rows.append({'id': f'un-{i}', 'lat': None, 'lng': None})
    return rows


async def test_mixed_set_classified_with_correct_counts_and_tags() -> None:
    properties = _props(5, 3, 2)
    fake_sb = _FakeSupabase(
        jobs={'job-1': {'id': 'job-1', 'query_raw': None, 'polygon': _POLYGON}},
        properties=properties,
    )
    app = _make_app(fake_sb)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.get('/job-1/properties')

    assert resp.status_code == 200
    data = resp.json()
    assert len(data['properties']) == 10
    assert data['counts'] == {'inside': 5, 'outside': 3, 'ungeocoded': 2, 'total': 10}
    inside_tags = [p['in_polygon'] for p in data['properties'] if p['id'].startswith('in-')]
    outside_tags = [p['in_polygon'] for p in data['properties'] if p['id'].startswith('out-')]
    ungeo_tags = [p['in_polygon'] for p in data['properties'] if p['id'].startswith('un-')]
    assert all(t is True for t in inside_tags)
    assert all(t is False for t in outside_tags)
    assert all(t is None for t in ungeo_tags)


async def test_no_polygon_job_skips_classification_backward_compat() -> None:
    fake_sb = _FakeSupabase(
        jobs={'job-1': {'id': 'job-1', 'query_raw': None, 'polygon': None}},
        properties=[{'id': 'p1', 'lat': -34.59, 'lng': -58.43}],
    )
    app = _make_app(fake_sb)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.get('/job-1/properties')

    assert resp.status_code == 200
    data = resp.json()
    assert data['polygon'] is None
    assert data['counts'] is None
    assert 'in_polygon' not in data['properties'][0] or data['properties'][0]['in_polygon'] is None


async def test_empty_polygon_falls_back_without_raising() -> None:
    fake_sb = _FakeSupabase(
        jobs={'job-1': {'id': 'job-1', 'query_raw': None, 'polygon': []}},
        properties=[{'id': 'p1', 'lat': -34.59, 'lng': -58.43}],
    )
    app = _make_app(fake_sb)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.get('/job-1/properties')

    assert resp.status_code == 200
    data = resp.json()
    assert data['counts'] is None


async def test_malformed_polygon_lt3_points_falls_back_without_raising() -> None:
    fake_sb = _FakeSupabase(
        jobs={'job-1': {'id': 'job-1', 'query_raw': None, 'polygon': [[-34.6, -58.4], [-34.5, -58.3]]}},
        properties=[{'id': 'p1', 'lat': -34.59, 'lng': -58.43}],
    )
    app = _make_app(fake_sb)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.get('/job-1/properties')

    assert resp.status_code == 200
    data = resp.json()
    assert data['counts'] is None
