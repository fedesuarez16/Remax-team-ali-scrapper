"""The remaining unbounded `.in_()` call sites must chunk too.

Same ceiling as the scraping-graph ones: PostgREST puts `.in_()` values in the
query string, and past ~39 KB of encoded parameter Supabase answers `400 JSON
could not be generated`. These four take UUIDs (~39 B encoded each), so they
break somewhere past ~1000 ids — reachable the moment a user selects a big
result set and hits "Eliminar" or "marcar enviadas".

Writes carry an extra obligation. Chunking turns one statement into several,
so a failure partway through leaves earlier chunks APPLIED. Returning the old
`{'deleted': 0}` would then be a lie about data that is already gone. Each
write reports what actually landed, alongside the error.
"""
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.database import IN_FILTER_MAX_BYTES

# Enough UUID-shaped ids to need several chunks.
IDS = [f'{i:08d}-0000-4000-8000-{i:012d}' for i in range(1500)]


class _Res:
    def __init__(self, data: Any, count: int | None = None) -> None:
        self.data = data
        self.count = count


class _RecordingQuery:
    """Records every `in_` batch; fails on the Nth call when asked to."""

    def __init__(self, sb: 'RecordingSupabase') -> None:
        self._sb = sb
        self._values: list[str] = []

    def delete(self) -> '_RecordingQuery':
        return self

    def update(self, _payload: dict) -> '_RecordingQuery':
        return self

    def select(self, *_a: Any, **_kw: Any) -> '_RecordingQuery':
        return self

    def order(self, *_a: Any, **_kw: Any) -> '_RecordingQuery':
        return self

    def in_(self, _column: str, values: list[str]) -> '_RecordingQuery':
        self._values = list(values)
        return self

    async def execute(self) -> _Res:
        self._sb.batches.append(self._values)
        if self._sb.fail_on_batch == len(self._sb.batches):
            raise RuntimeError('supabase caído a mitad')
        return _Res([{'id': v} for v in self._values])


class RecordingSupabase:
    def __init__(self, fail_on_batch: int | None = None) -> None:
        self.batches: list[list[str]] = []
        self.fail_on_batch = fail_on_batch

    def table(self, _name: str) -> _RecordingQuery:
        return _RecordingQuery(self)


def _client(fake_sb: Any) -> AsyncClient:
    from app.api.v1 import properties
    app = FastAPI()
    app.include_router(properties.router, prefix='/properties')
    app.state.supabase = fake_sb
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


def _encoded(values: list[str]) -> int:
    from urllib.parse import quote
    return len(quote(','.join(f'"{v}"' for v in values)))


class TestBulkDeleteChunks:
    async def test_splits_into_batches_under_the_limit(self):
        sb = RecordingSupabase()
        async with _client(sb) as c:
            res = await c.post('/properties/bulk-delete', json={'ids': IDS})

        assert res.status_code == 200
        assert len(sb.batches) > 1
        assert all(_encoded(b) <= IN_FILTER_MAX_BYTES for b in sb.batches)

    async def test_every_id_is_deleted_exactly_once(self):
        sb = RecordingSupabase()
        async with _client(sb) as c:
            res = await c.post('/properties/bulk-delete', json={'ids': IDS})

        assert [v for b in sb.batches for v in b] == IDS
        assert res.json()['deleted'] == len(IDS)

    async def test_partial_failure_reports_what_was_actually_deleted(self):
        """The rows in batch 1 are gone; saying `deleted: 0` would be a lie."""
        sb = RecordingSupabase(fail_on_batch=2)
        async with _client(sb) as c:
            body = (await c.post('/properties/bulk-delete', json={'ids': IDS})).json()

        assert body['error']
        assert body['deleted'] == len(sb.batches[0])
        assert body['ids'] == sb.batches[0]

    async def test_small_request_still_takes_one_round_trip(self):
        sb = RecordingSupabase()
        async with _client(sb) as c:
            await c.post('/properties/bulk-delete', json={'ids': IDS[:40]})

        assert len(sb.batches) == 1


class TestMarkSentChunks:
    async def test_splits_into_batches_under_the_limit(self):
        sb = RecordingSupabase()
        async with _client(sb) as c:
            res = await c.post('/properties/mark-sent', json={'ids': IDS})

        assert res.status_code == 200
        assert len(sb.batches) > 1
        assert all(_encoded(b) <= IN_FILTER_MAX_BYTES for b in sb.batches)
        assert res.json()['updated'] == len(IDS)

    async def test_partial_failure_reports_what_was_actually_updated(self):
        sb = RecordingSupabase(fail_on_batch=2)
        async with _client(sb) as c:
            body = (await c.post('/properties/mark-sent', json={'ids': IDS})).json()

        assert body['error']
        assert body['updated'] == len(sb.batches[0])


class TestSearchHistoryChunks:
    async def test_cost_lookup_chunks_and_merges(self):
        """`_attach_apify_costs` reads job costs for the history list.

        The listing endpoint caps itself at CAP=20, so this only overflows if a
        caller passes a longer history — but the helper is public to the module
        and must not depend on its caller staying small.
        """
        from app.api.v1.search_history import _attach_apify_costs

        sb = RecordingSupabase()
        history = [{'job_id': i} for i in IDS]
        out = await _attach_apify_costs(sb, history)

        assert len(sb.batches) > 1
        assert all(_encoded(b) <= IN_FILTER_MAX_BYTES for b in sb.batches)
        # Every entry got its row back — nothing dropped by the split.
        assert all('apify_cost_usd' in e for e in out)

    async def test_trim_cap_chunks_its_delete(self):
        from app.api.v1 import search_history

        class _TrimSupabase(RecordingSupabase):
            def table(self, _name: str) -> Any:
                q = _RecordingQuery(self)
                # `_trim_cap` first SELECTs every row, then deletes the tail.
                if not self.batches and not getattr(self, '_selected', False):
                    self._selected = True

                    class _Sel:
                        def select(self_, *a, **kw): return self_
                        def order(self_, *a, **kw): return self_
                        async def execute(self_): return _Res([{'id': i} for i in IDS])
                    return _Sel()
                return q

        sb = _TrimSupabase()
        await search_history._trim_cap(sb, cap=10)

        assert len(sb.batches) > 1
        assert all(_encoded(b) <= IN_FILTER_MAX_BYTES for b in sb.batches)
        assert [v for b in sb.batches for v in b] == IDS[10:]
