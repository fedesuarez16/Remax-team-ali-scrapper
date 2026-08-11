"""`matcher._parse_criteria` runs Haiku on every ranking pass — it must be booked.

Two entry points reach it, and only one of them belongs to a search:

    rank_properties(query, props, sb=…, job_id=…)  ← GET /scraping/{job}/properties
    match_properties(query, sb)                    ← POST /properties/match (CRM)

So the ledger row carries a `job_id` in the first case and NULL in the second.
Both are billed either way, including when tool-use parsing fails and the code
falls back to the heuristic parser.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services import matcher
from app.services.llm_costs import SCOPE_MATCH_PARSE


class _Usage:
    def __init__(self, input_tokens: int = 400, output_tokens: int = 60) -> None:
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
    """Captures `llm_usage` inserts and serves an empty `properties` query."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self._pending: dict[str, Any] = {}
        self._table = ''

    def table(self, name: str) -> '_LedgerSupabase':
        self._table = name
        return self

    def insert(self, payload: dict[str, Any]) -> '_LedgerSupabase':
        self._pending = payload
        return self

    def select(self, *_a: Any, **_k: Any) -> '_LedgerSupabase':
        return self

    def eq(self, *_a: Any, **_k: Any) -> '_LedgerSupabase':
        return self

    def gte(self, *_a: Any, **_k: Any) -> '_LedgerSupabase':
        return self

    def lte(self, *_a: Any, **_k: Any) -> '_LedgerSupabase':
        return self

    def limit(self, *_a: Any, **_k: Any) -> '_LedgerSupabase':
        return self

    async def execute(self) -> Any:
        if self._table == 'llm_usage':
            self.rows.append(self._pending)
            return type('_Res', (), {'data': [self._pending]})()
        return type('_Res', (), {'data': []})()


def _stub_llm(monkeypatch: pytest.MonkeyPatch, message: Any) -> None:
    async def _create(**_kwargs: Any) -> Any:
        if isinstance(message, Exception):
            raise message
        return message

    monkeypatch.setattr(matcher._client.messages, 'create', _create)


_CRITERIA = {'tipo_propiedad': 'departamento', 'ambientes_min': 2}


async def test_ranking_books_the_criteria_parse_against_the_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sb = _LedgerSupabase()
    _stub_llm(monkeypatch, _Msg(_CRITERIA, _Usage(520, 75)))

    props = [{'precio': 100000, 'tipo_propiedad': 'departamento', 'ambientes': 3}]
    await matcher.rank_properties('depto 2 amb', props, sb=sb, job_id='job-77')

    assert len(sb.rows) == 1
    row = sb.rows[0]
    assert row['scope'] == SCOPE_MATCH_PARSE
    assert row['job_id'] == 'job-77'
    assert row['cost_usd'] == pytest.approx(520 / 1e6 + 75 / 1e6 * 5.0)


async def test_ranking_without_a_ledger_client_still_ranks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`sb` is optional so the existing call path keeps working un-instrumented."""
    _stub_llm(monkeypatch, _Msg(_CRITERIA, _Usage()))

    props = [{'precio': 100000, 'tipo_propiedad': 'departamento', 'ambientes': 3}]
    ranked = await matcher.rank_properties('depto 2 amb', props)

    assert 'match_score' in ranked[0]


async def test_crm_match_books_with_no_job_attached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /properties/match is not part of any search — job_id must stay NULL."""
    sb = _LedgerSupabase()
    _stub_llm(monkeypatch, _Msg(_CRITERIA, _Usage(480, 50)))

    await matcher.match_properties('depto 2 amb en Belgrano', sb)

    assert len(sb.rows) == 1
    assert sb.rows[0]['scope'] == SCOPE_MATCH_PARSE
    assert sb.rows[0]['job_id'] is None


async def test_books_the_call_that_fell_back_to_the_heuristic_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No tool_use → heuristic parser takes over, but the call was still billed."""
    sb = _LedgerSupabase()
    _stub_llm(monkeypatch, _Msg(None, _Usage(410, 15)))

    await matcher.rank_properties('algo raro', [], sb=sb, job_id='job-78')

    assert len(sb.rows) == 1
    assert sb.rows[0]['input_tokens'] == 410


async def test_books_nothing_when_the_call_never_reached_anthropic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sb = _LedgerSupabase()
    _stub_llm(monkeypatch, RuntimeError('connection reset'))

    await matcher.rank_properties('depto', [], sb=sb, job_id='job-79')

    assert sb.rows == []
