from typing import Any

import asyncpg  # type: ignore[import-untyped]
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from app.core.config import settings


async def create_db_pool() -> Any:
    pool = await asyncpg.create_pool(dsn=settings.DATABASE_URL, min_size=1, max_size=10)
    if pool is None:
        raise RuntimeError("Failed to create asyncpg pool")
    return pool


async def create_checkpointer() -> AsyncPostgresSaver:
    dsn = settings.CHECKPOINTER_DSN or settings.DATABASE_URL
    # AsyncPostgresSaver is an async context manager; we enter it and keep it open.
    cm = AsyncPostgresSaver.from_conn_string(dsn)
    saver = await cm.__aenter__()
    await saver.setup()  # creates checkpoint tables on first run (idempotent)
    return saver
