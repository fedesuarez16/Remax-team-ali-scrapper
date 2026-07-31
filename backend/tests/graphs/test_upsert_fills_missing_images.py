"""Re-scrapes must backfill galleries onto rows that have none.

`_upsert_properties` writes with `ignore_duplicates=True`
(`INSERT ... ON CONFLICT DO NOTHING` on the `(direccion, precio,
tipo_operacion)` dedup index). That is deliberate — a blind
`DO UPDATE` would clobber the ficha editor's manual curation, which
`PATCH /properties/{id}` explicitly supports for `imagenes` ("drop junk images
(agency logos)", `app/api/v1/properties.py:227`).

The cost of that choice was a permanent hole: every property first scraped
before galleries were extracted sits in the table with `imagenes = '{}'`, and
no later run could ever fill it — the conflicting insert just does nothing, so
re-searching the same zona re-scrapes correct photos and throws them away.

So the write is split: insert-ignore keeps ownership of existing rows, then a
targeted pass fills ONLY rows whose gallery is still empty. Curated rows have a
non-empty gallery and are therefore never touched.
"""
from typing import Any

import pytest

from app.graphs.extraction import nodes
from app.graphs.extraction.nodes import _fill_missing_images, _upsert_properties
from app.models.property import NormalizedProperty


def _prop(
    direccion: str,
    precio: float | None,
    imagenes: list[str] | None = None,
) -> NormalizedProperty:
    return NormalizedProperty(
        direccion=direccion, precio=precio, tipo_operacion='venta',
        fuente='remax', imagenes=imagenes or [],
    )


class _Res:
    def __init__(self, data: Any) -> None:
        self.data = data


class _FakePropertiesTable:
    def __init__(self, rows: list[dict], sink: 'dict[str, list[str]]') -> None:
        self._rows = rows
        self._sink = sink
        self._patch: dict[str, Any] | None = None
        self._target_id: str | None = None
        self.select_calls = 0

    def upsert(self, _rows: list[dict], **_k: Any) -> '_FakePropertiesTable':
        return self

    def select(self, *_a: Any, **_k: Any) -> '_FakePropertiesTable':
        self.select_calls += 1
        return self

    def in_(self, _key: str, _values: list) -> '_FakePropertiesTable':
        return self

    def update(self, patch: dict[str, Any]) -> '_FakePropertiesTable':
        self._patch = patch
        return self

    def eq(self, key: str, value: str) -> '_FakePropertiesTable':
        assert key == 'id'
        self._target_id = value
        return self

    async def execute(self) -> _Res:
        if self._patch is not None and self._target_id is not None:
            self._sink[self._target_id] = self._patch['imagenes']
            self._patch = None
            self._target_id = None
            return _Res([])
        return _Res(list(self._rows))


class _FakeSupabase:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.updated: dict[str, list[str]] = {}
        self._table = _FakePropertiesTable(rows, self.updated)

    def table(self, name: str) -> Any:
        assert name == 'properties'
        return self._table


