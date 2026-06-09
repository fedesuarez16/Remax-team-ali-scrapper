from typing import Any

import asyncpg  # type: ignore[import-untyped]
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from app.core.config import settings


async def create_db_pool() -> Any:
    if not settings.DATABASE_URL:
        return None
    pool = await asyncpg.create_pool(dsn=settings.DATABASE_URL, min_size=1, max_size=10)
    return pool


async def create_checkpointer() -> AsyncPostgresSaver | None:
    dsn = settings.CHECKPOINTER_DSN or settings.DATABASE_URL
    if not dsn:
        return None
    cm = AsyncPostgresSaver.from_conn_string(dsn)
    saver = await cm.__aenter__()
    await saver.setup()
    return saver
