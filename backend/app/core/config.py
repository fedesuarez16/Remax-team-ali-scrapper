from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    SUPABASE_JWT_SECRET: str = ""
    DATABASE_URL: str = ""               # asyncpg dsn (app pool)
    CHECKPOINTER_DSN: str = ""            # psycopg dsn; falls back to DATABASE_URL
    REDIS_URL: str = "redis://localhost:6379"
    ANTHROPIC_API_KEY: str = ""
    APIFY_API_TOKEN: str = ""
    APIFY_USE_MOCK: bool = True
    APIFY_DISABLED: bool = False         # skip all Apify actors (zonaprop, googlemaps, instagram) — only direct scrapers (mercadolibre)
    SCRAPE_ZONAPROP_ONLY: bool = False   # test mode: only fan out ZonaProp, skip ML + agencies
    SCRAPE_GOOGLEMAPS_ONLY: bool = False  # test mode: agencies-only — skip portal + Instagram scrapers
    MAX_WEBSITE_URLS: int = 10           # cap agency websites scraped per search
    ZONAPROP_MAX_RESULTS: int = 200      # cap items per ZonaProp run; 0 = no cap (actor default)
    GOOGLEMAPS_MAX_PLACES: int = 20      # cap billed places per zona in agency discovery (raise after testing)
    INSTAGRAM_RESULTS_LIMIT: int = 10    # cap posts per agency profile (raise after testing)
    AGENCY_CACHE_TTL_DAYS: int = 30      # reuse cached agencies per zona within N days (skip paid Google Maps actor)
    YCLOUD_API_KEY: str = ""
    ALLOWED_ORIGINS: str = "http://localhost:3000"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]


settings = Settings()  # type: ignore[call-arg]
