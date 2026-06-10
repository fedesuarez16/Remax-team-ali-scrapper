from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.memory import MemorySaver

from app.core.config import settings
from app.core.database import create_db_pool, create_checkpointer
from app.api.v1.router import api_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    app.state.db_pool = await create_db_pool()
    pg_checkpointer = await create_checkpointer()
    # Use Postgres checkpointer when DB is available, MemorySaver otherwise.
    # MemorySaver is fine for dev/mock — state is lost on server restart.
    app.state.checkpointer = pg_checkpointer if pg_checkpointer is not None else MemorySaver()
    yield
    if pg_checkpointer is not None:
        await pg_checkpointer.__aexit__(None, None, None)  # type: ignore[attr-defined]
    if app.state.db_pool is not None:
        await app.state.db_pool.close()


app = FastAPI(title="multi-agent-realstate API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0"}