@pytest.fixture(autouse=True)
def _no_geocode_backfill(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_upsert_properties` fires geocoding as a background task — keep the
    network (and a pending-task warning) out of these tests."""
    async def _noop(*_a: Any, **_k: Any) -> None:
        return None
    monkeypatch.setattr('app.services.geocode.run_backfill', _noop)


async def test_fills_gallery_on_a_row_that_has_none() -> None:
    sb = _FakeSupabase([
        {'id': 'p1', 'direccion': 'Arévalo 1900', 'precio': 340_000.0,
         'tipo_operacion': 'venta', 'imagenes': []},
    ])
    props = [_prop('Arévalo 1900', 340_000.0, ['http://cdn/a.jpg', 'http://cdn/b.jpg'])]

    await _fill_missing_images(sb, props)

    assert sb.updated == {'p1': ['http://cdn/a.jpg', 'http://cdn/b.jpg']}


async def test_never_overwrites_a_curated_gallery() -> None:
    # The ficha editor already pruned this row's photos — a re-scrape must not
    # undo that, which is the whole reason the write is not a blind DO UPDATE.
    sb = _FakeSupabase([
        {'id': 'p1', 'direccion': 'Arévalo 1900', 'precio': 340_000.0,
         'tipo_operacion': 'venta', 'imagenes': ['http://cdn/curated.jpg']},
    ])
    props = [_prop('Arévalo 1900', 340_000.0, ['http://cdn/a.jpg', 'http://cdn/b.jpg'])]

    await _fill_missing_images(sb, props)

    assert sb.updated == {}


async def test_props_without_scraped_photos_update_nothing() -> None:
    sb = _FakeSupabase([
        {'id': 'p1', 'direccion': 'Arévalo 1900', 'precio': 340_000.0,
         'tipo_operacion': 'venta', 'imagenes': []},
    ])

    await _fill_missing_images(sb, [_prop('Arévalo 1900', 340_000.0, [])])

    assert sb.updated == {}


async def test_no_scraped_photos_at_all_skips_the_query_entirely() -> None:
    sb = _FakeSupabase([])
    await _fill_missing_images(sb, [_prop('Arévalo 1900', 340_000.0, [])])
    assert sb._table.select_calls == 0


async def test_matches_on_the_full_dedup_triple_not_just_direccion() -> None:
    # Same street address, different price = a different listing under the
    # `properties_dedup_idx (direccion, precio, tipo_operacion)` unique index.
    sb = _FakeSupabase([
        {'id': 'cheap', 'direccion': 'Arévalo 1900', 'precio': 200_000.0,
         'tipo_operacion': 'venta', 'imagenes': []},
        {'id': 'pricey', 'direccion': 'Arévalo 1900', 'precio': 340_000.0,
         'tipo_operacion': 'venta', 'imagenes': []},
    ])
    props = [_prop('Arévalo 1900', 340_000.0, ['http://cdn/a.jpg'])]

    await _fill_missing_images(sb, props)

    assert sb.updated == {'pricey': ['http://cdn/a.jpg']}


async def test_operacion_is_part_of_the_match() -> None:
    sb = _FakeSupabase([
        {'id': 'alq', 'direccion': 'Arévalo 1900', 'precio': 340_000.0,
         'tipo_operacion': 'alquiler', 'imagenes': []},
    ])
    props = [_prop('Arévalo 1900', 340_000.0, ['http://cdn/a.jpg'])]

    await _fill_missing_images(sb, props)

    assert sb.updated == {}


async def test_null_priced_rows_are_matched_too() -> None:
    sb = _FakeSupabase([
        {'id': 'p1', 'direccion': 'Sin Precio 1', 'precio': None,
         'tipo_operacion': 'venta', 'imagenes': []},
    ])
    props = [_prop('Sin Precio 1', None, ['http://cdn/a.jpg'])]

    await _fill_missing_images(sb, props)

    assert sb.updated == {'p1': ['http://cdn/a.jpg']}


async def test_rows_the_scrape_did_not_produce_are_left_alone() -> None:
    sb = _FakeSupabase([
        {'id': 'other', 'direccion': 'Otra Calle 500', 'precio': 100_000.0,
         'tipo_operacion': 'venta', 'imagenes': []},
    ])
    props = [_prop('Arévalo 1900', 340_000.0, ['http://cdn/a.jpg'])]

    await _fill_missing_images(sb, props)

    assert sb.updated == {}


async def test_fill_failure_is_swallowed() -> None:
    class _Boom:
        def table(self, _name: str) -> Any:
            raise RuntimeError('supabase down')

    # Best-effort, exactly like the surrounding persistence code: a failed
    # backfill must never fail the scraping run.
    await _fill_missing_images(_Boom(), [_prop('A 1', 1.0, ['http://cdn/a.jpg'])])


async def test_none_supabase_is_a_noop() -> None:
    await _fill_missing_images(None, [_prop('A 1', 1.0, ['http://cdn/a.jpg'])])


async def test_upsert_properties_runs_the_fill_pass() -> None:
    sb = _FakeSupabase([
        {'id': 'p1', 'direccion': 'Arévalo 1900', 'precio': 340_000.0,
         'tipo_operacion': 'venta', 'imagenes': []},
    ])
    props = [_prop('Arévalo 1900', 340_000.0, ['http://cdn/a.jpg'])]

    await _upsert_properties(sb, props, 'job-1')

    assert sb.updated == {'p1': ['http://cdn/a.jpg']}


async def test_upsert_still_writes_insert_ignore_so_curated_rows_survive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    class _CaptureTable(_FakePropertiesTable):
        def upsert(self, rows: list[dict], **kwargs: Any) -> '_CaptureTable':
            seen.update(kwargs)
            return self

    sb = _FakeSupabase([])
    sb._table = _CaptureTable([], sb.updated)
    monkeypatch.setattr(nodes, '_fill_missing_images', lambda *_a, **_k: _noop_coro())

    await _upsert_properties(sb, [_prop('A 1', 1.0)], 'job-1')

    assert seen['ignore_duplicates'] is True
    assert seen['on_conflict'] == 'direccion,precio,tipo_operacion'


async def _noop_coro() -> None:
    return None
