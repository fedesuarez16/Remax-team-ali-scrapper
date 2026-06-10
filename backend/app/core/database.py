import logging
from typing import Any

import asyncpg  # type: ignore[import-untyped]
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from app.core.config import settings

log = logging.getLogger(__name__)


async def create_db_pool() -> Any:
    if not settings.DATABASE_URL:
        return None
    try:
        pool = await asyncpg.create_pool(dsn=settings.DATABASE_URL, min_size=1, max_size=10)
        return pool
    except Exception as e:
        log.warning("DB pool unavailable (%s) — running without persistence.", e)
        return None


async def create_checkpointer() -> AsyncPostgresSaver | None:
    dsn = settings.CHECKPOINTER_DSN or settings.DATABASE_URL
    if not dsn:
        return None
    try:
        cm = AsyncPostgresSaver.from_conn_string(dsn)
        saver = await cm.__aenter__()
        await saver.setup()
        return saver
    except Exception as e:
        log.warning("Postgres checkpointer unavailable (%s) — falling back to MemorySaver.", e)
        return None
