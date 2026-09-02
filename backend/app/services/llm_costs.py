"""Anthropic token spend, priced and persisted.

Ficha Propio does NOT cost Apify credits — the portal page is fetched with plain
httpx. What it costs is Anthropic tokens: `importer._extract_llm` runs Haiku over
the page text, and `ficha.enrich_ficha` runs it again over the description. This
module turns the SDK's `msg.usage` into dollars and books it, so the CRM counter
is derived from real per-call usage rather than an estimate.

The search side is booked here too: query parsing, criteria parsing, and the
per-page / per-post extraction loops. Those loops are where token spend actually
concentrates, so leaving them out made total LLM cost read as a fraction of the
real number.

Prices are per MILLION tokens, from the Anthropic pricing table. They are the
one thing here that goes stale: when the model changes, add its row.
"""
from __future__ import annotations

import contextvars
import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

HAIKU_4_5 = 'claude-haiku-4-5-20251001'

# ── scopes ───────────────────────────────────────────────────────────────────
#
# One scope per call site, decided AT the call site. This taxonomy is load-bearing
# twice over: `GET /properties/ficha-propio/stats` sums `cost_usd` filtered to
# SCOPE_FICHA_PROPIO, so any call site reusing that scope silently inflates the
# CRM counter; and the cost dashboard breaks spend down by scope, which collapses
# the moment two different call sites share one.

# CRM side — not part of any search, so these rows carry job_id = NULL.
SCOPE_FICHA_PROPIO = 'ficha_propio'      # importer._extract_llm
SCOPE_FICHA_ENRICH = 'ficha_enrich'      # ficha.enrich_ficha

# Search side — one call per search.
SCOPE_SEARCH_PARSE = 'search_parse'      # nodes.parse_query
SCOPE_MATCH_PARSE = 'match_parse'        # matcher._parse_criteria

# Search side — one call per scraped page/post. These dominate token spend.
SCOPE_EXTRACT_WEBSITE = 'extract_website'        # nodes.extract_website_properties_llm
SCOPE_EXTRACT_INSTAGRAM = 'extract_instagram'    # nodes.extract_instagram_properties_llm

SEARCH_SCOPES = frozenset({
    SCOPE_SEARCH_PARSE,
    SCOPE_MATCH_PARSE,
    SCOPE_EXTRACT_WEBSITE,
    SCOPE_EXTRACT_INSTAGRAM,
})

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


# ── Presupuesto por búsqueda ─────────────────────────────────────────────────
#
# `record_llm_usage` anota contra la BASE: sirve para la factura de ayer, no
# para frenar la de hoy. Este contador vive en memoria y se consulta ANTES de
# cada llamada.
#
# En un ContextVar y no en el estado del grafo por lo mismo que el ledger de
# Apify: el loop de extracción abre cientos de tareas hijas y todas heredan el
# mismo dict, así que el fan-out entero suma en un solo lugar.
#
# Forma: {scope: usd}. Por scope y no un total pelado para poder decir QUÉ se
# llevó el presupuesto cuando se agota.

_LLM_LEDGER: contextvars.ContextVar[dict[str, float] | None] = contextvars.ContextVar(
    'llm_cost_ledger', default=None,
)


@contextmanager
def use_llm_ledger(ledger: dict[str, float]) -> Iterator[None]:
    """Anota en `ledger` todo lo que gaste el LLM dentro de este bloque."""
    token = _LLM_LEDGER.set(ledger)
    try:
        yield
    finally:
        _LLM_LEDGER.reset(token)


def book_llm_cost(scope: str, usd: float) -> None:
    """Suma una llamada. No-op fuera de una búsqueda (caminos de CRM)."""
    ledger = _LLM_LEDGER.get()
    if ledger is None:
        return
    ledger[scope] = round(ledger.get(scope, 0.0) + float(usd or 0.0), 8)


def llm_total_usd(ledger: Mapping[str, float]) -> float:
    return round(sum(float(v or 0.0) for v in ledger.values()), 6)


def llm_budget_exhausted() -> bool:
    """¿Esta búsqueda ya gastó su presupuesto de tokens?

    Devuelve un bool y no levanta a propósito. El loop de extracción corre
    ~1500 llamadas en un `asyncio.gather`: mil quinientas excepciones serían
    ruido, no información. La página que encuentra el presupuesto agotado
    devuelve vacío y sale.

    Sin ledger instalado no estamos en una búsqueda (ficha, importer): ahí no
    hay contra qué acumular, y leerlo como presupuesto agotado dejaría a esos
    caminos sin poder hacer una sola llamada.
    """
    from app.core.config import settings
    cap = float(settings.LLM_MAX_USD_PER_SEARCH or 0.0)
    ledger = _LLM_LEDGER.get()
    if cap <= 0 or ledger is None:
        return False
    return llm_total_usd(ledger) >= cap


async def record_llm_usage(
    sb: Any,
    *,
    scope: str,
    model: str,
    usage: Any,
    property_id: str | None = None,
    job_id: str | None = None,
    url: str | None = None,
) -> None:
    """Book one call into `llm_usage`.

    `scope` must be one of the constants above, chosen at the call site — see the
    taxonomy note there for why picking the wrong one corrupts the CRM counter.

    `job_id` is the search that paid for the call, and is what makes cost-per-search
    answerable. Pass None for CRM-side calls that belong to no job. Do NOT rely on
    `property_id` as a proxy: the properties dedup index leaves a re-scraped listing
    attached to the job that first saw it.

    Call this for every call that REACHED Anthropic, including ones whose output
    turned out unusable (no tool_use block, fell back to a heuristic) — those were
    billed all the same. Skip it only when the request raised, since a failed call
    has no usage object and would book a phantom zero-cost row.

    Never raises: this is a side ledger, and losing an accounting row must not
    cost the user the ficha or the search they were running.
    """
    # ANTES del corte por `sb`: el conteo en memoria es lo que sostiene el tope,
    # y si viviera después de ese return una instalación sin Supabase gastaría
    # sin techo y sin enterarse.
    book_llm_cost(scope, usage_cost_usd(model, usage))

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
            'job_id': job_id,
            'url': url,
        }).execute()
    except Exception as exc:
        logger.warning('llm_usage write failed (scope=%s): %s', scope, exc)
