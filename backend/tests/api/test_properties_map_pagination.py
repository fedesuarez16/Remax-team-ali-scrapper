"""Test-first: `GET /api/v1/properties/map` must return EVERY geocoded row,
not just the first PostgREST page.

PostgREST enforces a server-side `max_rows` cap (1000 by default on Supabase,
see supabase/config.toml). A client-side `.limit(2000)` does NOT lift it — the
response is silently truncated at 1000, which is exactly what the /map tab was
showing while the database held ~2000 located properties.

The route therefore has to page through the table with `.range(...)` until a
short page proves the table is exhausted.
"""
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# Mirrors PostgREST's default `max_rows`; the fake truncates like the real server.
SERVER_CAP = 1000


class _Res:
    def __init__(self, data, count=None) -> None:
        self.data = data
        self.count = count


class _Query:
    """Filter ops return self; `.range()` slices the backing rows and truncates
    the slice at SERVER_CAP the way PostgREST's max_rows does."""

    def __init__(self, rows: list[dict], ranges: list[tuple[int, int]]) -> None:
        self._rows = rows
        self._ranges = ranges
        self._start = 0
        self._stop = len(rows)
        self._count_exact = False

    def select(self, columns: str, **kwargs) -> '_Query':
        self._count_exact = kwargs.get('count') == 'exact'
        return self

    @property
    def not_(self) -> '_Query':
        return self

    def is_(self, *args, **kwargs) -> '_Query':
        return self

    def range(self, start: int, end: int) -> '_Query':
        self._ranges.append((start, end))
        self._start = start
        self._stop = min(end + 1, start + SERVER_CAP)
        return self

    def limit(self, n: int) -> '_Query':
        self._stop = min(n, SERVER_CAP)
        return self

    async def execute(self) -> _Res:
        page = self._rows[self._start:self._stop]
        return _Res(page, len(self._rows) if self._count_exact else None)


class _FakeSupabase:
    def __init__(self, located: int, cartera: int) -> None:
        self.rows = {
            'properties': [
                {'id': f'p{i}', 'lat': -34.6, 'lng': -58.4} for i in range(located)
            ],
            'propiedades': [
                {'id': i, 'lat': -34.9, 'lng': -57.9} for i in range(cartera)
            ],
        }
        self.ranges: dict[str, list[tuple[int, int]]] = {
            'properties': [], 'propiedades': [],
        }

    def table(self, name: str) -> _Query:
        return _Query(self.rows[name], self.ranges[name])


def _client(fake_sb: _FakeSupabase) -> AsyncClient:
    from app.api.v1 import properties

    app = FastAPI()
    app.include_router(properties.router, prefix='/properties')
    app.state.supabase = fake_sb
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url='http://test')


async def test_map_returns_rows_beyond_the_postgrest_cap() -> None:
    """2500 located rows must all come back, despite the 1000-row server cap."""
    fake_sb = _FakeSupabase(located=2500, cartera=12)
    async with _client(fake_sb) as client:
        resp = await client.get('/properties/map')

    assert resp.status_code == 200
    body = resp.json()
    assert len(body['properties']) == 2500, 'markers truncated at the PostgREST cap'
    assert body['total'] == 2500, '`total` must count every located row, not one page'
    assert len(body['cartera']) == 12


async def test_map_pages_with_range_never_over_the_cap() -> None:
    """Each request must ask for at most SERVER_CAP rows, or PostgREST truncates."""
    fake_sb = _FakeSupabase(located=2500, cartera=0)
    async with _client(fake_sb) as client:
        await client.get('/properties/map')

    ranges = fake_sb.ranges['properties']
    assert ranges, 'route must page with .range(), not a bare .limit()'
    for start, end in ranges:
        assert end - start + 1 <= SERVER_CAP, f'page {start}-{end} exceeds the server cap'


async def test_map_stops_paging_on_a_short_page() -> None:
    """A page shorter than requested means the table is exhausted — stop there
    instead of spinning until the `limit` ceiling."""
    fake_sb = _FakeSupabase(located=1200, cartera=0)
    async with _client(fake_sb) as client:
        resp = await client.get('/properties/map')

    assert len(resp.json()['properties']) == 1200
    # 1200 rows = one full page + one short page. A third request would be waste.
    assert len(fake_sb.ranges['properties']) == 2


async def test_map_respects_an_explicit_limit() -> None:
    """`?limit=` still caps the payload, so the route can't be used to pull
    an unbounded table by accident."""
    fake_sb = _FakeSupabase(located=2500, cartera=0)
    async with _client(fake_sb) as client:
        resp = await client.get('/properties/map', params={'limit': 1500})

    assert len(resp.json()['properties']) == 1500
