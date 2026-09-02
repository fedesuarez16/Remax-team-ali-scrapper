"""Test-first for the `zona` filter on `GET /api/v1/properties`.

`properties` has no locality column (see the baseline migration), so the only
signal available is the locality named inside `direccion` / `direccion_norm`.
The route must turn `zona=<slug>` into an `ilike` OR over those two columns.

The catch this file pins down: "La Plata" is BOTH a locality and the partido
that contains City Bell, Gonnet and Villa Elisa, which portals publish as
"City Bell, La Plata". Unqualified, the La Plata option would swallow its own
siblings — so it must also negate them.
"""
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


class _Res:
    def __init__(self, data, count) -> None:
        self.data = data
        self.count = count


class _Negated:
    """Mirrors postgrest-py's `query.not_` accessor."""

    def __init__(self, query: '_FakeSelectQuery') -> None:
        self._query = query

    def ilike(self, column: str, pattern: str) -> '_FakeSelectQuery':
        return self._query._record('not.ilike', column, pattern)

    def is_(self, column: str, value) -> '_FakeSelectQuery':
        return self._query._record('not.is', column, value)


class _FakeSelectQuery:
    def __init__(self, calls: list) -> None:
        self.calls = calls

    def _record(self, op: str, *args) -> '_FakeSelectQuery':
        self.calls.append((op, *args))
        return self

    @property
    def not_(self) -> _Negated:
        return _Negated(self)

    def order(self, *args, **kwargs) -> '_FakeSelectQuery':
        return self._record('order', args, kwargs)

    def eq(self, column: str, value) -> '_FakeSelectQuery':
        return self._record('eq', column, value)

    def gte(self, column: str, value) -> '_FakeSelectQuery':
        return self._record('gte', column, value)

    def lte(self, column: str, value) -> '_FakeSelectQuery':
        return self._record('lte', column, value)

    def is_(self, column: str, value) -> '_FakeSelectQuery':
        return self._record('is', column, value)

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


def _ors(fake_sb: _FakeSupabase) -> list[str]:
    return [c[1] for c in fake_sb.calls if c[0] == 'or']


def _not_ilikes(fake_sb: _FakeSupabase) -> list[tuple]:
    return [c for c in fake_sb.calls if c[0] == 'not.ilike']


async def test_no_zona_param_applies_no_zona_filter() -> None:
    fake_sb = _FakeSupabase()
    async with _client(fake_sb) as client:
        resp = await client.get('/properties')

    assert resp.status_code == 200
    assert _ors(fake_sb) == []
    assert _not_ilikes(fake_sb) == []


async def test_city_bell_matches_direccion_and_direccion_norm() -> None:
    fake_sb = _FakeSupabase()
    async with _client(fake_sb) as client:
        resp = await client.get('/properties', params={'zona': 'city_bell'})

    assert resp.status_code == 200
    clause = _ors(fake_sb)[0]
    assert 'direccion.ilike.*city bell*' in clause
    assert 'direccion_norm.ilike.*city bell*' in clause
    # The run-together spelling portals publish ("citybell") counts too.
    assert 'direccion.ilike.*citybell*' in clause


async def test_city_bell_does_not_exclude_anything() -> None:
    """"City Bell, La Plata" is a City Bell listing — no negation applies."""
    fake_sb = _FakeSupabase()
    async with _client(fake_sb) as client:
        resp = await client.get('/properties', params={'zona': 'city_bell'})

    assert resp.status_code == 200
    assert _not_ilikes(fake_sb) == []


async def test_la_plata_excludes_its_sibling_localities() -> None:
    fake_sb = _FakeSupabase()
    async with _client(fake_sb) as client:
        resp = await client.get('/properties', params={'zona': 'la_plata'})

    assert resp.status_code == 200
    assert 'direccion.ilike.*la plata*' in _ors(fake_sb)[0]
    excluded = {c[2] for c in _not_ilikes(fake_sb)}
    assert excluded == {
        '*city bell*', '*citybell*', '*gonnet*', '*villa elisa*', '*hudson*',
    }
    assert all(c[1] == 'direccion' for c in _not_ilikes(fake_sb))


async def test_every_offered_zona_is_accepted() -> None:
    for slug in ('la_plata', 'city_bell', 'gonnet', 'villa_elisa', 'hudson'):
        fake_sb = _FakeSupabase()
        async with _client(fake_sb) as client:
            resp = await client.get('/properties', params={'zona': slug})

        assert resp.status_code == 200
        assert resp.json().get('error') is None, slug
        assert _ors(fake_sb), slug


async def test_unknown_zona_reports_an_error_instead_of_silently_ignoring() -> None:
    fake_sb = _FakeSupabase()
    async with _client(fake_sb) as client:
        resp = await client.get('/properties', params={'zona': 'palermo'})

    assert resp.status_code == 200
    assert 'palermo' in resp.json()['error']
    assert fake_sb.calls == []


async def test_zona_combines_with_the_other_filters() -> None:
    fake_sb = _FakeSupabase()
    async with _client(fake_sb) as client:
        resp = await client.get(
            '/properties', params={'zona': 'gonnet', 'tipo_operacion': 'venta', 'q': 'piscina'},
        )

    assert resp.status_code == 200
    assert ('eq', 'tipo_operacion', 'venta') in fake_sb.calls
    ors = _ors(fake_sb)
    assert any('gonnet' in c for c in ors)
    assert any('piscina' in c for c in ors)
