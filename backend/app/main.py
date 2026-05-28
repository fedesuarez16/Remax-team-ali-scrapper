from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from arq import create_pool
from arq.connections import RedisSettings

from app.core.config import settings
from app.core.database import create_db_pool, create_checkpointer
from app.api.v1.router import api_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    app.state.db_pool = await create_db_pool()
    app.state.checkpointer = await create_checkpointer()
    app.state.arq = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    yield
    await app.state.arq.aclose()
    await app.state.checkpointer.__aexit__(None, None, None)
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
