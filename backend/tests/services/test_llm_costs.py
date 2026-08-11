"""Test-first for the Anthropic token-cost ledger behind the Ficha Propio counters.

Generating a Ficha Propio costs LLM tokens, not Apify credits: `importer._extract_llm`
runs Haiku over the portal page, and `ficha.enrich_ficha` runs it again over the
description. These tests pin the pricing math and the write path.

Prices are per MILLION tokens, from the Anthropic pricing table:
Haiku 4.5 → $1.00 input / $5.00 output. Cache writes bill at 1.25x input,
cache reads at 0.1x input.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.llm_costs import (
    HAIKU_4_5,
    SCOPE_EXTRACT_INSTAGRAM,
    SCOPE_EXTRACT_WEBSITE,
    SCOPE_FICHA_ENRICH,
    SCOPE_FICHA_PROPIO,
    SCOPE_MATCH_PARSE,
    SCOPE_SEARCH_PARSE,
    SEARCH_SCOPES,
    record_llm_usage,
    usage_cost_usd,
)


class _Usage:
    """Mirrors the shape of `anthropic` SDK's `msg.usage` (a pydantic object)."""

    def __init__(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_creation_input_tokens: int | None = None,
        cache_read_input_tokens: int | None = None,
    ) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens
        self.cache_read_input_tokens = cache_read_input_tokens


# ── pricing math ─────────────────────────────────────────────────────────────


def test_prices_input_and_output_separately() -> None:
    # 1M input @ $1 + 1M output @ $5 = $6
    cost = usage_cost_usd(HAIKU_4_5, _Usage(input_tokens=1_000_000, output_tokens=1_000_000))
    assert cost == pytest.approx(6.0)


def test_typical_ficha_call_costs_fractions_of_a_cent() -> None:
    # A real ficha extraction: ~8k chars of page text in, ~400 tokens of tool JSON out.
    cost = usage_cost_usd(HAIKU_4_5, _Usage(input_tokens=2400, output_tokens=400))
    assert cost == pytest.approx(2400 / 1e6 * 1.0 + 400 / 1e6 * 5.0)


def test_cache_writes_and_reads_use_their_own_multipliers() -> None:
    cost = usage_cost_usd(
        HAIKU_4_5,
        _Usage(cache_creation_input_tokens=1_000_000, cache_read_input_tokens=1_000_000),
    )
    # 1.25x + 0.1x of the $1 input rate
    assert cost == pytest.approx(1.35)


def test_missing_cache_fields_are_treated_as_zero() -> None:
    """The SDK leaves these None when caching is not in play — importer/ficha don't use it."""
    assert usage_cost_usd(HAIKU_4_5, _Usage(input_tokens=1000, output_tokens=0)) == pytest.approx(0.001)


def test_accepts_a_plain_dict_usage() -> None:
    assert usage_cost_usd(HAIKU_4_5, {'input_tokens': 1000, 'output_tokens': 200}) == pytest.approx(
        0.001 + 0.001
    )


def test_unknown_model_costs_zero_instead_of_crashing() -> None:
    """A model swap must never take down ficha generation over a missing price row."""
    assert usage_cost_usd('some-future-model', _Usage(input_tokens=1_000_000)) == 0.0


def test_no_usage_object_costs_zero() -> None:
    assert usage_cost_usd(HAIKU_4_5, None) == 0.0


# ── write path ───────────────────────────────────────────────────────────────


class _CapturingSupabase:
    def __init__(self, *, blow_up: bool = False) -> None:
        self.rows: list[dict[str, Any]] = []
        self._blow_up = blow_up

    def table(self, name: str) -> '_CapturingSupabase':
        assert name == 'llm_usage'
        return self

    def insert(self, payload: dict[str, Any]) -> '_CapturingSupabase':
        self._pending = payload
        return self

    async def execute(self) -> Any:
        if self._blow_up:
            raise RuntimeError('relation "llm_usage" does not exist')
        self.rows.append(self._pending)
        return type('_Res', (), {'data': [self._pending]})()


async def test_records_the_call_with_its_computed_cost() -> None:
    sb = _CapturingSupabase()
    await record_llm_usage(
        sb, scope='ficha_propio', model=HAIKU_4_5,
        usage=_Usage(input_tokens=2000, output_tokens=500),
        property_id='prop-1', url='https://zonaprop/x',
    )

    row = sb.rows[-1]
    assert row['scope'] == 'ficha_propio'
    assert row['model'] == HAIKU_4_5
    assert row['input_tokens'] == 2000
    assert row['output_tokens'] == 500
    assert row['property_id'] == 'prop-1'
    assert row['url'] == 'https://zonaprop/x'
    assert row['cost_usd'] == pytest.approx(2000 / 1e6 + 500 / 1e6 * 5.0)


async def test_write_failure_never_propagates() -> None:
    """Accounting must not be able to break ficha generation — it is a side ledger."""
    sb = _CapturingSupabase(blow_up=True)
    await record_llm_usage(sb, scope='ficha_propio', model=HAIKU_4_5, usage=_Usage(input_tokens=1))
    assert sb.rows == []


async def test_no_supabase_is_a_noop() -> None:
    await record_llm_usage(None, scope='ficha_propio', model=HAIKU_4_5, usage=_Usage(input_tokens=1))


# ── job attribution ──────────────────────────────────────────────────────────
#
# Without `job_id` the ledger can price a call but cannot answer "what did THIS
# search cost me in tokens". `property_id` is not a substitute: the properties
# dedup index (direccion, precio, tipo_operacion) leaves a re-scraped listing
# attached to the job that first saw it, so per-job spend derived from it drifts.


async def test_records_the_job_that_paid_for_the_call() -> None:
    sb = _CapturingSupabase()
    await record_llm_usage(
        sb, scope=SCOPE_SEARCH_PARSE, model=HAIKU_4_5,
        usage=_Usage(input_tokens=300, output_tokens=80),
        job_id='job-42',
    )
    assert sb.rows[-1]['job_id'] == 'job-42'


async def test_job_id_is_null_when_the_call_belongs_to_no_search() -> None:
    """CRM-side calls (ficha propio, /properties/match) are not part of any job."""
    sb = _CapturingSupabase()
    await record_llm_usage(
        sb, scope=SCOPE_FICHA_PROPIO, model=HAIKU_4_5, usage=_Usage(input_tokens=10),
    )
    assert sb.rows[-1]['job_id'] is None


# ── scope taxonomy ───────────────────────────────────────────────────────────


def test_search_scopes_never_collide_with_the_ficha_propio_counter() -> None:
    """`GET /properties/ficha-propio/stats` sums `cost_usd` WHERE scope='ficha_propio'.

    Any newly instrumented call site that reused that scope would silently inflate
    the CRM counter, which is exactly the bug this taxonomy exists to prevent.
    """
    assert SCOPE_FICHA_PROPIO not in SEARCH_SCOPES
    assert SCOPE_FICHA_ENRICH not in SEARCH_SCOPES


def test_every_search_side_call_site_has_its_own_scope() -> None:
    """One scope per call site — otherwise per-source spend can't be broken out."""
    assert SEARCH_SCOPES == frozenset({
        SCOPE_SEARCH_PARSE,
        SCOPE_MATCH_PARSE,
        SCOPE_EXTRACT_WEBSITE,
        SCOPE_EXTRACT_INSTAGRAM,
    })
    assert len(SEARCH_SCOPES) == 4
