"""Test-first for Zonaprop-style filters on `GET /api/v1/properties`.

The properties base needs the essential Zonaprop filters: tipo_propiedad,
precio min/max (+ moneda), ambientes/banos/cocheras minimums and m2_total
range. The route must translate each query param into the matching
PostgREST operator (`eq` / `gte` / `lte`) and skip absent params entirely.

Fake `app.state.supabase` records the fluent filter chain so tests can
assert exactly which operators the route applied.
"""
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


class _Res:
    def __init__(self, data, count) -> None:
        self.data = data
        self.count = count


class _FakeSelectQuery:
    def __init__(self, calls: list) -> None:
        self.calls = calls

    def _record(self, op: str, *args) -> '_FakeSelectQuery':
        self.calls.append((op, *args))
        return self

    def order(self, *args, **kwargs) -> '_FakeSelectQuery':
        return self._record('order', args, kwargs)

    def eq(self, column: str, value) -> '_FakeSelectQuery':
        return self._record('eq', column, value)

    def gte(self, column: str, value) -> '_FakeSelectQuery':
        return self._record('gte', column, value)

    def lte(self, column: str, value) -> '_FakeSelectQuery':
        return self._record('lte', column, value)

    def or_(self, clause: str) -> '_FakeSelectQuery':
        return self._record('or', clause)

    def range(self, start: int, end: int) -> '_FakeSelectQuery':
        return self._record('range', start, end)

    async def execute(self) -> _Res:
        return _Res([], 0)


class _FakeTable:
    def __init__(self, calls: list) -> None:
        self._calls = calls

    def select(self, *args, **kwargs) -> _FakeSelectQuery:
        return _FakeSelectQuery(self._calls)


class _FakeSupabase:
    def __init__(self) -> None:
        self.calls: list = []

    def table(self, name: str) -> _FakeTable:
        assert name == 'properties'
        return _FakeTable(self.calls)


def _client(fake_sb: _FakeSupabase) -> AsyncClient:
    from app.api.v1 import properties

    app = FastAPI()
    app.include_router(properties.router, prefix='/properties')
    app.state.supabase = fake_sb
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url='http://test')


def _filters(fake_sb: _FakeSupabase) -> set:
    """Filter ops only — drop the always-present order/range plumbing."""
    return {c for c in fake_sb.calls if c[0] in ('eq', 'gte', 'lte', 'or')}


async def test_no_params_applies_no_filters() -> None:
    fake_sb = _FakeSupabase()
    async with _client(fake_sb) as client:
        resp = await client.get('/properties')

    assert resp.status_code == 200
    assert _filters(fake_sb) == set()


async def test_tipo_propiedad_and_moneda_filter_as_eq() -> None:
    fake_sb = _FakeSupabase()
    async with _client(fake_sb) as client:
        resp = await client.get('/properties', params={'tipo_propiedad': 'departamento', 'moneda': 'USD'})

    assert resp.status_code == 200
    assert ('eq', 'tipo_propiedad', 'departamento') in _filters(fake_sb)
    assert ('eq', 'moneda', 'USD') in _filters(fake_sb)


async def test_precio_range_filters_gte_lte() -> None:
    fake_sb = _FakeSupabase()
    async with _client(fake_sb) as client:
        resp = await client.get('/properties', params={'precio_min': 100000, 'precio_max': 250000})

    assert resp.status_code == 200
    assert ('gte', 'precio', 100000) in _filters(fake_sb)
    assert ('lte', 'precio', 250000) in _filters(fake_sb)


async def test_ambientes_banos_cocheras_minimums() -> None:
    fake_sb = _FakeSupabase()
    async with _client(fake_sb) as client:
        resp = await client.get(
            '/properties', params={'ambientes_min': 3, 'banos_min': 2, 'cocheras_min': 1}
        )

    assert resp.status_code == 200
    assert ('gte', 'ambientes', 3) in _filters(fake_sb)
    assert ('gte', 'banos', 2) in _filters(fake_sb)
    assert ('gte', 'cocheras', 1) in _filters(fake_sb)


async def test_m2_range_filters_m2_total() -> None:
    fake_sb = _FakeSupabase()
    async with _client(fake_sb) as client:
        resp = await client.get('/properties', params={'m2_min': 50, 'm2_max': 120})

    assert resp.status_code == 200
    assert ('gte', 'm2_total', 50) in _filters(fake_sb)
    assert ('lte', 'm2_total', 120) in _filters(fake_sb)


async def test_existing_filters_keep_working_combined() -> None:
    fake_sb = _FakeSupabase()
    async with _client(fake_sb) as client:
        resp = await client.get(
            '/properties',
            params={
                'fuente': 'zonaprop',
                'tipo_operacion': 'venta',
                'q': 'palermo',
                'precio_max': 300000,
            },
        )

    assert resp.status_code == 200
    filters = _filters(fake_sb)
    assert ('eq', 'fuente', 'zonaprop') in filters
    assert ('eq', 'tipo_operacion', 'venta') in filters
    assert ('lte', 'precio', 300000) in filters
    assert any(c[0] == 'or' and 'palermo' in c[1] for c in filters)
