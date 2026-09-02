"""Test-first for `review_agencies` honouring the pre-search source pick.

Two rules:
1. `buscar_inmobiliarias=False` → the manual-sources registry is NOT consulted
   at all (portales-only search must never touch inmobiliarias).
2. `zona_inmobiliarias='City Bell'` → only that zona's manually-curated
   inmobiliarias are fetched; other zonas are never consulted.

`adispatch_custom_event` and `interrupt` are monkeypatched: this test targets
the source-selection branch, not LangGraph's event/interrupt machinery.
"""
import pytest

from app.graphs.extraction import nodes
from app.graphs.extraction.nodes import review_agencies


@pytest.fixture(autouse=True)
def _no_langgraph_runtime(monkeypatch):
    events: list[tuple[str, dict]] = []

    async def _fake_dispatch(name, data, config=None):
        events.append((name, data))

    monkeypatch.setattr(nodes, 'adispatch_custom_event', _fake_dispatch)
    monkeypatch.setattr(nodes, 'interrupt', lambda _payload: [])
    return events


def _config(sb) -> dict:
    return {'configurable': {'supabase': sb}}


class _Sentinel:
    """Any access means the registry was consulted — which must not happen."""

    def table(self, name: str):
        raise AssertionError(f'manual sources must not be fetched (table {name})')


async def test_portales_only_never_consults_manual_sources() -> None:
    state = {
        'job_id': 'job-1',
        'agencies': [],
        'normalized_properties': [],
        'source_selection': {'buscar_portales': True, 'buscar_inmobiliarias': False},
    }
    out = await review_agencies(state, _config(_Sentinel()))
    assert out['manual_sources'] == []


async def test_inmobiliarias_zona_is_passed_through_to_the_fetch(monkeypatch) -> None:
    captured: dict = {}

    async def _fake_fetch(sb, zona=None, **_kw):
        captured['zona'] = zona
        return [{'nombre': 'Inmobiliaria A', 'url': 'https://a.com'}]

    monkeypatch.setattr(nodes, '_fetch_active_manual_sources', _fake_fetch)
    state = {
        'job_id': 'job-1',
        'agencies': [],
        'normalized_properties': [],
        'source_selection': {
            'buscar_portales': False,
            'buscar_inmobiliarias': True,
            'zona_inmobiliarias': 'City Bell',
        },
    }
    out = await review_agencies(state, _config(object()))
    assert captured['zona'] == 'City Bell'
    assert [s['nombre'] for s in out['manual_sources']] == ['Inmobiliaria A']


async def test_todas_las_zonas_passes_none(monkeypatch) -> None:
    captured: dict = {}

    async def _fake_fetch(sb, zona=None, **_kw):
        captured['zona'] = zona
        return []

    monkeypatch.setattr(nodes, '_fetch_active_manual_sources', _fake_fetch)
    state = {
        'job_id': 'job-1',
        'agencies': [],
        'normalized_properties': [],
        'source_selection': {'buscar_inmobiliarias': True, 'zona_inmobiliarias': None},
    }
    await review_agencies(state, _config(object()))
    assert captured['zona'] is None


async def test_missing_selection_still_fetches_all_manual_sources(monkeypatch) -> None:
    """Legacy/omitted selection must behave exactly as before the feature."""
    captured: dict = {}

    async def _fake_fetch(sb, zona=None, **_kw):
        captured['zona'] = zona
        return [{'nombre': 'Inmobiliaria A', 'url': 'https://a.com'}]

    monkeypatch.setattr(nodes, '_fetch_active_manual_sources', _fake_fetch)
    state = {'job_id': 'job-1', 'agencies': [], 'normalized_properties': []}
    out = await review_agencies(state, _config(object()))
    assert captured['zona'] is None
    assert len(out['manual_sources']) == 1
