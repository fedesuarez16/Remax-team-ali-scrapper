import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import create_supabase_client, create_checkpointer
from app.api.v1.router import api_router
from app.services.cleaner import scheduler_loop as cleanup_scheduler_loop


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    app.state.supabase = await create_supabase_client()
    app.state.checkpointer = await create_checkpointer()
    # BOT LIMPIADOR: latido de fondo que dispara la limpieza programada cuando
    # toca. El intervalo real ("cada X días") lo decide `last_run_at` en la
    # base, no este loop — reiniciar el backend no adelanta ni pierde una
    # limpieza. Arranca apagado hasta que se habilite desde /limpieza.
    app.state.cleanup_scheduler = asyncio.ensure_future(
        cleanup_scheduler_loop(lambda: app.state.supabase)
    )
    yield
    task = app.state.cleanup_scheduler
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    sb = app.state.supabase
    if sb is not None and hasattr(sb, 'aclose'):
        await sb.aclose()


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


# Same probe on `/`. Railway reads `healthcheckPath` from the railway.toml
# inside the service's Root Directory; when that file is not picked up the
# default probe hits `/`, and a container serving only `/health` fails the
# deploy with "Failed to connect before the deadline" while being perfectly
# healthy. Touches nothing — no Supabase, no state — so it answers regardless
# of what the lifespan managed to connect to.
@app.get("/")
async def root() -> dict[str, str]:
    return await health()
