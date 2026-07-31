"""Test-first for zona-scoped manual sources — the manually-curated
"which inmobiliarias belong to which zona" classification.

`_fetch_active_manual_sources(sb, zona)` must:
- filter on the normalized zona key (`zona_norm`) when a zona is given, so
  'City Bell', 'city bell' and 'City Bell, Buenos Aires' all hit the same bucket
  (same `normalize_zona` used by the agency cache);
- return every active source when the zona is None/blank ("todas las zonas");
- stay best-effort (empty list, never raise) when the table/column is missing.

Written BEFORE the helper takes a `zona` argument, so the filter assertions
MUST fail until it lands.
"""
import pytest

from app.graphs.extraction.nodes import _fetch_active_manual_sources
from app.services.zona import normalize_zona


class _Res:
    def __init__(self, data) -> None:
        self.data = data


class _FakeQuery:
    def __init__(self, rows: list[dict], recorded: list[tuple[str, object]]) -> None:
        self._rows = rows
        self._recorded = recorded

    def select(self, *_a, **_kw) -> '_FakeQuery':
        return self

    def eq(self, field: str, value) -> '_FakeQuery':
        self._recorded.append((field, value))
        return self

    async def execute(self) -> _Res:
        rows = self._rows
        for field, value in self._recorded:
            rows = [r for r in rows if r.get(field) == value]
        return _Res(rows)


class _FakeTable:
    def __init__(self, rows: list[dict], recorded: list[tuple[str, object]]) -> None:
        self._rows = rows
        self._recorded = recorded

    def select(self, *a, **kw) -> _FakeQuery:
        return _FakeQuery(self._rows, self._recorded).select(*a, **kw)


class _FakeSupabase:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.recorded_filters: list[tuple[str, object]] = []

    def table(self, name: str) -> _FakeTable:
        assert name == 'manual_sources'
        return _FakeTable(self.rows, self.recorded_filters)


class _RaisingSupabase:
    def table(self, _name: str):
        raise RuntimeError('column manual_sources.zona_norm does not exist')


def _src(nombre: str, url: str, zona: str | None, *, activo: bool = True) -> dict:
    return {
        'nombre': nombre, 'url': url, 'activo': activo,
        'zona': zona, 'zona_norm': normalize_zona(zona) if zona else None,
    }


ROWS = [
    _src('Inmobiliaria A', 'https://a.com', 'City Bell'),
    _src('Inmobiliaria B', 'https://b.com', 'City Bell, Buenos Aires'),
    _src('Inmobiliaria D', 'https://d.com', 'Gonnet'),
    _src('Portal sin zona', 'https://z.com', None),
]


async def test_zona_filters_to_that_zonas_inmobiliarias_only() -> None:
    sb = _FakeSupabase(list(ROWS))
    sources = await _fetch_active_manual_sources(sb, 'City Bell')
    assert sorted(s['nombre'] for s in sources) == ['Inmobiliaria A', 'Inmobiliaria B']
    assert ('zona_norm', normalize_zona('City Bell')) in sb.recorded_filters


async def test_zona_matching_is_normalized_not_literal() -> None:
    sb = _FakeSupabase(list(ROWS))
    sources = await _fetch_active_manual_sources(sb, 'city bell, Buenos Aires')
    assert sorted(s['nombre'] for s in sources) == ['Inmobiliaria A', 'Inmobiliaria B']


async def test_other_zonas_are_never_consulted() -> None:
    sb = _FakeSupabase(list(ROWS))
    sources = await _fetch_active_manual_sources(sb, 'Gonnet')
    assert [s['nombre'] for s in sources] == ['Inmobiliaria D']


async def test_unknown_zona_returns_nothing() -> None:
    sb = _FakeSupabase(list(ROWS))
    assert await _fetch_active_manual_sources(sb, 'Hudson') == []


@pytest.mark.parametrize('zona', [None, '', '   '])
async def test_no_zona_means_all_registered_sources(zona) -> None:
    sb = _FakeSupabase(list(ROWS))
    sources = await _fetch_active_manual_sources(sb, zona)
    assert len(sources) == 4
    assert not any(field == 'zona_norm' for field, _ in sb.recorded_filters)


async def test_inactive_sources_are_excluded_within_a_zona() -> None:
    sb = _FakeSupabase([*ROWS, _src('Inmobiliaria C', 'https://c.com', 'City Bell', activo=False)])
    sources = await _fetch_active_manual_sources(sb, 'City Bell')
    assert 'Inmobiliaria C' not in [s['nombre'] for s in sources]


async def test_zona_column_missing_degrades_to_empty_list() -> None:
    assert await _fetch_active_manual_sources(_RaisingSupabase(), 'City Bell') == []


async def test_no_supabase_returns_empty_list() -> None:
    assert await _fetch_active_manual_sources(None, 'City Bell') == []


async def test_default_zona_argument_keeps_old_call_shape_working() -> None:
    sb = _FakeSupabase(list(ROWS))
    assert len(await _fetch_active_manual_sources(sb)) == 4
