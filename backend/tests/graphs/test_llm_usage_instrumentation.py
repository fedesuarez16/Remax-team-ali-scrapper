"""Every Anthropic call in the scraping graph must land in the `llm_usage` ledger.

Before this, three of the graph's four LLM call sites billed tokens without booking
a row, so per-search LLM spend read as a fraction of the real number:

    parse_query                      → 1 call per search
    extract_website_properties_llm   → 1 call per scraped page
    extract_instagram_properties_llm → 1 call per scraped post

The invariant these tests pin: a call that reached Anthropic is a call that was
billed, so it gets a row — whether or not its output was usable. Tool-use parsing
failures, clarification branches and unusable pages are all still paid for.

`adispatch_custom_event` is monkeypatched: these tests target the ledger, not
LangGraph's event machinery.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.graphs.extraction import nodes
from app.services.llm_costs import (
    SCOPE_EXTRACT_INSTAGRAM,
    SCOPE_EXTRACT_WEBSITE,
    SCOPE_SEARCH_PARSE,
)


# ── fakes ────────────────────────────────────────────────────────────────────


class _Usage:
    def __init__(self, input_tokens: int = 1000, output_tokens: int = 200) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_creation_input_tokens = None
        self.cache_read_input_tokens = None


class _ToolUse:
    type = 'tool_use'

    def __init__(self, payload: dict[str, Any]) -> None:
        self.input = payload


class _Msg:
    def __init__(self, tool_input: dict[str, Any] | None, usage: _Usage) -> None:
        self.content = [_ToolUse(tool_input)] if tool_input is not None else []
        self.usage = usage


class _LedgerSupabase:
    """Captures `llm_usage` inserts; rejects any other table."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self._pending: dict[str, Any] = {}

    def table(self, name: str) -> '_LedgerSupabase':
        assert name == 'llm_usage', f'unexpected table {name}'
        return self

    def insert(self, payload: dict[str, Any]) -> '_LedgerSupabase':
        self._pending = payload
        return self

    async def execute(self) -> Any:
        self.rows.append(self._pending)
        return type('_Res', (), {'data': [self._pending]})()


