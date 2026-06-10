import logging
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
