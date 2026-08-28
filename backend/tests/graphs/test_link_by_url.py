"""Once several rows can share an address, the job link must key on the URL.

`_link_job_properties` located rows by `direccion` and claimed every one whose
`(direccion, precio, tipo_operacion)` matched. That was sound only while the
unique index guaranteed ONE row per triple. Widening the index so distinct
listings stop overwriting each other breaks that assumption in both
directions: this job's listings are no longer distinguishable from another
search's rows at the same address and price, so the triple would drag
unrelated properties into the results.

`url_origen` is the listing's identity and every scraped row carries one.
Rows without one keep the old triple path.
"""
from typing import Any

from app.graphs.extraction.nodes import _link_job_properties
from app.models.property import NormalizedProperty


def _prop(direccion: str, precio: float | None, url: str | None) -> NormalizedProperty:
    return NormalizedProperty(
        direccion=direccion, precio=precio, tipo_operacion='venta',
        fuente='zonaprop', url_origen=url,
    )


class _Res:
    def __init__(self, data: Any) -> None:
        self.data = data


class _FakeProperties:
    """Records what column the lookup filtered on, and answers from `rows`."""
    def __init__(self, rows: list[dict], seen: dict) -> None:
        self._rows = rows
        self._seen = seen
        self._by_url: list[str] | None = None
        self._null_precio = False

    def select(self, *_a: Any, **_k: Any) -> '_FakeProperties':
        return self

    def in_(self, key: str, values: list) -> '_FakeProperties':
        self._seen.setdefault('in_', []).append(key)
        if key == 'url_origen':
            self._by_url = values
        return self

    def is_(self, key: str, _v: str) -> '_FakeProperties':
        if key == 'precio':
            self._null_precio = True
        return self

    async def execute(self) -> _Res:
        rows = self._rows
        if self._by_url is not None:
            return _Res([r for r in rows if r.get('url_origen') in self._by_url])
        if self._null_precio:
            return _Res([r for r in rows if r.get('precio') is None])
        return _Res([r for r in rows if r.get('precio') is not None])


class _FakeLinks:
    def __init__(self, sink: list) -> None:
        self._sink = sink
        self._rows: list[dict] = []

    def upsert(self, rows: list[dict], **_k: Any) -> '_FakeLinks':
        self._rows = rows
        return self

    async def execute(self) -> _Res:
        self._sink.append(list(self._rows))
        return _Res([])


class _FakeSb:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.upserts: list[list[dict]] = []
        self.seen: dict = {}

    def table(self, name: str) -> Any:
        if name == 'properties':
            return _FakeProperties(self._rows, self.seen)
        return _FakeLinks(self.upserts)


def _linked_ids(sb: _FakeSb) -> set:
    return {r['property_id'] for batch in sb.upserts for r in batch}


async def test_every_listing_at_one_address_gets_linked() -> None:
    """THE point: a block of houses at one address and one price used to be a
    single row, so a single link. Now each listing links on its own."""
    rows = [
        {'id': 'a', 'url_origen': 'https://zp/1', 'direccion': 'La Plata',
         'precio': 380_000.0, 'tipo_operacion': 'venta'},
        {'id': 'b', 'url_origen': 'https://zp/2', 'direccion': 'La Plata',
         'precio': 380_000.0, 'tipo_operacion': 'venta'},
        {'id': 'c', 'url_origen': 'https://zp/3', 'direccion': 'La Plata',
         'precio': 380_000.0, 'tipo_operacion': 'venta'},
    ]
    sb = _FakeSb(rows)
    props = [
        _prop('La Plata', 380_000.0, 'https://zp/1'),
        _prop('La Plata', 380_000.0, 'https://zp/2'),
        _prop('La Plata', 380_000.0, 'https://zp/3'),
    ]

    await _link_job_properties(sb, props, 'job-1')

    assert _linked_ids(sb) == {'a', 'b', 'c'}


async def test_another_searchs_row_is_not_dragged_in() -> None:
    """Same address and price, different listing — it is not this job's."""
    rows = [
        {'id': 'mine', 'url_origen': 'https://zp/1', 'direccion': 'La Plata',
         'precio': 380_000.0, 'tipo_operacion': 'venta'},
        {'id': 'someone-elses', 'url_origen': 'https://zp/999', 'direccion': 'La Plata',
         'precio': 380_000.0, 'tipo_operacion': 'venta'},
    ]
    sb = _FakeSb(rows)

    await _link_job_properties(sb, [_prop('La Plata', 380_000.0, 'https://zp/1')], 'job-1')

    assert _linked_ids(sb) == {'mine'}


async def test_the_lookup_uses_the_url_column() -> None:
    rows = [{'id': 'a', 'url_origen': 'https://zp/1', 'direccion': 'X',
             'precio': 1.0, 'tipo_operacion': 'venta'}]
    sb = _FakeSb(rows)

    await _link_job_properties(sb, [_prop('X', 1.0, 'https://zp/1')], 'job-1')

    assert 'url_origen' in sb.seen.get('in_', [])


async def test_rows_without_a_url_keep_the_triple_path() -> None:
    rows = [{'id': 'a', 'url_origen': None, 'direccion': 'Calle 7 500',
             'precio': 250_000.0, 'tipo_operacion': 'venta'}]
    sb = _FakeSb(rows)

    await _link_job_properties(sb, [_prop('Calle 7 500', 250_000.0, None)], 'job-1')

    assert _linked_ids(sb) == {'a'}


async def test_the_matched_flag_survives() -> None:
    rows = [
        {'id': 'a', 'url_origen': 'https://zp/1', 'direccion': 'X',
         'precio': 100.0, 'tipo_operacion': 'venta'},
        {'id': 'b', 'url_origen': 'https://zp/2', 'direccion': 'X',
         'precio': 900.0, 'tipo_operacion': 'venta'},
    ]
    sb = _FakeSb(rows)
    cheap = _prop('X', 100.0, 'https://zp/1')
    dear = _prop('X', 900.0, 'https://zp/2')

    await _link_job_properties(sb, [cheap, dear], 'job-1', matched=[cheap])

    flags = {r['property_id']: r['matches_criteria']
             for batch in sb.upserts for r in batch}
    assert flags == {'a': True, 'b': False}