@pytest.fixture(autouse=True)
def _silence_events(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_dispatch(_name: Any, _data: Any, config: Any = None) -> None:
        return None

    monkeypatch.setattr(nodes, 'adispatch_custom_event', _fake_dispatch)


def _stub_llm(monkeypatch: pytest.MonkeyPatch, messages: list[Any]) -> list[dict]:
    """Feed `messages` to successive `messages.create` calls. An Exception instance
    in the list is raised instead of returned (a call that never reached Anthropic)."""
    seen: list[dict] = []
    queue = list(messages)

    async def _create(**kwargs: Any) -> Any:
        seen.append(kwargs)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(nodes._client.messages, 'create', _create)
    return seen


def _config(sb: Any) -> dict:
    return {'configurable': {'supabase': sb}}


# ── parse_query ──────────────────────────────────────────────────────────────


async def test_parse_query_books_its_call_against_the_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sb = _LedgerSupabase()
    _stub_llm(monkeypatch, [_Msg({'zonas': ['Belgrano']}, _Usage(1200, 150))])

    await nodes.parse_query({'query': 'depto en Belgrano', 'job_id': 'job-1'}, _config(sb))

    assert len(sb.rows) == 1
    row = sb.rows[0]
    assert row['scope'] == SCOPE_SEARCH_PARSE
    assert row['job_id'] == 'job-1'
    assert row['input_tokens'] == 1200
    assert row['output_tokens'] == 150
    assert row['cost_usd'] == pytest.approx(1200 / 1e6 + 150 / 1e6 * 5.0)


async def test_parse_query_books_the_call_that_asked_for_clarification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No tool_use block means the query was unusable — Anthropic still billed it."""
    sb = _LedgerSupabase()
    _stub_llm(monkeypatch, [_Msg(None, _Usage(900, 40))])

    result = await nodes.parse_query({'query': 'hola', 'job_id': 'job-2'}, _config(sb))

    assert result['clarification_needed'] is True
    assert len(sb.rows) == 1
    assert sb.rows[0]['scope'] == SCOPE_SEARCH_PARSE


async def test_parse_query_books_the_call_when_the_zone_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Filters parsed but no zona → clarification. Still one billed call."""
    sb = _LedgerSupabase()
    _stub_llm(monkeypatch, [_Msg({'tipo_propiedad': 'departamento'}, _Usage(1000, 60))])

    result = await nodes.parse_query({'query': 'un depto', 'job_id': 'job-3'}, _config(sb))

    assert result['clarification_needed'] is True
    assert len(sb.rows) == 1


async def test_parse_query_survives_a_missing_supabase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ledger is a side effect: no client configured must not break the search."""
    _stub_llm(monkeypatch, [_Msg({'zonas': ['Nuñez']}, _Usage())])

    result = await nodes.parse_query(
        {'query': 'casa en Nuñez', 'job_id': 'job-4'}, {'configurable': {}}
    )
    assert result['clarification_needed'] is False


# ── extract_website_properties_llm ───────────────────────────────────────────


def _page(url: str, text_len: int = 500) -> dict[str, Any]:
    return {'url': url, 'text': 'x' * text_len, 'images': []}


async def test_website_extraction_books_one_row_per_analyzed_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-page rows, not one per node: this is the loop that dominates LLM spend,
    and a single aggregate row would hide which site was expensive."""
    sb = _LedgerSupabase()
    _stub_llm(monkeypatch, [
        _Msg({'propiedades': []}, _Usage(4000, 300)),
        _Msg({'propiedades': []}, _Usage(2500, 120)),
    ])

    state = {'job_id': 'job-5', 'website_pages': [_page('https://a.com'), _page('https://b.com')]}
    await nodes.extract_website_properties_llm(state, _config(sb))

    assert len(sb.rows) == 2
    assert {r['scope'] for r in sb.rows} == {SCOPE_EXTRACT_WEBSITE}
    assert {r['job_id'] for r in sb.rows} == {'job-5'}
    assert [r['url'] for r in sb.rows] == ['https://a.com', 'https://b.com']
    assert [r['input_tokens'] for r in sb.rows] == [4000, 2500]


async def test_website_extraction_skips_pages_that_never_reach_the_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pages under 100 chars are dropped before the call — nothing billed, no row."""
    sb = _LedgerSupabase()
    _stub_llm(monkeypatch, [_Msg({'propiedades': []}, _Usage(3000, 100))])

    state = {
        'job_id': 'job-6',
        'website_pages': [_page('https://tiny.com', text_len=50), _page('https://real.com')],
    }
    await nodes.extract_website_properties_llm(state, _config(sb))

    assert len(sb.rows) == 1
    assert sb.rows[0]['url'] == 'https://real.com'


async def test_website_extraction_books_nothing_for_a_failed_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raised call never got a usage object — booking 0 would be a phantom row."""
    sb = _LedgerSupabase()
    _stub_llm(monkeypatch, [
        RuntimeError('overloaded_error'),
        _Msg({'propiedades': []}, _Usage(1500, 90)),
    ])

    state = {'job_id': 'job-7', 'website_pages': [_page('https://down.com'), _page('https://ok.com')]}
    await nodes.extract_website_properties_llm(state, _config(sb))

    assert len(sb.rows) == 1
    assert sb.rows[0]['url'] == 'https://ok.com'


async def test_website_extraction_books_a_call_whose_output_was_unusable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The call succeeded and was billed; the model just returned no tool_use."""
    sb = _LedgerSupabase()
    _stub_llm(monkeypatch, [_Msg(None, _Usage(3200, 20))])

    state = {'job_id': 'job-8', 'website_pages': [_page('https://noisy.com')]}
    await nodes.extract_website_properties_llm(state, _config(sb))

    assert len(sb.rows) == 1
    assert sb.rows[0]['input_tokens'] == 3200


# ── extract_instagram_properties_llm ─────────────────────────────────────────


async def test_instagram_extraction_books_one_row_per_analyzed_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sb = _LedgerSupabase()
    _stub_llm(monkeypatch, [
        _Msg({'es_propiedad': True, 'descripcion': 'depto 2 amb'}, _Usage(700, 110)),
        _Msg({'es_propiedad': False}, _Usage(400, 20)),
    ])

    state = {
        'job_id': 'job-9',
        'instagram_posts': [
            {'titulo': 'Vendo depto 2 ambientes en Belgrano con balcon'},
            {'titulo': 'Feliz dia del amigo a toda nuestra comunidad!!'},
        ],
    }
    await nodes.extract_instagram_properties_llm(state, _config(sb))

    # The second post was not a listing, but classifying it still cost tokens.
    assert len(sb.rows) == 2
    assert {r['scope'] for r in sb.rows} == {SCOPE_EXTRACT_INSTAGRAM}
    assert {r['job_id'] for r in sb.rows} == {'job-9'}
    assert [r['input_tokens'] for r in sb.rows] == [700, 400]


async def test_instagram_extraction_skips_captions_too_short_to_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sb = _LedgerSupabase()
    _stub_llm(monkeypatch, [_Msg({'es_propiedad': False}, _Usage(300, 10))])

    state = {
        'job_id': 'job-10',
        'instagram_posts': [{'titulo': 'hola'}, {'titulo': 'Vendo casa en City Bell 3 dormitorios'}],
    }
    await nodes.extract_instagram_properties_llm(state, _config(sb))

    assert len(sb.rows) == 1
