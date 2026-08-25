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
    APIFY_DISABLED: bool = False         # skip all Apify actors (zonaprop, googlemaps, instagram) — only direct scrapers (mercadolibre, inmobusqueda, mudafy)
    SCRAPE_ZONAPROP_ONLY: bool = False   # test mode: only fan out ZonaProp, skip ML + agencies
    SCRAPE_GOOGLEMAPS_ONLY: bool = False  # test mode: agencies-only — skip portal + Instagram scrapers
    MAX_WEBSITE_URLS: int = 0            # 0 = NO CAP: scrape every selected agency/curated website
    LOG_LEVEL: str = 'INFO'              # `app.*` logger level — INFO surfaces the scrape funnels
    # ── Portal paging depth ───────────────────────────────────────────────────
    # `0` means NO CAP on every source below: page until the portal itself runs
    # out (totalPages / an empty page / a short page / a page with nothing new).
    # These used to ship with low defaults, which is why a search topped out at
    # exactly 100 RE/MAX items (5 pages × 20) — a self-imposed ceiling, never a
    # portal constraint. Set a positive value to re-cap a source (cost/latency).
    ZONAPROP_MAX_RESULTS: int = 0        # items per ZonaProp search; PAID — one Apify actor run PER PAGE
    ARGENPROP_MAX_PAGES: int = 0         # 0 = the robots.txt ceiling (Allow: pagina-1..pagina-10)
    REMAX_MAX_PAGES: int = 0             # pages per RE/MAX search (API serves 3300+; verified live)
    REMAX_PAGE_SIZE: int = 200           # items per RE/MAX API page — 200 is the max the API honours
    REMAX_UNLOCATED_MAX_PAGES: int = 15  # ceiling ONLY for the nationwide fallback (zona unresolved)
    MERCADOLIBRE_MAX_PAGES: int = 0      # pages per MercadoLibre search (50 items each)
    INMOBUSQUEDA_MAX_PAGES: int = 0      # pages per InmoBusqueda search (15 items each)
    MUDAFY_MAX_PAGES: int = 0            # pages per Mudafy search (25 items each)
    # `0` = NO CAP on both actors below: the input key is OMITTED so the actor
    # runs to its own exhaustion. These are PAID per result — a positive value
    # re-caps the spend per zona/profile.
    GOOGLEMAPS_MAX_PLACES: int = 0       # places per zona in agency discovery; PAID per place
    INSTAGRAM_RESULTS_LIMIT: int = 0     # posts per agency profile; PAID per post
    AGENCY_CACHE_TTL_DAYS: int = 30      # reuse cached agencies per zona within N days (skip paid Google Maps actor)
    # MercadoLibre bloquea por IP de DATACENTER y lo hace con un 200: misma URL
    # y mismos headers devuelven 1.98 MB de listado desde una IP residencial y
    # 39 KB de /gz/account-verification desde una de datacenter (medido en vivo,
    # 2026-08-20). Railway sale por datacenter, así que producción reportaba
    # `0 propiedades` en TODA búsqueda mientras local traía 96.
    # Formato Apify: http://groups-RESIDENTIAL:<proxy_password>@proxy.apify.com:8000
    # (la password del proxy NO es el APIFY_API_TOKEN — está en Apify → Proxy).
    # Vacío = salida directa, que es lo correcto en local: el tráfico residencial
    # se factura por GB y una página de ML pesa ~2 MB.
    SCRAPER_PROXY_URL: str = ""
    YCLOUD_API_KEY: str = ""
    ALLOWED_ORIGINS: str = "http://localhost:3000"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]


settings = Settings()  # type: ignore[call-arg]
