"""Test-first: `GET /api/v1/properties/map` must return the attribute-filter
columns so the /map dashboard can filter markers client-side.

The map page filters by fuente/ambientes/banos/cocheras/m2_total in the browser
(no reload), so those columns have to be present in the payload — i.e. in the
`select(...)` the route issues against the `properties` table.
"""
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


class _Res:
    def __init__(self, data, count=None) -> None:
        self.data = data
        self.count = count


class _Query:
    """Records the select column list; every filter op returns self."""

    def __init__(self, selects: list[str]) -> None:
        self._selects = selects

    def select(self, columns: str, **kwargs) -> '_Query':
        self._selects.append(columns)
        return self

    @property
    def not_(self) -> '_Query':
        return self

    def is_(self, *args, **kwargs) -> '_Query':
        return self

    def limit(self, *args, **kwargs) -> '_Query':
        return self

    def range(self, *args, **kwargs) -> '_Query':
        return self

    async def execute(self) -> _Res:
        return _Res([], 0)


class _FakeSupabase:
    def __init__(self) -> None:
        self.selects: dict[str, list[str]] = {'properties': [], 'propiedades': []}

    def table(self, name: str) -> _Query:
        return _Query(self.selects[name])


def _client(fake_sb: _FakeSupabase) -> AsyncClient:
    from app.api.v1 import properties

    app = FastAPI()
    app.include_router(properties.router, prefix='/properties')
    app.state.supabase = fake_sb
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url='http://test')


async def test_map_select_includes_filter_columns() -> None:
    fake_sb = _FakeSupabase()
    async with _client(fake_sb) as client:
        resp = await client.get('/properties/map')

    assert resp.status_code == 200
    # The route funnels every exception into a 200 carrying an `error` key, so
    # the status alone proves nothing — assert the query actually went through.
    assert 'error' not in resp.json(), resp.json().get('error')
    # The first select against `properties` is the located-markers query.
    located_select = fake_sb.selects['properties'][0]
    for column in ('fuente', 'ambientes', 'banos', 'cocheras', 'm2_total', 'enviada_at'):
        assert column in located_select, f'{column} missing from /map select'
    # Existing columns must survive.
    for column in ('id', 'lat', 'lng', 'tipo_operacion', 'precio'):
        assert column in located_select
