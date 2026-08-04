"""Anthropic token spend, priced and persisted.

Ficha Propio does NOT cost Apify credits — the portal page is fetched with plain
httpx. What it costs is Anthropic tokens: `importer._extract_llm` runs Haiku over
the page text, and `ficha.enrich_ficha` runs it again over the description. This
module turns the SDK's `msg.usage` into dollars and books it, so the CRM counter
is derived from real per-call usage rather than an estimate.

Prices are per MILLION tokens, from the Anthropic pricing table. They are the
one thing here that goes stale: when the model changes, add its row.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

HAIKU_4_5 = 'claude-haiku-4-5-20251001'

# {model: (usd per 1M input, usd per 1M output)}
_PRICING: dict[str, tuple[float, float]] = {
    HAIKU_4_5: (1.00, 5.00),
    'claude-haiku-4-5': (1.00, 5.00),
}

# Cache writes bill above the input rate, cache reads far below it. Neither path
# is used today (no cache_control on the ficha calls) — priced so that turning
# caching on later doesn't silently under-report.
_CACHE_WRITE_MULTIPLIER = 1.25
_CACHE_READ_MULTIPLIER = 0.10


def _field(usage: Any, name: str) -> int:
    """Read a token count off the SDK's usage object or a plain dict.

    The SDK leaves the cache fields as `None` when caching isn't in play, so a
    missing value and a zero must collapse to the same thing.
    """
    value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
    return int(value or 0)


def usage_cost_usd(model: str, usage: Any) -> float:
    """USD billed for one Anthropic call. Unknown model → 0.0, never an exception:
    a model swap must not be able to take down ficha generation."""
    if usage is None:
        return 0.0
    price = _PRICING.get(model)
    if price is None:
        logger.warning('no pricing row for model %s — booking 0.0', model)
        return 0.0

    per_input, per_output = price
    billable_input = (
        _field(usage, 'input_tokens')
        + _field(usage, 'cache_creation_input_tokens') * _CACHE_WRITE_MULTIPLIER
        + _field(usage, 'cache_read_input_tokens') * _CACHE_READ_MULTIPLIER
    )
    total = billable_input / 1e6 * per_input + _field(usage, 'output_tokens') / 1e6 * per_output
    return round(total, 8)


async def record_llm_usage(
    sb: Any,
    *,
    scope: str,
    model: str,
    usage: Any,
    property_id: str | None = None,
    url: str | None = None,
) -> None:
    """Book one call into `llm_usage`.

    `scope` is what the stats endpoint filters on: `ficha_propio` for spend that
    belongs to a Ficha Propio, `ficha_enrich` for the same enrichment run against
    a portal-scraped property. Getting that right at the call site is what keeps
    the counter from over-reporting.

    Never raises: this is a side ledger, and losing an accounting row must not
    cost the user the ficha they were generating.
    """
    if sb is None:
        return
    try:
        await sb.table('llm_usage').insert({
            'scope': scope,
            'model': model,
            'input_tokens': _field(usage, 'input_tokens'),
            'output_tokens': _field(usage, 'output_tokens'),
            'cache_creation_input_tokens': _field(usage, 'cache_creation_input_tokens'),
            'cache_read_input_tokens': _field(usage, 'cache_read_input_tokens'),
            'cost_usd': usage_cost_usd(model, usage),
            'property_id': property_id,
            'url': url,
        }).execute()
    except Exception as exc:
        logger.warning('llm_usage write failed (scope=%s): %s', scope, exc)
