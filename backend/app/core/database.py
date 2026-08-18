import logging
from collections.abc import Iterator, Sequence
from urllib.parse import quote
from typing import Any

from app.core.config import settings

log = logging.getLogger(__name__)


async def create_supabase_client() -> Any:
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        log.warning("Supabase credentials not set — running without persistence.")
        return None
    try:
        from supabase import acreate_client  # type: ignore[attr-defined]
        client = await acreate_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
        log.info("Supabase client connected.")
        return client
    except Exception as e:
        log.warning("Supabase client unavailable (%s) — running without persistence.", e)
        return None


async def create_checkpointer() -> Any:
    """Always returns MemorySaver — Postgres checkpointer requires direct DB access."""
    from langgraph.checkpoint.memory import MemorySaver
    return MemorySaver()


# PostgREST puts `.in_(col, values)` in the QUERY STRING, so a long value list
# becomes a long URL. Past ~39 KB of encoded parameter Supabase answers
# `{'message': 'JSON could not be generated', 'code': 400}` — measured by
# bisection: 635 typical addresses (~38 849 B) pass, 636 (~38 908 B) fail.
# The ceiling is BYTES, not item count: 800 real addresses (~30 KB) pass while
# 800 longer ones (~49 KB) do not. Budget well under it — the parameter is not
# the only thing in the URL, and address lengths vary per portal.
IN_FILTER_MAX_BYTES = 16_000


def chunk_for_in_filter(
    values: Sequence[str], max_bytes: int = IN_FILTER_MAX_BYTES,
) -> Iterator[list[str]]:
    """Split `values` into runs whose encoded `in.(...)` payload fits in a URL.

    Callers loop over the chunks and concatenate the results — every value is
    yielded exactly once, in order. A single value longer than the budget is
    emitted on its own rather than dropped: an over-long URL surfaces as a
    logged error, while a silently skipped row does not.
    """
    chunk: list[str] = []
    size = 0
    for value in values:
        # `"<value>",` — quotes plus separator, percent-encoded as PostgREST does.
        cost = len(quote(f'"{value}",'))
        if chunk and size + cost > max_bytes:
            yield chunk
            chunk, size = [], 0
        chunk.append(value)
        size += cost
    if chunk:
        yield chunk
