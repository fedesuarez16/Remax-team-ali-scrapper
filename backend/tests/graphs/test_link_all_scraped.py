"""All scraped properties must be linked to the job — matched ones flagged
`matches_criteria=True` so the results view can order matched-first while
still showing everything the search scraped."""
from typing import Any

from app.graphs.extraction.nodes import _link_job_properties, _split_by_criteria
from app.models.property import NormalizedProperty, ScrapingFilters


def _prop(direccion: str, precio: float | None, ambientes: int | None = None) -> NormalizedProperty:
    return NormalizedProperty(
        direccion=direccion, precio=precio, ambientes=ambientes,
        tipo_operacion='venta', fuente='zonaprop',
    )


class _Res:
    def __init__(self, data: Any) -> None:
        self.data = data


class _FakePropertiesQuery:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self._null_precio = False

    def select(self, *_a: Any, **_k: Any) -> '_FakePropertiesQuery':
        return self

    def in_(self, _key: str, _values: list) -> '_FakePropertiesQuery':
        return self

    def is_(self, key: str, _value: str) -> '_FakePropertiesQuery':
        if key == 'precio':
            self._null_precio = True
        return self

    async def execute(self) -> _Res:
        if self._null_precio:
            return _Res([r for r in self._rows if r.get('precio') is None])
        return _Res([r for r in self._rows if r.get('precio') is not None])


class _FakeUpsert:
    def __init__(self, sink: list, fail_on_flag: bool = False) -> None:
        self._sink = sink
        self._fail_on_flag = fail_on_flag
        self._rows: list[dict] = []

    def upsert(self, rows: list[dict], **_k: Any) -> '_FakeUpsert':
        self._rows = rows
        return self

    async def execute(self) -> _Res:
        if self._fail_on_flag and any('matches_criteria' in r for r in self._rows):
            raise RuntimeError('column "matches_criteria" does not exist')
        self._sink.append(list(self._rows))
        return _Res([])


class _FakeSupabase:
    def __init__(self, property_rows: list[dict], fail_on_flag: bool = False) -> None:
        self._property_rows = property_rows
        self._fail_on_flag = fail_on_flag
        self.upserts: list[list[dict]] = []

    def table(self, name: str) -> Any:
        if name == 'properties':
            return _FakePropertiesQuery(self._property_rows)
        assert name == 'search_property_results'
        return _FakeUpsert(self.upserts, self._fail_on_flag)


def test_split_by_criteria_orders_matched_first() -> None:
    filters = ScrapingFilters(precio_min=180_000, precio_max=250_000)
    props = [
        _prop('Calle Cara 1', 900_000),
        _prop('Calle Justa 2', 200_000),
        _prop('Calle Sin Precio 3', None),  # missing data never excludes
    ]
    matched, rest = _split_by_criteria(props, filters)
    assert [p.direccion for p in matched] == ['Calle Justa 2', 'Calle Sin Precio 3']
    assert [p.direccion for p in rest] == ['Calle Cara 1']


async def test_link_flags_matched_and_links_everything() -> None:
    props = [_prop('Calle A 1', 200_000.0), _prop('Calle B 2', 900_000.0)]
    matched = [props[0]]
    sb = _FakeSupabase([
        {'id': 'p1', 'direccion': 'Calle A 1', 'precio': 200_000.0, 'tipo_operacion': 'venta'},
        {'id': 'p2', 'direccion': 'Calle B 2', 'precio': 900_000.0, 'tipo_operacion': 'venta'},
    ])

    await _link_job_properties(sb, props, 'job-1', matched)

    assert len(sb.upserts) == 1
    rows = {r['property_id']: r for r in sb.upserts[0]}
    assert rows['p1'] == {'job_id': 'job-1', 'property_id': 'p1', 'matches_criteria': True}
    assert rows['p2'] == {'job_id': 'job-1', 'property_id': 'p2', 'matches_criteria': False}


async def test_link_retries_without_flag_when_column_missing() -> None:
    """Migration not applied yet → first upsert fails → rows are re-upserted
    without the flag so job links are never lost."""
    props = [_prop('Calle A 1', 200_000.0)]
    sb = _FakeSupabase(
        [{'id': 'p1', 'direccion': 'Calle A 1', 'precio': 200_000.0, 'tipo_operacion': 'venta'}],
        fail_on_flag=True,
    )

    await _link_job_properties(sb, props, 'job-1', props)

    assert len(sb.upserts) == 1
    assert sb.upserts[0] == [{'job_id': 'job-1', 'property_id': 'p1'}]


async def test_link_defaults_to_all_matched_when_matched_omitted() -> None:
    props = [_prop('Calle A 1', 200_000.0)]
    sb = _FakeSupabase(
        [{'id': 'p1', 'direccion': 'Calle A 1', 'precio': 200_000.0, 'tipo_operacion': 'venta'}],
    )

    await _link_job_properties(sb, props, 'job-1')

    assert sb.upserts[0][0]['matches_criteria'] is True
