"""Test-first for surfacing manually-registered sources in the `agencies_review`
step — the pre-confirmation card the operator actually sees.

Before this, `review_agencies` fetched the curated registry
(backend/app/api/v1/manual_sources.py) and pushed it straight into the fan-out:
the operator confirmed a list of Google-Maps agencies and never learned that
their own inmobiliarias were in the run at all. Three rules:

1. The `agencies_review` event carries `manual_sources` alongside `agencies`,
   so the selector can render (and let the user deselect) them.
2. A run whose only inmobiliarias are the curated ones still interrupts —
   previously it skipped the review entirely and auto-included them.
3. The resume payload can scope which curated sources stay in. A bare list
   (the pre-feature shape) means "all of them", NOT "none".
"""
import pytest

from app.graphs.extraction import nodes
from app.graphs.extraction.nodes import review_agencies
from app.models.property import Agency


@pytest.fixture
def events(monkeypatch):
    """Captures dispatched events; `interrupt` returns whatever the test sets
    on `resume.value` (default: everything selected)."""
    captured: list[tuple[str, dict]] = []

    async def _fake_dispatch(name, data, config=None):
        captured.append((name, data))

    monkeypatch.setattr(nodes, 'adispatch_custom_event', _fake_dispatch)
    return captured


@pytest.fixture
def resume(monkeypatch):
    class _Resume:
        value: object = []
        payload: object = None

    holder = _Resume()

    def _fake_interrupt(payload):
        holder.payload = payload
        return holder.value

    monkeypatch.setattr(nodes, 'interrupt', _fake_interrupt)
    return holder


def _config(sb=None) -> dict:
    return {'configurable': {'supabase': sb}}


def _agency(agency_id: str) -> Agency:
    return Agency(id=agency_id, nombre=f'Agencia {agency_id}', sitio_web=f'https://{agency_id}.com')


def _manual(source_id: str, nombre: str) -> dict:
    return {'id': source_id, 'nombre': nombre, 'url': f'https://{source_id}.com', 'zona': 'City Bell'}


CURATED = [_manual('m1', 'RE/MAX City Bell'), _manual('m2', 'Inmobiliaria Sur')]


def _state(agencies: list[Agency]) -> dict:
    return {
        'job_id': 'job-1',
        'agencies': agencies,
        'normalized_properties': [],
        'source_selection': {'buscar_portales': True, 'buscar_inmobiliarias': True},
    }


def _patch_fetch(monkeypatch, rows: list[dict]) -> None:
    async def _fake_fetch(sb, zona=None, **_kw):
        return list(rows)

    monkeypatch.setattr(nodes, '_fetch_active_manual_sources', _fake_fetch)


def _review_event(events: list[tuple[str, dict]]) -> dict:
    matches = [data for name, data in events if data.get('event') == 'agencies_review']
    assert matches, f'no agencies_review event dispatched (got {[n for n, _ in events]})'
    return matches[-1]


# ── Rule 1: the curated sources reach the selector ────────────────────────────

async def test_review_event_carries_manual_sources(monkeypatch, events, resume) -> None:
    _patch_fetch(monkeypatch, CURATED)
    await review_agencies(_state([_agency('a1')]), _config())

    payload = _review_event(events)
    assert [s['nombre'] for s in payload['manual_sources']] == ['RE/MAX City Bell', 'Inmobiliaria Sur']
    assert [a['id'] for a in payload['agencies']] == ['a1']


async def test_manual_sources_key_is_always_present(monkeypatch, events, resume) -> None:
    """A portales+agencies run with an empty registry still gets the key, so the
    frontend never has to branch on `undefined`."""
    _patch_fetch(monkeypatch, [])
    await review_agencies(_state([_agency('a1')]), _config())
    assert _review_event(events)['manual_sources'] == []


async def test_message_mentions_the_curated_count(monkeypatch, events, resume) -> None:
    _patch_fetch(monkeypatch, CURATED)
    await review_agencies(_state([_agency('a1')]), _config())
    assert '2' in _review_event(events)['message']


# ── Rule 2: curated-only runs must still ask ──────────────────────────────────

async def test_only_manual_sources_still_interrupts(monkeypatch, events, resume) -> None:
    _patch_fetch(monkeypatch, CURATED)
    resume.value = {'agency_ids': [], 'manual_source_ids': ['m1', 'm2']}

    out = await review_agencies(_state([]), _config())

    assert resume.payload == {'type': 'agency_selection'}
    assert _review_event(events)['agencies'] == []
    assert [s['id'] for s in out['manual_sources']] == ['m1', 'm2']
    assert not any(data.get('event') == 'done' for _, data in events)


async def test_nothing_at_all_emits_done_without_interrupting(monkeypatch, events, resume) -> None:
    _patch_fetch(monkeypatch, [])
    out = await review_agencies(_state([]), _config())

    assert resume.payload is None
    assert [data['event'] for _, data in events] == ['done']
    assert out == {'selected_agency_ids': [], 'manual_sources': []}


# ── Rule 3: the resume payload scopes the curated sources ─────────────────────

async def test_deselected_manual_sources_are_dropped(monkeypatch, events, resume) -> None:
    _patch_fetch(monkeypatch, CURATED)
    resume.value = {'agency_ids': ['a1'], 'manual_source_ids': ['m2']}

    out = await review_agencies(_state([_agency('a1')]), _config())

    assert [s['id'] for s in out['manual_sources']] == ['m2']
    assert out['selected_agency_ids'] == ['a1']


async def test_empty_manual_selection_drops_all_of_them(monkeypatch, events, resume) -> None:
    """`[]` is an explicit "none", distinct from the legacy `None` below."""
    _patch_fetch(monkeypatch, CURATED)
    resume.value = {'agency_ids': ['a1'], 'manual_source_ids': []}

    out = await review_agencies(_state([_agency('a1')]), _config())
    assert out['manual_sources'] == []


async def test_legacy_list_resume_keeps_every_manual_source(monkeypatch, events, resume) -> None:
    """A job interrupted by the previous build resumes with a bare id list; it
    must not silently drop the curated sources it never got to show."""
    _patch_fetch(monkeypatch, CURATED)
    resume.value = ['a1']

    out = await review_agencies(_state([_agency('a1')]), _config())

    assert out['selected_agency_ids'] == ['a1']
    assert [s['id'] for s in out['manual_sources']] == ['m1', 'm2']


async def test_omitted_manual_ids_key_keeps_every_manual_source(monkeypatch, events, resume) -> None:
    _patch_fetch(monkeypatch, CURATED)
    resume.value = {'agency_ids': ['a1']}

    out = await review_agencies(_state([_agency('a1')]), _config())
    assert [s['id'] for s in out['manual_sources']] == ['m1', 'm2']


# ── The registry read has to carry the id the selection is keyed on ───────────

class _RecordingSupabase:
    def __init__(self) -> None:
        self.columns: str | None = None

    def table(self, _name: str) -> '_RecordingSupabase':
        return self

    def select(self, columns: str) -> '_RecordingSupabase':
        self.columns = columns
        return self

    def eq(self, _field: str, _value) -> '_RecordingSupabase':
        return self

    async def execute(self):
        class _Res:
            data: list[dict] = []

        return _Res()


async def test_fetch_selects_the_id_column() -> None:
    sb = _RecordingSupabase()
    await nodes._fetch_active_manual_sources(sb, 'City Bell')
    assert sb.columns is not None
    assert 'id' in [c.strip() for c in sb.columns.split(',')]
