"""A partially-written link table must not hide a job's own properties.

`GET /scraping/{job_id}/properties` reads `search_property_results` and only
falls back to `properties.scraping_job_id` when that read comes back EMPTY.
That guard covers a link table that failed outright, but not the far worse
case of one that failed halfway.

Observed live (job bb382a74): 1130 properties counted, 757 written with the
job's `scraping_job_id`, but only 30 links persisted — 25 of them agencies
from the inmobiliarias phase. The endpoint returned 30. A sibling job whose
links failed COMPLETELY (7b71731d) correctly returned all 292, so the job
that half-succeeded was punished harder than the one that failed outright.

The fix is to union both sources instead of treating the fallback as an
either/or: a link carries the `matches_criteria` verdict, and a row the job
wrote carries proof the job scraped it. Losing the link must never lose the
property.
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

    def select(self, *_a, **_kw) -> '_FakeQuery':
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

    def select(self, *a, **kw) -> _FakeQuery:
        return _FakeQuery(self._tables[self._name]).select(*a, **kw)


class _FakeSupabase:
    def __init__(self, *, links, properties) -> None:
        self._tables = {
            'scraping_jobs': lambda f: _Res(
                [{'id': f.get('id'), 'query_raw': None, 'polygon': None}]
            ),
            'search_property_results': lambda f: _Res(links),
            # Mirrors the real filter: only rows carrying this job's id.
            'properties': lambda f: _Res(
                [p for p in properties if p.get('scraping_job_id') == f.get('scraping_job_id')]
            ),
        }

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(name, self._tables)


def _app(fake_sb) -> FastAPI:
    from app.api.v1 import scraping
    app = FastAPI()
    app.include_router(scraping.router)
    app.state.supabase = fake_sb
    return app


async def _get(fake_sb, job_id='job-1') -> dict:
    transport = ASGITransport(app=_app(fake_sb))
    async with AsyncClient(transport=transport, base_url='http://t') as c:
        res = await c.get(f'/{job_id}/properties')
    assert res.status_code == 200
    return res.json()


def _prop(pid: str, job: str = 'job-1') -> dict:
    return {'id': pid, 'scraping_job_id': job, 'direccion': f'calle {pid}'}


class TestPartialLinksStillReturnEverything:
    async def test_scraped_rows_survive_a_half_written_link_table(self):
        """The bb382a74 shape: many rows written, few links."""
        props = [_prop(f'p{i}') for i in range(10)]
        links = [{'property_id': 'p0', 'matches_criteria': True, 'properties': props[0]}]

        body = await _get(_FakeSupabase(links=links, properties=props))

        assert len(body['properties']) == 10

    async def test_no_duplicates_when_a_row_is_both_linked_and_owned(self):
        props = [_prop('p1'), _prop('p2')]
        links = [{'property_id': 'p1', 'matches_criteria': True, 'properties': props[0]}]

        body = await _get(_FakeSupabase(links=links, properties=props))

        assert sorted(p['id'] for p in body['properties']) == ['p1', 'p2']

    async def test_link_verdict_wins_over_the_unlinked_default(self):
        """A link says the property did NOT match the criteria; recovering the
        row must not silently upgrade it to matching."""
        props = [_prop('p1')]
        links = [{'property_id': 'p1', 'matches_criteria': False, 'properties': props[0]}]

        body = await _get(_FakeSupabase(links=links, properties=props))

        assert [p['matches_criteria'] for p in body['properties']] == [False]


class TestExistingBehaviourIsPreserved:
    async def test_empty_link_table_still_falls_back(self):
        """The 7b71731d shape, which already worked."""
        props = [_prop(f'p{i}') for i in range(5)]

        body = await _get(_FakeSupabase(links=[], properties=props))

        assert len(body['properties']) == 5

    async def test_links_to_rows_from_earlier_jobs_are_kept(self):
        """A job re-finding a property scraped by an EARLIER job links it
        without owning it; that link is the only proof it belongs here."""
        older = {'id': 'old', 'scraping_job_id': 'job-0', 'direccion': 'calle vieja'}
        props = [_prop('p1'), older]
        links = [{'property_id': 'old', 'matches_criteria': True, 'properties': older}]

        body = await _get(_FakeSupabase(links=links, properties=props))

        assert sorted(p['id'] for p in body['properties']) == ['old', 'p1']

    async def test_matched_rows_still_lead(self):
        props = [_prop('p1'), _prop('p2')]
        links = [
            {'property_id': 'p1', 'matches_criteria': False, 'properties': props[0]},
            {'property_id': 'p2', 'matches_criteria': True, 'properties': props[1]},
        ]

        body = await _get(_FakeSupabase(links=links, properties=props))

        assert body['properties'][0]['id'] == 'p2'
