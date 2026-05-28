from arq.connections import RedisSettings
from app.core.config import settings


async def startup(ctx: dict[str, object]) -> None: ...


async def shutdown(ctx: dict[str, object]) -> None: ...


class WorkerSettings:
    functions: list[object] = []  # populated by EP-01 (apify_scraper, extraction)
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
