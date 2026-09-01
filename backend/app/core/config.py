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
    APIFY_DISABLED: bool = False         # skip los actores de Apify (googlemaps, instagram) — quedan los scrapers directos (zonaprop, mercadolibre, inmobusqueda, mudafy)
    SCRAPE_ZONAPROP_ONLY: bool = False   # test mode: only fan out ZonaProp, skip ML + agencies
    SCRAPE_GOOGLEMAPS_ONLY: bool = False  # test mode: agencies-only — skip portal + Instagram scrapers
    MAX_WEBSITE_URLS: int = 0            # 0 = NO CAP: scrape every selected agency/curated website
    # ── Fase 2: fan-out de inmobiliarias ──────────────────────────────────────
    # Cuántos sitios de inmobiliarias se scrapean EN PARALELO. Sin tope, una
    # búsqueda con 260 inmobiliarias tildadas abría ~1500 requests HTTP de una
    # (cada sitio son 1 home + hasta 5 sub-páginas) y el proceso se quedaba sin
    # sockets antes de terminar. El tope NO recorta resultados: sólo pone los
    # sitios en fila.
    WEBSITE_SCRAPE_CONCURRENCY: int = 12
    # Los sitios de inmobiliarias salen por el MISMO proxy residencial que los
    # portales (`SCRAPER_PROXY_URL`) y con UA de browser real. Antes iban con
    # `PropSearchBot/1.0` y salida directa: desde Railway (datacenter) eso es
    # un 403 o un HTML vacío en la mayoría de los sitios, y la búsqueda volvía
    # sin una sola propiedad sin decir por qué.
    #
    # Renderizar galerías con Chromium POR SITIO era lo que colgaba y lo que
    # gastaba: un Chromium por inmobiliaria, hasta 6 páginas con
    # `wait_until='networkidle'` (25 s cada una) y TODAS las imágenes bajadas
    # por el proxio residencial, que se factura por GB. Con 260 inmobiliarias
    # eran 260 Chromiums y decenas de GB. Las fotos ya salen del HTML
    # (`og:image` + `<img>`) y las fichas que quedan cortas las recupera
    # `harvest_page_images`, que rinde con presupuesto acotado.
    WEBSITE_RENDER_GALLERIES: bool = False
    # Segundos por request a un sitio de inmobiliaria. Por proxy residencial
    # una home tarda más que directo, pero un sitio muerto no puede comerse
    # minutos: son `1 + WEBSITE_MAX_SUBPAGES` requests por sitio.
    WEBSITE_HTTP_TIMEOUT: float = 20.0
    WEBSITE_MAX_SUBPAGES: int = 5
    # Llamadas de extracción al LLM en paralelo. El bucle era SECUENCIAL: 1500
    # páginas × ~4 s = más de una hora con el stream abierto, que es lo que
    # terminaba muriendo. 8 en paralelo lo bajan a minutos.
    WEBSITE_EXTRACT_CONCURRENCY: int = 8
    # Segundos máximos por llamada de extracción: una que se cuelga no puede
    # frenar la búsqueda entera.
    WEBSITE_EXTRACT_TIMEOUT: float = 90.0
    # Cada cuántos segundos de silencio el stream manda un frame de keepalive.
    # Railway/Vercel cortan una conexión ociosa y el cliente lo ve como "error".
    SSE_KEEPALIVE_SECONDS: float = 15.0
    LOG_LEVEL: str = 'INFO'              # `app.*` logger level — INFO surfaces the scrape funnels
    # ── Portal paging depth ───────────────────────────────────────────────────
    # `0` means NO CAP on every source below: page until the portal itself runs
    # out (totalPages / an empty page / a short page / a page with nothing new).
    # These used to ship with low defaults, which is why a search topped out at
    # exactly 100 RE/MAX items (5 pages × 20) — a self-imposed ceiling, never a
    # portal constraint. Set a positive value to re-cap a source (cost/latency).
    # ZonaProp se lee DIRECTO (HTML + __PRELOADED_STATE__ vía SCRAPER_PROXY_URL).
    # `true` devuelve la fuente al actor de Apify `crawlerbros/zonaprop-scraper`,
    # que quedó intacto: su browser de Playwright crashea enriqueciendo fichas
    # y eso le trunca la paginación ("Reached last page (1)"), pero sirve como
    # marcha atrás si el scraping directo se rompe.
    ZONAPROP_USE_APIFY: bool = False
    # Avisos por búsqueda de ZonaProp (30 por página → ~27 páginas). El costo ya
    # no son runs pagos de Apify sino ancho de banda del proxy residencial y,
    # sobre todo, TIEMPO: ~1.3 MB y ~4 s por página, medido en producción. Sin
    # tope, una búsqueda de La Plata son 67 páginas ≈ 87 MB y 4½ minutos sólo
    # de este portal, en paralelo con los otros seis. 0 = sin tope.
    ZONAPROP_MAX_RESULTS: int = 800
    ARGENPROP_MAX_PAGES: int = 0         # 0 = the robots.txt ceiling (Allow: pagina-1..pagina-10)
    REMAX_MAX_PAGES: int = 0             # pages per RE/MAX search (API serves 3300+; verified live)
    REMAX_PAGE_SIZE: int = 200           # items per RE/MAX API page — 200 is the max the API honours
    REMAX_UNLOCATED_MAX_PAGES: int = 15  # ceiling ONLY for the nationwide fallback (zona unresolved)
    MERCADOLIBRE_MAX_PAGES: int = 0      # páginas por búsqueda de MercadoLibre (48 tarjetas c/u, ~2 MB por página vía proxy residencial). 0 = sin tope.
    INMOBUSQUEDA_MAX_PAGES: int = 0      # pages per InmoBusqueda search (15 items each)
    MUDAFY_MAX_PAGES: int = 0            # pages per Mudafy search (25 items each)
    # `0` = NO CAP on both actors below: the input key is OMITTED so the actor
    # runs to its own exhaustion. These are PAID per result — a positive value
    # re-caps the spend per zona/profile.
    #
    # El cap de Google Maps es un TOPE DE GASTO expresado en la única unidad que
    # el actor entiende. `compass/crawler-google-places` cobra USD 1.50 / 1.000
    # lugares (pay-per-event; verificado en apify.com/compass/crawler-google-places
    # el 2026-09-01), o sea USD 0.0015 por lugar:
    #
    #     200 lugares x 0.0015 = USD 0.30 por zona
    #
    # En `0` una zona grande fijaba el gasto por su cuenta y nosotros nos
    # enterábamos al cerrar el job. Los 200 dejan los otros ~0.70 del
    # presupuesto de la búsqueda para los runs de Instagram, que vienen después
    # y son uno POR inmobiliaria: el que se queda sin nafta ahí es el track
    # entero, no una rama.
    GOOGLEMAPS_MAX_PLACES: int = 200     # places per zona in agency discovery; PAID per place (~USD 0.30)
    # Techo de gasto de TODA la búsqueda, sumando los dos actores. El cap de
    # arriba acota un run; éste acota el total, que es lo que aparece en la
    # factura: el track abre un run de Instagram POR inmobiliaria con handle, y
    # ahí el número de runs lo pone la zona, no nosotros.
    #
    # Es un tope BLANDO: se consulta ANTES de arrancar cada run y no arranca
    # ninguno nuevo una vez alcanzado, pero un run ya en vuelo termina. O sea
    # que el total puede pasarse por lo que cueste ese run. Frenar en seco
    # exigiría abortarlo en Apify y leer el dataset parcial.
    # `0` = sin tope, misma convención que el resto de los knobs.
    APIFY_MAX_USD_PER_SEARCH: float = 1.0
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
