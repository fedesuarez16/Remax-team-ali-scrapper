from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import random
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from itertools import count
from typing import Any, Awaitable, Callable, Iterable, Iterator, Mapping

import httpx

from app.models.property import Agency, RawProperty, ScrapingFilters
from app.services.zona import zona_candidates

ProgressCb = Callable[[str, str, int], Awaitable[None]]

logger = logging.getLogger(__name__)

PORTAL_SOURCES = ('zonaprop', 'mercadolibre', 'argenprop', 'remax', 'inmobusqueda', 'mudafy')   # phase-1 portal scrapers
SOURCES = (*PORTAL_SOURCES, 'googlemaps', 'instagram')

# ── Actor IDs ─────────────────────────────────────────────────────────────────
_ACTORS: dict[str, str] = {
    'zonaprop':   'crawlerbros~zonaprop-scraper',
    'argenprop':  'apify~website-content-crawler',
    'googlemaps': 'compass~crawler-google-places',
    'instagram':  'apify~instagram-post-scraper',
    'website':    'apify~website-content-crawler',
}

# ── MercadoLibre: listing HTML, NOT the REST API ──────────────────────────────
# `api.mercadolibre.com` answers 403 to everything — `/sites/MLA/search`,
# `/items/{id}` and even `/sites/MLA` — with a REAL application token
# (client_credentials → HTTP 200, scope `read`), and the DevCenter offers no
# catalogue permission to enable. Verified live; the constants for that API are
# gone rather than left around to look usable. See `_scrape_mercadolibre`.
# Paging depth is `settings.MERCADOLIBRE_MAX_PAGES` (0 = no cap): page until a
# page yields nothing new.

# ── RE/MAX public REST API (no Apify) — same undocumented-but-open API its own
# Angular frontend calls (confirmed via live requests, no auth required) ──────
_REMAX_API_BASE = 'https://api-ar.redremax.com/remaxweb-ar/api'
# Paging depth is a cost/latency knob, not a portal constraint (RE/MAX serves
# far more than 5 pages) — see `settings.REMAX_MAX_PAGES` / `REMAX_PAGE_SIZE`.
# Unlike Argenprop there is no robots.txt page ceiling, so `0` means uncapped:
# page until `totalPages` is exhausted.
# Location autocomplete (what remax.com.ar's own search box calls — public,
# verified live) + resolved `locations` filter cache.
_REMAX_LOCATION_CACHE: dict[str, str | None] = {}
# `locations` is `in:` + 7 colon slots; the id goes in the slot matching the
# autocomplete result's `level` (0-based index == level). Which id field to
# read also depends on the level. Reverse-engineered from the Angular
# frontend's own request and verified live: level 3 (city) slot 4 →
# `in::::1067:::` returns Manuel B Gonnet listings; level 4 (neighborhood)
# slot 5 → `in:::::5::` returns Las Cañitas listings. The `@label` suffix the
# frontend appends is ignored by the server.
_REMAX_LEVEL_ID_FIELD: dict[int, str] = {
    2: 'countyId', 3: 'cityId', 4: 'neighborhoodId', 5: 'privatecommunityId',
}
_REMAX_LOCATION_SLOTS = 7

# id → value from GET {_REMAX_API_BASE}/listingTypes/findAll (relevé el catálogo
# completo en vivo). typeId query values, no reverse-engineered guess.
_REMAX_TYPE_IDS: dict[str, tuple[int, ...]] = {
    'departamento': (1, 2, 3, 4, 5, 6, 7, 8),
    'ph': (12,),
    'casa': (9, 10, 11),
    'terreno': (18, 19, 23, 26),
    'local': (17, 20),
    'oficina': (16, 27),
    'otro': (13, 14, 15, 21, 22, 28),
}
_REMAX_TYPE_VALUE_TO_TIPO: dict[str, str] = {
    'departamento_duplex': 'departamento', 'departamento_estandar': 'departamento',
    'departamento_loft': 'departamento', 'departamento_monoambiente': 'departamento',
    'departamento_penthouse': 'departamento', 'departamento_piso': 'departamento',
    'departamento_semipiso': 'departamento', 'departamento_triplex': 'departamento',
    'ph': 'ph',
    'casa': 'casa', 'casa_duplex': 'casa', 'casa_triplex': 'casa',
    'terrenos_y_lotes': 'terreno', 'campo': 'terreno', 'quinta': 'terreno', 'chacra': 'terreno',
    'local': 'local', 'fondo_de_comercio': 'local',
    'oficina': 'oficina', 'consultorio': 'oficina',
}

# ── Argenprop — behind AWS WAF Bot Control (verified: httpx and default
# headless Chromium both get challenged after the first request), crawled via
# Apify's generic website-content-crawler actor instead, which does get past
# it. `?pagina-N` pagination confirmed from real hrefs; robots.txt only
# `Allow`s pagina-1..pagina-10, hence the hard cap. ─────────────────────────
_ARGENPROP_BASE = 'https://www.argenprop.com'
_ARGENPROP_MAX_PAGES_HARD = 10
# The portal's own location-autocomplete API (what argenprop.com's search box
# calls) — public, NOT behind the WAF, verified live. Resolves free-text zonas
# to Argenprop's canonical slugs ("Gonnet" → MANUEL-GONNET).
_ARGENPROP_AUTOCOMPLETE_URL = 'https://api.sosiva451.com/Ubicaciones/buscar'
_ARGENPROP_SLUG_CACHE: dict[str, str | None] = {}
_ARGENPROP_URL_SLUG: dict[str, str] = {
    'departamento': 'departamentos',
    'casa': 'casas',
    'ph': 'ph',
    'local': 'locales-comerciales',
    'oficina': 'oficinas',
    'terreno': 'terrenos',
}

_APIFY_BASE = 'https://api.apify.com/v2'
_POLL_INTERVAL = 3.0   # seconds between status checks
_TIMEOUT = 300         # max seconds to wait for a run
# Read budget for one Apify API call. The old flat 30s was killing live
# pagination: a `ReadTimeout` on page 2 discarded a page-1 haul that had
# already been scraped and billed. Connect stays short so a dead host fails
# fast — it is only READING (a dataset of up to `ZONAPROP_MAX_RESULTS` items)
# that deserves the long leash.
_HTTP_TIMEOUT = 120.0
_HTTP_CONNECT_TIMEOUT = 10.0

# ── Per-search spend ledger ────────────────────────────────────────────────────
#
# Apify puts `usageTotalUsd` on every run object; we book it while polling so no
# extra request is needed. The tally CANNOT live on the service instance:
# `get_apify_service()` builds a fresh `ApifyService` on every call and one job
# calls it several times (portales, agencias, instagram). So it lives in a
# ContextVar set once per search — child tasks inherit the same dict object, so
# parallel/nested scrapes all land in one tally.
#
# Shape: {source: {'usd': float, 'runs': int}}. A source that never hit an actor
# (mercadolibre and remax go direct; agency cache hits skip Apify) has NO entry —
# that absence is precisely what makes a free search readable as free.

_COST_LEDGER: contextvars.ContextVar[dict[str, dict[str, Any]] | None] = contextvars.ContextVar(
    'apify_cost_ledger', default=None,
)


@contextmanager
def use_cost_ledger(ledger: dict[str, dict[str, Any]]) -> Iterator[None]:
    """Book every actor run started inside this block into `ledger`.

    The caller owns the dict so it can read the tally from a different task
    (the SSE generator writes the job row, the graph task spends the money).
    """
    token = _COST_LEDGER.set(ledger)
    try:
        yield
    finally:
        _COST_LEDGER.reset(token)


def record_run_cost(source: str, usd: float | None) -> None:
    """Book one finished actor run. No-op outside a search (ficha/importer paths)."""
    ledger = _COST_LEDGER.get()
    if ledger is None:
        return
    entry = ledger.setdefault(source, {'usd': 0.0, 'runs': 0})
    entry['runs'] += 1
    entry['usd'] = round(entry['usd'] + float(usd or 0.0), 6)


def ledger_total_usd(ledger: Mapping[str, Mapping[str, Any]]) -> float:
    """Total USD across sources, rounded to the job column's 4 decimals."""
    return round(sum(float(e.get('usd') or 0.0) for e in ledger.values()), 4)


# ── Normalisation helpers ──────────────────────────────────────────────────────

def _slugify(value: str) -> str:
    """Turn a zona name into a portal-safe URL slug (no accents, commas or parens)."""
    import re
    import unicodedata
    ascii_only = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode()
    cleaned = re.sub(r'[^a-zA-Z0-9\s-]', '', ascii_only)
    slug = re.sub(r'\s+', '-', cleaned.strip().lower())
    return re.sub(r'-+', '-', slug)


def _guard_phrases(filters: ScrapingFilters) -> set[str]:
    """Phrase-set for the ZonaProp redirect guard: the zonas actually REQUESTED,
    never their fallbacks. Map path (localidades present): barrios ∪
    localidades ∪ zona — wide on purpose across the seeds, since the polygon is
    the precision gate downstream. Chat path: ONLY this branch's zona.

    Seeds are deliberately NOT expanded through `zona_candidates`. That
    expansion put the degraded phrase ("La Plata") into the guard of a
    "City Bell, La Plata" search, and `_item_matches_zona` passes on ANY
    phrase — so a nationwide redirect sailed through on its La Plata listings,
    the caller saw a non-empty result, and the composite-slug retry gated on
    `not results` never fired. The guard exists to DETECT that redirect; a
    phrase set wider than the request cannot do it.

    On the chat path the seed is `zona_pedida` — the ORIGINAL request — not the
    candidate `scrape_source` is currently trying. Widening the slug to a page
    the portal actually serves is a retrieval strategy; widening the guard with
    it turned "departamentos en City Bell" into "everything in La Plata". The
    degraded pass now keeps only the barrio's listings off the wider page, or
    honestly returns nothing.
    A composite phrase still requires EVERY comma part — ZonaProp supplies
    them in separate fields (barrio in `neighborhood`, partido in `city`), and
    that is what keeps the San Luis homonym of "Casco Urbano" out.
    """
    if filters.localidades:
        seeds = set(filters.zonas) | set(filters.localidades) | {filters.zona or ''}
    else:
        seeds = {filters.zona_pedida or filters.zona or ''}
    seeds.discard('')
    return seeds


# ── ZonaProp, read directly ───────────────────────────────────────────────────
# Everything below replaces the `crawlerbros/zonaprop-scraper` actor. The actor
# pulls the listing in ~7 s and then opens one DETAIL page per result, ~85 s, and
# that is where its Playwright driver dies:
#
#   TypeError: Cannot read properties of undefined (reading 'url')
#   ... Browser.new_page: Connection closed while reading from the driver  (×19)
#   Pushed 20 listings (total: 20) · Reached last page (1).
#
# A dead browser cannot fetch page 2 either, so the crash silently truncates
# every multi-page search. Its input schema offers no switch to skip the
# enrichment. Set `ZONAPROP_USE_APIFY=true` to fall back to it — the actor path
# is still there, untouched.
#
# Everything we normalise already ships in `window.__PRELOADED_STATE__` on the
# listing page, which `SCRAPER_PROXY_URL` (residential, already configured for
# MercadoLibre) fetches with a plain 200 — measured: 1344 KB, 20 postings.

_ZP_STATE_MARKER = '__PRELOADED_STATE__'
_ZP_BASE = 'https://www.zonaprop.com.ar'


def _zonaprop_state(html: str) -> dict[str, Any] | None:
    """The `__PRELOADED_STATE__` object, or None when the page has none.

    Brace-matched rather than regexed: more script follows the object, so a
    regex either overshoots into it or truncates the JSON. Against the live
    page a regex failed with `Extra data: line 1 column 354022`. String and
    escape state are tracked so a `{` inside a description cannot end it.
    """
    start = html.find(_ZP_STATE_MARKER)
    if start < 0:
        return None
    start = html.find('{', start)
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(html)):
        ch = html[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start:i + 1])  # type: ignore[no-any-return]
                except json.JSONDecodeError:
                    return None
    return None


def _zonaprop_paging(state: Mapping[str, Any]) -> dict[str, Any]:
    """`{total, totalPages, currentPage, ...}` as the portal declares it.

    This is why paging stops being guesswork: no more inferring a page size
    from how many items came back and calling anything shorter the last page.
    """
    paging = (state.get('listStore') or {}).get('paging') or {}
    return dict(paging)


def _zone_id(location_id: Any) -> str:
    """`"V1-D-1001379"` → `"1001379"`, matching the ids in `appliedFilters`."""
    return str(location_id or '').rsplit('-', 1)[-1]


def _zonaprop_applied_zone_ids(state: Mapping[str, Any]) -> set[str]:
    """Which zones the portal says it filtered on.

    The honest version of the redirect guard. `_item_matches_zona` had to infer
    it from listing TEXT, which cost real results — "Grand Bell" and "Lomas de
    City Bell" are inside City Bell and were thrown away for not spelling it.
    Here the portal states the applied filter outright.
    """
    ids: set[str] = set()
    for f in (state.get('listStore') or {}).get('appliedFilters') or []:
        if f.get('type') != 'location':
            continue
        for opt in f.get('options') or []:
            if opt.get('min'):
                ids.add(str(opt['min']))
    return ids


def _zonaprop_applied_zone_labels(state: Mapping[str, Any]) -> set[str]:
    """The NAMES of the zones the portal applied, for redirect detection."""
    labels: set[str] = set()
    for f in (state.get('listStore') or {}).get('appliedFilters') or []:
        if f.get('type') != 'location':
            continue
        for opt in f.get('options') or []:
            if opt.get('label'):
                labels.add(str(opt['label']))
    return labels


def _zonaprop_requested_zone_ids(
    state: Mapping[str, Any], zona: str,
) -> set[str] | None:
    """Of the zones the portal applied, the ones the USER actually asked for.

    `None` means it applied none of them — the slug was unknown and the portal
    answered with somewhere else.

    Taking the UNION of applied zones is wrong, and measurably so. Live,
    `casas-venta-city-bell-la-plata-...` applies TWO filters:

        [{"label": "La Plata",  "type": "city", "min": "1001361"},
         {"label": "City Bell", "type": "zone", "min": "1001379"}]

    and returns 73 listings — Gonnet, Villa Elisa and Miralagos among them.
    Every one of those carries the La Plata city in its parent chain, so a
    union accepts the whole partido for a City Bell search. Only the applied
    option whose LABEL matches the request counts; the containing city is a
    widening the user did not ask for.

    Matching by label rather than by `type` keeps it honest in both
    directions: ask for "La Plata, La Plata" and the city-level filter IS the
    request, so it is kept.
    """
    wanted = _slugify(zona.split(',')[0])
    if not wanted:
        return set()
    ids: set[str] = set()
    for f in (state.get('listStore') or {}).get('appliedFilters') or []:
        if f.get('type') != 'location':
            continue
        for opt in f.get('options') or []:
            if opt.get('min') and wanted in _slugify(str(opt.get('label') or '')):
                ids.add(str(opt['min']))
    if not ids:
        # Nothing declared at all → no evidence either way, keep everything.
        return set() if not _zonaprop_applied_zone_labels(state) else None
    return ids


def _zonaprop_posting_zone_ids(posting: Mapping[str, Any]) -> set[str]:
    """Every zone the posting belongs to, walking `location.parent` upward —
    zona, ciudad, provincia. A sub-barrio therefore carries its containing
    zone's id, which is how Grand Bell survives a City Bell search."""
    ids: set[str] = set()
    node = ((posting.get('postingLocation') or {}).get('location')) or {}
    while isinstance(node, Mapping) and node:
        if node.get('locationId'):
            ids.add(_zone_id(node['locationId']))
        node = node.get('parent') or {}
    return ids


_ZP_REAL_ESTATE_TYPE = {
    'casas': 'casa', 'departamentos': 'departamento', 'ph': 'ph',
    'locales comerciales': 'local', 'oficinas': 'oficina', 'terrenos': 'terreno',
    'quintas': 'casa', 'campos': 'terreno', 'cocheras': 'otro',
    'depositos': 'local', 'galpones': 'local',
}


def _zp_num(value: Any) -> float | None:
    """ZonaProp mixes numbers and numeric strings ("1000", "1.000", 450000)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r'[^0-9,.]', '', str(value)).replace('.', '').replace(',', '.')
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _zp_feature(posting: Mapping[str, Any], label: str) -> float | None:
    for feat in (posting.get('mainFeatures') or {}).values():
        if feat.get('label') == label:
            return _zp_num(feat.get('value'))
    return None


def _norm_zonaprop_posting(posting: Mapping[str, Any]) -> RawProperty | None:
    """One `listPostings` entry → RawProperty, or None without a price."""
    precio = moneda = None
    for op in posting.get('priceOperationTypes') or []:
        for price in op.get('prices') or []:
            if price.get('amount'):
                precio = _zp_num(price['amount'])
                moneda = price.get('currency') or 'USD'
    if precio is None:
        return None

    loc = posting.get('postingLocation') or {}
    barrio = ((loc.get('location') or {}).get('name')) or ''
    direccion = ((loc.get('address') or {}).get('name')) or barrio
    raw_type = ((posting.get('realEstateType') or {}).get('name') or '').strip().lower()
    pics = (posting.get('visiblePictures') or {}).get('pictures') or []

    url = str(posting.get('url') or '')
    return RawProperty(
        fuente='zonaprop',
        titulo=posting.get('title') or None,
        descripcion=posting.get('descriptionNormalized') or None,
        direccion=direccion or barrio,
        precio=precio,
        moneda='ARS' if str(moneda).upper().startswith('$') else 'USD',
        tipo_operacion='venta',
        tipo_propiedad=_ZP_REAL_ESTATE_TYPE.get(raw_type, 'otro'),  # type: ignore[arg-type]
        ambientes=int(a) if (a := _zp_feature(posting, 'Ambientes')) else None,
        banos=int(b) if (b := _zp_feature(posting, 'Baños')) else None,
        cocheras=int(c) if (c := _zp_feature(posting, 'Cocheras')) else None,
        m2_total=_zp_feature(posting, 'Superficie total'),
        m2_cubiertos=_zp_feature(posting, 'Superficie cubierta'),
        antiguedad=int(posting['antiquity']) if posting.get('antiquity') else None,
        expensas=_zp_num((posting.get('expenses') or {}).get('amount')) or None,
        imagenes=[p['url730x532'] for p in pics if p.get('url730x532')],
        # Site-relative in the payload; a relative `url_origen` would break
        # dedup and every link the UI renders.
        url_origen=f'{_ZP_BASE}{url}' if url.startswith('/') else (url or None),
    )


_ZP_URL_SLUG = {
    'departamento': 'departamentos',
    'casa': 'casas',
    'ph': 'ph',
    'local': 'locales-comerciales',
    'oficina': 'oficinas',
    'terreno': 'terrenos',
}


def _zonaprop_search_url(filters: ScrapingFilters) -> str:
    """The listing URL for a search. ONE builder for both paths — the direct
    scrape and the Apify actor's `searchUrl` must never drift apart."""
    zona = filters.zona or 'Buenos Aires'
    op = filters.tipo_operacion or 'venta'
    # Portal-known localidad slug when available (ADR-1/spec: unknown barrio
    # slugs 404/redirect nationwide on ZonaProp) — else the barrio slug.
    zona_slug = _slugify(filters.localidades[0]) if filters.localidades else _slugify(zona)
    op_slug = 'alquiler' if op == 'alquiler' else 'venta'
    tipos = filters.tipos_propiedad
    prop_slug = _ZP_URL_SLUG.get(tipos[0], 'inmuebles') if len(tipos) == 1 else 'inmuebles'
    # Price belongs in the URL: the portal filters server-side, so pages that
    # cannot contain a match are never fetched at all.
    return f'{_ZP_BASE}/{prop_slug}-{op_slug}-{zona_slug}{_zonaprop_price_segment(filters)}.html'


# How many exit IPs to try before calling a block a wall. Two was the ceiling
# production kept hitting: from Railway, ZonaProp answered 403 on both attempts
# and the search ended at zero, while the SAME credential and `country-AR`
# residential proxy answers 200 twice in a row from a laptop. What differs is
# which IP the pool hands out, so the answer is more draws from it. Cheap: a
# 403 body is a few KB against ~1.3 MB for a real listing page.
_ZP_BLOCK_ATTEMPTS = 4
_ZP_BLOCK_BACKOFF = 0.7   # seconds between draws — a burst looks like a bot

_PROXY_SESSION_SEQ = count(1)


def _next_proxy_session() -> str:
    """A fresh Apify proxy session id — i.e. a fresh exit IP."""
    return f'zp{next(_PROXY_SESSION_SEQ)}{random.randint(1000, 9999)}'


def _proxy_with_session(proxy_url: str | None, session: str) -> str | None:
    """`proxy_url` with `session-<id>` set in the USERNAME.

    Apify selects the exit IP from the username: `groups-RESIDENTIAL` reuses
    whatever session it had, `groups-RESIDENTIAL,session-abc` pins a specific
    one. Rotating the id is how the actor never got stuck behind a burnt IP
    (`Browser launching with proxy session: zp_71397` in its own log) while we,
    reusing one session, ate consecutive 403s.

    An existing `session-` is REPLACED, never appended — two of them make the
    username invalid. A proxy without credentials has nothing to rotate and is
    returned untouched.
    """
    if not proxy_url:
        return None
    scheme, _, rest = proxy_url.partition('://')
    creds, at, host = rest.rpartition('@')
    if not at:
        return proxy_url
    user, _, password = creds.partition(':')
    opts = [o for o in user.split(',') if o and not o.startswith('session-')]
    opts.append(f'session-{session}')
    return f'{scheme}://{",".join(opts)}:{password}@{host}'


async def _scrape_zonaprop_direct(
    filters: ScrapingFilters, on_progress: ProgressCb,
) -> list[RawProperty]:
    """Page ZonaProp's own listing HTML — no Apify actor in the path.

    Three things get better beyond dodging the actor's crash:

    * Paging is DECLARED, not inferred. `paging.totalPages` says how many pages
      exist, so we neither guess a page size nor pay for a request past the end.
    * Membership is checked by ZONE ID against `appliedFilters` instead of
      matching listing text — which is what keeps "Grand Bell" and "Lomas de
      City Bell", real sub-barrios of City Bell that the text guard discarded.
    * An unknown composite slug is DETECTED rather than guessed at: the portal
      states which zone it applied.
    """
    from app.core.config import settings

    await on_progress('zonaprop', 'running', 0)
    base = _zonaprop_search_url(filters)
    cap = settings.ZONAPROP_MAX_RESULTS
    zona_req = filters.zona_pedida or filters.zona or ''

    results: list[RawProperty] = []
    seen: set[str] = set()
    page = 1
    total_pages = 1
    retried_plain = False

    # One session for the whole search, rotated only when it stops working.
    # Rotating per request was self-inflicted: live, nearly every FIRST attempt
    # drew a 403 and the retry then went through — a fresh exit IP gets
    # challenged, a warmed one is let through. The actor used one session per
    # run (`Browser launching with proxy session: zp_71397`).
    session = _next_proxy_session()

    async def _fetch(url: str) -> str | None:
        """The page body, or None.

        A 403/429 means this exit IP is burnt, not that the zona is empty, so
        the session is rotated and the request tried once more. Two failures in
        a row is a wall, not bad luck — it stops rather than spinning through
        IPs, and whatever pages already came back are kept.
        """
        nonlocal session
        for attempt in range(1, _ZP_BLOCK_ATTEMPTS + 1):
            if attempt > 1:
                session = _next_proxy_session()
                await asyncio.sleep(_ZP_BLOCK_BACKOFF)
            proxy = _proxy_with_session(settings.SCRAPER_PROXY_URL, session)
            async with httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT, follow_redirects=True, proxy=proxy,
                headers={'User-Agent': _BROWSER_UA, 'Accept-Language': 'es-AR,es;q=0.9'},
            ) as client:
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    return resp.text
                except httpx.HTTPStatusError as exc:
                    code = exc.response.status_code
                    # 403/429 = this exit IP is burnt. 5xx (Apify's own proxy
                    # answers 590 UPSTREAM504) = a hiccup between us and the
                    # portal. Both are worth one more try; a 404 is an ANSWER —
                    # the slug does not exist — and paying twice for it is waste.
                    if attempt < _ZP_BLOCK_ATTEMPTS and (code in (403, 429) or code >= 500):
                        logger.info(
                            'zonaprop: %s fallo transitorio (%d) — reintento con '
                            'otra sesion de proxy', url, code,
                        )
                        continue
                    logger.warning('zonaprop: %s fallo (%s)', url, exc)
                    return None
                except Exception as exc:
                    # A timeout or a dropped connection: transient by nature.
                    if attempt < _ZP_BLOCK_ATTEMPTS:
                        logger.info(
                            'zonaprop: %s corto la conexion (%s) — reintento con '
                            'otra sesion de proxy', url, type(exc).__name__,
                        )
                        continue
                    logger.warning('zonaprop: %s fallo (%s)', url, exc)
                    return None
        return None

    while page <= total_pages and (cap <= 0 or len(results) < cap):
        url = base if page == 1 else base.replace('.html', f'-pagina-{page}.html')
        body = await _fetch(url)
        if body is None:
            break

        state = _zonaprop_state(body)

        if state is not None and page == 1 and not retried_plain and ',' in zona_req:
            if _zonaprop_requested_zone_ids(state, zona_req) is None:
                # The portal served a zone we did not ask for: the composite
                # slug is unknown to it, and it answers with the containing
                # partido rather than a 404. Restart on the bare localidad,
                # which is the form its own URLs use.
                plain = zona_req.split(',')[0].strip()
                logger.info(
                    'zonaprop directo: %s sirvio %s en vez de %r — reintento con %r',
                    url, sorted(_zonaprop_applied_zone_labels(state)), zona_req, plain,
                )
                retried_plain = True
                base = _zonaprop_search_url(
                    filters.model_copy(update={'zona': plain, 'localidades': []})
                )
                total_pages = 1
                continue

        if state is None:
            # A WAF challenge or an error page. Say so: a silent empty list is
            # indistinguishable from a zona with no listings, which is exactly
            # how a broken source stays broken for weeks.
            logger.warning(
                'zonaprop: %s no trajo %s (%d KB) — puede ser el muro anti-bot; '
                'revisa SCRAPER_PROXY_URL',
                url, _ZP_STATE_MARKER, len(body) // 1024,
            )
            break

        paging = _zonaprop_paging(state)
        total_pages = int(paging.get('totalPages') or 1)
        # NOT every applied zone — only the one that was asked for.
        wanted = _zonaprop_requested_zone_ids(state, zona_req) or set()
        postings = (state.get('listStore') or {}).get('listPostings') or []

        kept = rejected = 0
        for posting in postings:
            pid = str(posting.get('postingId') or '')
            if pid and pid in seen:
                continue
            if pid:
                seen.add(pid)
            if wanted and not (wanted & _zonaprop_posting_zone_ids(posting)):
                rejected += 1
                continue
            prop = _norm_zonaprop_posting(posting)
            if prop is None:
                continue
            if cap > 0 and len(results) >= cap:
                break
            results.append(prop)
            kept += 1

        logger.info(
            'zonaprop directo: %s pagina=%d/%d avisos=%d kept=%d fuera_de_zona=%d '
            'total_portal=%s',
            url, page, total_pages, len(postings), kept, rejected, paging.get('total'),
        )
        page += 1

    await on_progress('zonaprop', 'done', len(results))
    return results


def _zonaprop_price_segment(filters: ScrapingFilters) -> str:
    """ZonaProp's price slug for a search, or `''` when it cannot be built.

    Two forms, both confirmed against real portal searches (`dolar` is
    SINGULAR, and the range carries no `menos`/`mas` keyword at all):

        ceiling  /locales-comerciales-venta-la-plata-la-plata-menos-30000-dolar.html
        range    /locales-comerciales-venta-la-plata-la-plata-20000-30000-dolar.html

    A FLOOR ALONE still emits nothing: the `mas-{N}-dolar` form is unverified,
    and an unknown ZonaProp slug does not 404 politely — it redirects to a
    nationwide listing that `_item_matches_zona` then rejects wholesale, so a
    wrong guess costs a silent zero-result search. The same reasoning drives
    the two fallbacks below: whenever the range is not well-formed, degrade to
    the confirmed ceiling form, which is WIDER than asked and therefore cannot
    filter out something the user wanted (`_split_by_criteria` orders the
    remainder downstream anyway).

    Bounds are floats on the model and ZonaProp's slug is an integer —
    `30000.0` in the path is a 404, hence the explicit `int()`.
    """
    pmin, pmax = filters.precio_min, filters.precio_max
    if pmax is None:
        return ''
    # `0-30000-dolar` is not a range the portal emits for "up to 30k", and a
    # floor above the ceiling is an upstream parse error, not a search.
    if pmin is not None and 0 < pmin < pmax:
        return f'-{int(pmin)}-{int(pmax)}-dolar'
    return f'-menos-{int(pmax)}-dolar'


def _locality_haystack(item: dict[str, Any]) -> str:
    """Where a LOCALIDAD may be looked for — deliberately excluding `city`.

    ZonaProp puts the PARTIDO in `city`, so letting the localidad match there
    makes every listing in the partido answer to its head town: a search for
    the La Plata casco came back full of City Bell and Villa Elisa.

    `neighborhood` is authoritative when the portal filled it — believe the
    label rather than an address that may spell out the partido too. Only when
    it is missing do we fall back to the free text.
    """
    if hood := _slugify(str(item.get('neighborhood') or '')):
        return hood
    return _slugify(' '.join(
        str(item.get(k) or '') for k in ('address', 'title', 'description')
    ))


def _item_matches_zona(item: dict[str, Any], zonas: Iterable[str]) -> bool:
    """Guard against ZonaProp redirecting an unknown zona slug to a nationwide
    listing: keep items that mention ANY phrase in `zonas` (as a phrase).

    `zonas` is a phrase SET (ADR-1: union of barrios ∪ localidad for a
    localidad-branch, or a single-item set `{zona}` on the chat path — which
    preserves today's single-phrase behavior exactly). An empty set keeps
    everything, same as the old empty-string sentinel.

    A composite phrase is read as `localidad, partido` and the two halves are
    checked against DIFFERENT fields. The localidad must appear where a
    locality is named (`_locality_haystack`); only the partido and any further
    parts may roam the whole record.

    That split is the whole point. Checking both halves against one merged
    blob collapsed "La Plata, La Plata" — the casco, whose localidad and
    partido share a name — into "does 'la plata' appear anywhere", which the
    `city` field satisfies for every listing in the partido. A single-part
    phrase keeps roaming the whole record, unchanged.
    """
    phrase_parts = [
        parts for parts in
        ([_slugify(p) for p in z.split(',') if _slugify(p)] for z in zonas)
        if parts
    ]
    if not phrase_parts:
        return True
    haystack = _slugify(' '.join(
        str(item.get(k) or '') for k in ('neighborhood', 'city', 'address', 'title', 'description')
    ))
    locality = _locality_haystack(item)

    def _matches(parts: list[str]) -> bool:
        if len(parts) == 1:
            return parts[0] in haystack
        return parts[0] in locality and all(p in haystack for p in parts[1:])

    return any(_matches(parts) for parts in phrase_parts)


# Share of a page the zona guard must reject before we treat the run as a
# nationwide redirect rather than a real (if narrow) result set. A correct slug
# keeps nearly everything — the guard is a redirect detector, not a filter — so
# anything at or above half is a clean separation between the two cases.
_REDIRECT_REJECT_RATIO = 0.5

# How many DISTINCT rejected locality labels one page keeps. A nationwide dump
# carries hundreds; a dozen is enough to recognise the shape and keeps the log
# line readable.
_REJECTED_SAMPLE = 12


@dataclass
class ZonaPropPage:
    """What one ZonaProp listing page yielded, and where each item died."""
    page: int
    raw: int = 0
    attempt: int = 1         # >1 = this page came back stunted and was re-asked
    duplicates: int = 0      # already seen — an out-of-range page redirects to page 1
    zona_rejected: int = 0   # failed `_item_matches_zona` (nationwide-redirect guard)
    no_price: int = 0        # `_norm_zonaprop` dropped it: "consultar precio"
    capped: int = 0          # matched, but `ZONAPROP_MAX_RESULTS` was already full
    kept: int = 0
    # The portal's own locality label for what the guard threw away. A count
    # alone cannot tell "the portal served another place" from "the right place
    # under a label the guard does not know" — and those need opposite fixes
    # (retry the slug vs. loosen the guard).
    rejected_zonas: dict[str, int] = field(default_factory=dict)

    def note_rejected(self, zona: str) -> None:
        label = (zona or '').strip() or '(sin barrio)'
        if label in self.rejected_zonas or len(self.rejected_zonas) < _REJECTED_SAMPLE:
            self.rejected_zonas[label] = self.rejected_zonas.get(label, 0) + 1

    @property
    def new_unique(self) -> int:
        return self.raw - self.duplicates


@dataclass
class ZonaPropFunnel:
    """Per-search accounting for the gap between "what the portal shows" and
    "what we return". Four gates and an early `break` sit between them, and
    without per-stage counts there is no way to tell which one is costing the
    results — so the loop reports all of them plus WHY it stopped paginating.
    """
    search_url: str
    pages: list[ZonaPropPage] = field(default_factory=list)
    stop_reason: str = 'unknown'

    def _total(self, attr: str) -> int:
        return sum(getattr(p, attr) for p in self.pages)

    @property
    def raw(self) -> int: return self._total('raw')

    @property
    def duplicates(self) -> int: return self._total('duplicates')

    @property
    def zona_rejected(self) -> int: return self._total('zona_rejected')

    @property
    def no_price(self) -> int: return self._total('no_price')

    @property
    def capped(self) -> int: return self._total('capped')

    @property
    def kept(self) -> int: return self._total('kept')

    @property
    def rejected_zonas(self) -> dict[str, int]:
        merged: dict[str, int] = {}
        for page in self.pages:
            for label, n in page.rejected_zonas.items():
                merged[label] = merged.get(label, 0) + n
        return merged

    @property
    def redirect_suspected(self) -> bool:
        """True when the guard threw out most of the page — the signature of
        ZonaProp answering an unknown slug with a NATIONWIDE listing instead of
        a 404.

        `not results` cannot see this. A nationwide dump that happens to carry
        a couple of listings from the right barrio comes back non-empty, so a
        retry gated on emptiness stays asleep and the search settles for two
        results when the portal has forty — the reported City Bell case.

        Judged on the FIRST page alone. A wrong slug is wrong from the first
        request, whereas a later page that drifts is ordinary end-of-listing
        behaviour (an out-of-range `-pagina-N` redirects to page 1 or to a
        nationwide listing). Averaging over every page conflates the two and
        buys a second full paid scrape for a search whose slug was fine.

        Keyed on `zona_rejected` specifically, not on `kept`: a page lost to
        missing prices says nothing about whether the slug was right.
        """
        if not self.pages:
            return False
        first = self.pages[0]
        if first.raw == 0:
            return False
        return first.zona_rejected / first.raw >= _REDIRECT_REJECT_RATIO

    def summary(self) -> str:
        return (
            f'zonaprop funnel url={self.search_url} pages={len(self.pages)} '
            f'raw={self.raw} duplicates={self.duplicates} '
            f'zona_rejected={self.zona_rejected} no_price={self.no_price} '
            f'capped={self.capped} kept={self.kept} stop={self.stop_reason} '
            f'per_page=[{", ".join(self._page_label(p) for p in self.pages)}]'
            + self._rejected_suffix()
        )

    @staticmethod
    def _page_label(page: ZonaPropPage) -> str:
        n = f'p{page.page}' if page.attempt == 1 else f'p{page.page}#{page.attempt}'
        return f'{n}:{page.raw}->{page.kept}'

    def _rejected_suffix(self) -> str:
        """Only printed when something was actually rejected — a clean page
        must not pay for this in noise."""
        rejected = self.rejected_zonas
        if not rejected:
            return ''
        top = sorted(rejected.items(), key=lambda kv: (-kv[1], kv[0]))
        return ' rechazados=[' + ', '.join(f'{k}:{n}' for k, n in top) + ']'


_ZP_PROP_TYPE: dict[str, str] = {
    'apartment': 'departamento', 'residence': 'departamento',
    'house': 'casa', 'ph': 'ph',
    'land': 'terreno', 'commercial': 'local', 'office': 'oficina',
}

def _norm_zonaprop(item: dict[str, Any], zona: str) -> RawProperty | None:
    precio = item.get('price')
    if precio is None:
        return None
    raw_type = (item.get('propertyType') or '').lower()
    prop_type = _ZP_PROP_TYPE.get(raw_type, 'otro')
    year_built = item.get('yearBuilt')
    antiguedad = (datetime.now().year - int(year_built)) if year_built and int(year_built) > 1900 else None
    return RawProperty(
        fuente='zonaprop',
        titulo=item.get('title', ''),
        descripcion=item.get('description'),
        direccion=item.get('address') or item.get('neighborhood') or zona,
        precio=float(precio),
        moneda=item.get('currency', 'USD'),
        tipo_operacion='alquiler' if item.get('operationType') == 'rent' else 'venta',
        tipo_propiedad=prop_type,  # type: ignore[arg-type]
        ambientes=item.get('rooms'),
        banos=int(item['bathrooms']) if item.get('bathrooms') is not None else None,
        cocheras=int(item['garages']) if item.get('garages') is not None else None,
        m2_total=item.get('totalArea'),
        m2_cubiertos=item.get('coveredArea'),
        antiguedad=antiguedad,
        amenities=[],
        imagenes=[
            img for img in (item.get('images') or [])
            if isinstance(img, str) and '/empresas/' not in img and 'logo' not in img.lower()
        ][:30],
        url_origen=item.get('url', ''),
    )


_ARGENPROP_TIPO_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'\bdepartamento\b', re.I), 'departamento'),
    (re.compile(r'\bph\b', re.I), 'ph'),
    (re.compile(r'\bcasa\b', re.I), 'casa'),
    (re.compile(r'\boficina\b', re.I), 'oficina'),
    (re.compile(r'\blocal\b', re.I), 'local'),
    (re.compile(r'\bterreno\b|\blote\b', re.I), 'terreno'),
]


def _argenprop_tipo_propiedad(title_primary: str) -> str:
    for pattern, tipo in _ARGENPROP_TIPO_PATTERNS:
        if pattern.search(title_primary):
            return tipo
    return 'otro'


async def _argenprop_resolve_zona_slug(zona: str) -> str | None:
    """Free-text zona → Argenprop's canonical URL slug, via the portal's own
    autocomplete API ("Gonnet" → "manuel-gonnet"). Naive slugs the portal
    doesn't know 301 to the NATIONWIDE listing, so guessing is not an option.

    Results come ordered by `Importancia`, which ranks fuzzy matches above
    exact ones (real case: "villa elisa" → "Barrio Villa Felisa, San Lorenzo"
    first) — so the winner is the first result whose slugified label contains
    EVERY comma-part of the query, never the raw first item. Slug is the
    lowercased `CodigoBarrio` (when present) or `CodigoLocalidad`. Returns
    None on no confident match or any API failure — callers fall back to
    `_slugify`, with the zona guard as the final safety net."""
    query_parts = [p.strip() for p in zona.split(',') if p.strip()]
    if not query_parts:
        return None
    cache_key = _slugify(zona)
    if cache_key in _ARGENPROP_SLUG_CACHE:
        return _ARGENPROP_SLUG_CACHE[cache_key]

    slug: str | None = None
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                _ARGENPROP_AUTOCOMPLETE_URL,
                params={'stringBusqueda': query_parts[0]},
            )
            resp.raise_for_status()
            results = resp.json()
        wanted = [_slugify(p) for p in query_parts]
        for entry in results:
            label_slug = _slugify(str(entry.get('label') or ''))
            if not all(part in label_slug for part in wanted):
                continue
            value = entry.get('value') or {}
            code = value.get('CodigoBarrio') or value.get('CodigoLocalidad') or ''
            if code:
                slug = str(code).lower()
                break
    except Exception:
        return None  # transient failure — don't cache, retry next search

    _ARGENPROP_SLUG_CACHE[cache_key] = slug
    return slug


def _argenprop_search_urls(
    filters: ScrapingFilters, max_pages: int, zona_slug: str | None = None,
) -> list[str]:
    """Argenprop listing-page URLs for one search, page 1..N. Page 1 has no
    query string; later pages append the literal `?pagina-N` token (no `=`,
    confirmed from real `href`s) — capped at 10 because robots.txt only
    `Allow`s pagina-1 through pagina-10 — so `max_pages=0` ("no cap" everywhere
    else) resolves to that ceiling here, since it is a robots.txt boundary and
    not a tunable. `zona_slug` is the portal-resolved slug from
    `_argenprop_resolve_zona_slug`; without one, falls back to naive `_slugify`
    (the zona guard downstream rejects redirect garbage)."""
    zona = filters.zona or 'Buenos Aires'
    op_slug = 'alquiler' if filters.tipo_operacion == 'alquiler' else 'venta'
    tipos = filters.tipos_propiedad or []
    tipo_slug = _ARGENPROP_URL_SLUG.get(tipos[0], 'inmuebles') if len(tipos) == 1 else 'inmuebles'
    if zona_slug is None:
        zona_slug = _slugify(filters.localidades[0]) if filters.localidades else _slugify(zona)
    base_url = f'{_ARGENPROP_BASE}/{tipo_slug}/{op_slug}/{zona_slug}'

    capped = (
        _ARGENPROP_MAX_PAGES_HARD if max_pages <= 0
        else min(max_pages, _ARGENPROP_MAX_PAGES_HARD)
    )
    return [base_url] + [f'{base_url}?pagina-{page}' for page in range(2, capped + 1)]


def _argenprop_feature_list(card: Any) -> Any:
    """The `<ul>` holding m²/dorms/antigüedad.

    In raw HTML the card's first `<ul>` is the photo carousel, so a positional
    `card.find('ul')` reads photos and silently drops every feature. Prefer the
    class, then fall back to the first non-carousel `<ul>` — that fallback is
    what keeps Readability output (classes stripped, carousel gone) working.
    """
    if (by_class := card.select_one('ul.card__main-features')) is not None:
        return by_class
    for ul in card.find_all('ul'):
        classes = ul.get('class') or []
        if ul.get('data-carousel') is None and 'card__photos' not in classes:
            return ul
    return None


def _argenprop_card_images(card: Any) -> list[str]:
    """The card's photo carousel, from raw (pre-Readability) HTML.

    Argenprop server-renders every carousel photo into the search results —
    the first `<img>` eagerly via `src`, the rest lazily via `data-src`. Both
    are read here. The scope is deliberately the carousel `<ul>` and not the
    card: the agency logo `<img>` sits in a sibling `div.card__agent` and must
    never be mistaken for a property photo.

    Cards serve `_u_small`; the ficha serves the same asset ids at
    `_u_medium` (verified live), so the suffix is upgraded for free quality.
    """
    urls: list[str] = []
    for img in card.select('ul[data-carousel] img, ul.card__photos img'):
        src = str(img.get('src') or img.get('data-src') or '').strip()
        # Cards with no photos render the local placeholder SVG instead.
        if not src.startswith('http') or 'photo_placeholder' in src:
            continue
        url = src.replace('_u_small.', '_u_medium.')
        if url not in urls:
            urls.append(url)
    return urls[:_MAX_GALLERY]


def _parse_argenprop_page(html: str, filters: ScrapingFilters) -> list[RawProperty]:
    """Argenprop's search-results HTML, parsed deterministically (no LLM).

    The website-content-crawler actor runs with `htmlTransformer: 'none'` so
    this receives the RAW server DOM. It used to receive the actor's default
    Readability (reader-mode) output, which strips every `class` attribute
    site-wide AND the entire photo carousel — that's why Argenprop results
    reached the UI photoless.

    The selectors below stay tolerant of BOTH shapes, so a transformer change
    upstream degrades (no photos) instead of returning zero properties: cards
    are matched via `a[idaviso]` and most fields via bare attributes plus
    stable `<p>`/`<ul>`/`<h2>` sibling ordering, which Readability preserves.

    One trap the raw shape introduces: the card's FIRST `<ul>` is the photo
    carousel, not the feature list — see `_argenprop_feature_list`.
    """
    from bs4 import BeautifulSoup  # type: ignore[import-untyped]

    if not html:
        return []
    soup = BeautifulSoup(html, 'html.parser')
    tipo_operacion = filters.tipo_operacion or 'venta'
    # Unknown zona slugs 301-redirect to the bare nationwide listing (verified
    # live: /departamentos/venta/gonnet → /departamentos/venta, which even
    # includes Uruguay) — same failure mode as ZonaProp, same guard: drop any
    # card whose text doesn't mention the requested zona phrases.
    phrases = _guard_phrases(filters)
    results: list[RawProperty] = []

    for card in soup.select('a[idaviso]'):
        href = str(card.get('href') or '')
        if not href:
            continue
        url_origen = href if href.startswith('http') else _ARGENPROP_BASE + href

        monto = card.get('montonormalizado')
        try:
            precio = float(str(monto)) if monto else None
        except ValueError:
            precio = None
        if not precio:
            continue

        direccion_el = card.select_one('p[data-card-direccion]')
        direccion = direccion_el.get_text(strip=True) if direccion_el else (filters.zona or '')

        price_el = direccion_el.find_previous_sibling('p') if direccion_el else None
        currency_raw = 'USD'
        if price_el is not None:
            currency_span = price_el.find('span')
            if currency_span is not None and currency_span.get_text(strip=True):
                currency_raw = currency_span.get_text(strip=True)

        title_primary_el = direccion_el.find_next_sibling('p') if direccion_el else None
        title_primary = title_primary_el.get_text(strip=True) if title_primary_el else ''

        m2_cubiertos: float | None = None
        ambientes: int | None = None
        antiguedad: int | None = None
        features_ul = _argenprop_feature_list(card)
        if features_ul is not None:
            for feat_span in features_ul.select('li span'):
                feat = feat_span.get_text(strip=True)
                if 'm²' in feat:
                    if num := re.search(r'[\d.,]+', feat):
                        m2_cubiertos = float(num.group().replace(',', '.'))
                elif 'dorm' in feat:
                    if num := re.search(r'\d+', feat):
                        ambientes = int(num.group())
                elif 'año' in feat:
                    if num := re.search(r'\d+', feat):
                        antiguedad = int(num.group())
        if ambientes is None:
            dorm_attr = str(card.get('dormitorios') or '')
            if dorm_attr.isdigit():
                ambientes = int(dorm_attr)

        titulo_el = card.find('h2')
        titulo = titulo_el.get_text(strip=True) if titulo_el else title_primary

        descripcion = None
        if titulo_el is not None:
            desc_el = titulo_el.find_next_sibling('p')
            descripcion = desc_el.get_text(strip=True) if desc_el else None

        # The listing URL slug always carries the real zone
        # ("casa-en-venta-en-manuel-b-gonnet-...") — include it in the guard's
        # haystack alongside the visible text.
        if not _item_matches_zona({
            'address': direccion,
            'title': titulo,
            'description': f'{title_primary} {descripcion or ""} {url_origen}',
        }, phrases):
            continue

        results.append(RawProperty(
            fuente='argenprop',
            titulo=titulo,
            descripcion=descripcion,
            direccion=direccion,
            precio=precio,
            moneda=currency_raw,  # type: ignore[arg-type]
            tipo_operacion=tipo_operacion,  # type: ignore[arg-type]
            tipo_propiedad=_argenprop_tipo_propiedad(title_primary),  # type: ignore[arg-type]
            ambientes=ambientes,
            m2_cubiertos=m2_cubiertos,
            antiguedad=antiguedad,
            amenities=[],
            imagenes=_argenprop_card_images(card),
            url_origen=url_origen,
        ))

    return results


_ML_PROP_TYPE: dict[str, str] = {
    'departamento': 'departamento',
    'casa': 'casa',
    'ph': 'ph',
    'local comercial': 'local',
    'oficina': 'oficina',
    'terreno': 'terreno',
    'campo': 'terreno',
    'cochera': 'otro',
    'galpón': 'otro',
}


# ── InmoBusqueda — plain server-rendered PHP portal. No WAF, no Apify actor,
# no client-side hydration: a direct httpx GET returns the full results DOM,
# and `-pagina-N` URLs paginate for real (100+ pages on a busy zona).
#
# This is the one portal in the catalog with genuine La Plata-area depth
# (Manuel B Gonnet, City Bell, Villa Elisa, La Plata casco urbano), which is
# exactly where Zonaprop/Argenprop thin out.
#
# The zona is resolved through the portal's own location autocomplete — the
# endpoint its search box calls, public and unauthenticated. That indirection
# is NOT optional: an unknown slug does not 404, it renders the same page with
# the zona silently dropped, so a guessed slug returns nationwide results that
# look valid. Verified live: `propiedades-gonnet.html` is that trap; the real
# slug is `manuel-b-gonnet`. ─────────────────────────────────────────────────
_INMOBUSQUEDA_BASE = 'https://www.inmobusqueda.com.ar'
_INMOBUSQUEDA_AUTOCOMPLETE_URL = f'{_INMOBUSQUEDA_BASE}/configubicacion/autocomplete.json.php'
_INMOBUSQUEDA_SLUG_CACHE: dict[str, str | None] = {}
_INMOBUSQUEDA_PAGE_SIZE = 15   # cards per `propiedades-...` page (20 on typed ones)

# Operation and property-type segments of the URL, read off the portal's own
# search form. A type it doesn't model falls back to the untyped
# `propiedades-{zona}` listing rather than 404ing the whole search.
_INMOBUSQUEDA_URL_OP: dict[str, str] = {
    'venta': 'venta', 'alquiler': 'alquiler', 'alquiler_temp': 'alquiler-temporario',
}
_INMOBUSQUEDA_URL_TIPO: dict[str, str] = {
    'departamento': 'departamento', 'casa': 'casa', 'ph': 'ph',
    'local': 'local', 'oficina': 'oficina', 'terreno': 'terreno',
}
# Card label → our canonical type. The portal's catalog is far wider than ours
# (Chacras, Tambos, Haras…); anything unlisted lands on 'otro'.
_INMOBUSQUEDA_TIPO_LABELS: dict[str, str] = {
    'departamento': 'departamento', 'monoambiente': 'departamento', 'piso': 'departamento',
    'duplex': 'departamento', 'triplex': 'departamento',
    'casa': 'casa', 'chalet': 'casa', 'casa quinta': 'casa', 'casa en country': 'casa',
    'ph': 'ph',
    'local': 'local', 'fondo de comercio': 'local',
    'oficina': 'oficina', 'consultorio': 'oficina',
    'terreno': 'terreno', 'lote': 'terreno', 'campo': 'terreno',
    'fracciones': 'terreno', 'chacras': 'terreno', 'lote en country': 'terreno',
}


async def _inmobusqueda_resolve_zona_slug(zona: str) -> str | None:
    """Free-text zona → InmoBusqueda's canonical URL slug, via the portal's own
    autocomplete ("Gonnet" → "manuel-b-gonnet").

    Candidates are entries whose slugified `name` contains EVERY comma-part of
    the query; the winner is the first whose FIRST name-component matches the
    query head exactly, else the first candidate. That exact-head rule is what
    keeps "city bell" off "Lomas de City Bell" (which the API ranks first and
    whose page comes back zona-less) and picks CABA's "Palermo" over the
    homonym in Partido de Anta, Salta. Returns None on no match or any API
    failure — the caller then skips the search rather than scraping the
    country.
    """
    query_parts = [p.strip() for p in zona.split(',') if p.strip()]
    if not query_parts:
        return None
    cache_key = _slugify(zona)
    if cache_key in _INMOBUSQUEDA_SLUG_CACHE:
        return _INMOBUSQUEDA_SLUG_CACHE[cache_key]

    slug: str | None = None
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                _INMOBUSQUEDA_AUTOCOMPLETE_URL,
                params={'partido': 1, 'valor': query_parts[0]},
            )
            resp.raise_for_status()
            results = resp.json()
        wanted = [_slugify(p) for p in query_parts]
        fallback: str | None = None
        for entry in results or []:
            name = str(entry.get('name') or '')
            if not name or not all(part in _slugify(name) for part in wanted):
                continue
            # `localidad_id: 0` marks the form-only "Todo el Partido de X"
            # option: the search box submits it by id, and it has no listing
            # page of its own — `propiedades-todo-el-partido-de-la-plata.html`
            # renders the zona-less nationwide page (verified live).
            if not int(entry.get('localidad_id') or 0):
                continue
            head = _slugify(name.split(',')[0])
            if head == wanted[0]:
                slug = head
                break
            if fallback is None:
                fallback = head
        if slug is None:
            slug = fallback
    except Exception:
        return None  # transient failure — don't cache, retry next search

    _INMOBUSQUEDA_SLUG_CACHE[cache_key] = slug
    return slug


def _inmobusqueda_search_urls(
    filters: ScrapingFilters, max_pages: int, zona_slug: str,
) -> Iterator[str]:
    """Listing URLs for one search, page 1..N — lazily, so `max_pages=0` means
    "no cap" and the caller's own exhaustion checks (a page with nothing new,
    or a short page) are what end the crawl.

    Two shapes exist: `{tipo}-{operacion}-{zona}.html` when the search pins a
    single property type AND an operation, else the broader
    `propiedades-{zona}.html`. Later pages append `-pagina-N` before `.html`.
    """
    tipos = filters.tipos_propiedad or []
    tipo_slug = _INMOBUSQUEDA_URL_TIPO.get(tipos[0], '') if len(tipos) == 1 else ''
    op_slug = _INMOBUSQUEDA_URL_OP.get(filters.tipo_operacion or '', '')
    stem = f'{tipo_slug}-{op_slug}-{zona_slug}' if tipo_slug and op_slug else f'propiedades-{zona_slug}'

    yield f'{_INMOBUSQUEDA_BASE}/{stem}.html'
    for n in count(2):
        if max_pages > 0 and n > max_pages:
            return
        yield f'{_INMOBUSQUEDA_BASE}/{stem}-pagina-{n}.html'


def _inmobusqueda_price(text: str) -> tuple[float | None, str]:
    """"U$S 145.000" → (145000.0, 'USD'); "$ 350.000" → (350000.0, 'ARS').

    "Consultar" (a real listing with no public price) yields no price rather
    than dropping the card — the pipeline already treats `precio=None` as
    unknown and never filters it out.
    """
    raw = text.strip()
    moneda = 'ARS' if raw.startswith('$') else 'USD'
    digits = re.sub(r'[^\d]', '', raw.split(',')[0])
    return (float(digits) if digits else None), moneda


def _inmobusqueda_card_details(card: Any) -> dict[str, Any]:
    """The `div.rdBox` chip row: ambientes, m², garage, listing code, date.

    Chips are positional-free — each is identified by its own text, because a
    partial listing simply omits the ones it has no data for (and the row is
    padded with empty divs). "N Dorm" feeds `ambientes`, the same mapping the
    Argenprop parser makes.
    """
    out: dict[str, Any] = {}
    for chip in card.select('div.rdBox'):
        text = ' '.join(chip.get_text(' ', strip=True).split())
        low = text.lower()
        if not text or low.startswith('ib-'):
            continue
        if 'monoamb' in low:
            out['ambientes'] = 1
        elif (m := re.match(r'(\d+)\s*(?:amb|dorm)', low)):
            out['ambientes'] = int(m.group(1))
        elif (m := re.match(r'([\d.,]+)\s*(?:mts|m2|m²)', low)):
            out['m2_total'] = float(m.group(1).replace(',', '.'))
        elif low.startswith('garage'):
            out['cocheras'] = 0 if 'no' in low.split() else 1
    return out


def _inmobusqueda_ficha_url(href: str) -> str | None:
    """Canonical `/ficha-{id}` URL for a card link.

    Promoted listings are wrapped in `ficha.verdestacado.php?id=…&hash=…&rd=…`,
    a tracking redirect whose `hash`/`rd` change on every crawl — storing that
    would defeat both dedup and any later re-fetch of the same listing.
    """
    href = (href or '').strip()
    if not href:
        return None
    if 'verdestacado' in href:
        if m := re.search(r'[?&]id=(\d+)', href):
            return f'{_INMOBUSQUEDA_BASE}/ficha-{m.group(1)}'
        return None
    return href


def _inmobusqueda_card_identity(
    tipo_text: str, localidad_text: str,
) -> tuple[str, str]:
    """(tipo label, address) for a card, resolved by CONTENT not position.

    The portal reuses one markup for two page shapes: on `propiedades-{zona}`
    the `resultadoTipo` block reads "{tipo} en {operación}" and `localidad`
    holds the address; on `{tipo}-{operacion}-{zona}` the SAME block holds the
    street address and `localidad` reads "{tipo} en {zona}". Whichever block
    starts with a type we know is the type block; the other is the address.
    """
    def leading_tipo(text: str) -> str:
        head = ' '.join(text.split()).lower()
        # Longest label first so "casa quinta" wins over "casa".
        for label in sorted(_INMOBUSQUEDA_TIPO_LABELS, key=len, reverse=True):
            if head.startswith(label):
                return label
        return ''

    tipo_from_localidad = leading_tipo(localidad_text)
    if tipo_from_localidad and not leading_tipo(tipo_text):
        # Typed listing: `resultadoTipo` is the street address, and `localidad`
        # carries "{tipo} en {zona}" — join both so the address keeps its zona.
        zona_tail = re.sub(r'^\s*\S.*?\ben\b\s*', '', localidad_text, count=1).strip()
        street = ' '.join(tipo_text.split())
        direccion = f'{street}, {zona_tail}' if street and zona_tail else (street or zona_tail)
        return tipo_from_localidad, direccion

    return leading_tipo(tipo_text) or tipo_from_localidad, ' '.join(localidad_text.split())


def _parse_inmobusqueda_page(html: str, filters: ScrapingFilters) -> list[RawProperty]:
    """One results page → RawProperty list, zona-guarded.

    The guard is the same defence ZonaProp needs: when a slug fails to resolve
    the portal serves nationwide results in identical markup, so a card only
    survives if its text mentions one of the searched phrases.
    """
    from bs4 import BeautifulSoup  # type: ignore[import-untyped]

    soup = BeautifulSoup(html, 'html.parser')
    # Same phrase-set semantics as `_item_matches_zona`: a composite phrase
    # ("Villa Elisa, La Plata") requires EVERY comma part in the haystack.
    phrase_parts = [
        parts for parts in
        ([_slugify(p) for p in z.split(',') if _slugify(p)] for z in _guard_phrases(filters))
        if parts
    ]

    results: list[RawProperty] = []
    for card in soup.select('div.ResultadoCaja'):
        link = card.select_one('div.resultadoTipo a')
        if link is None:
            continue
        url = _inmobusqueda_ficha_url(str(link.get('href') or ''))
        heading = link.get_text(' ', strip=True)

        localidad_el = card.select_one('div.resultadoLocalidad')
        localidad_text = localidad_el.get_text(' ', strip=True) if localidad_el else ''

        if phrase_parts:
            haystack = _slugify(card.get_text(' ', strip=True))
            if not any(all(part in haystack for part in parts) for parts in phrase_parts):
                continue

        tipo_key, direccion = _inmobusqueda_card_identity(heading, localidad_text)

        # The operation is only spelled out on the untyped listing ("… en
        # Venta"); the typed one omits it entirely, so the search's own filter
        # is the fallback — it is what selected that URL in the first place.
        op_low = f'{heading} {localidad_text}'.lower()
        if 'temporario' in op_low:
            tipo_operacion = 'alquiler_temp'
        elif 'alquiler' in op_low:
            tipo_operacion = 'alquiler'
        elif 'venta' in op_low:
            tipo_operacion = 'venta'
        else:
            tipo_operacion = filters.tipo_operacion or 'venta'

        precio_el = card.select_one('div.resultadoPrecio')
        precio, moneda = _inmobusqueda_price(precio_el.get_text(' ', strip=True) if precio_el else '')

        desc_el = card.select_one('div.resultadoDescripcion')
        descripcion = desc_el.get_text(' ', strip=True) if desc_el else None

        # Photoless listings render the agency's "sin fotos" placeholder.
        imagenes = [
            src for img in card.select('img.FotoBox')
            if (src := str(img.get('src') or '').strip()).startswith('http')
            and 'sinfotos' not in src
        ]

        detalles = _inmobusqueda_card_details(card)
        results.append(RawProperty(
            fuente='inmobusqueda',
            titulo=' '.join(heading.split()) or None,
            descripcion=descripcion or None,
            direccion=direccion or (filters.zona or ''),
            precio=precio,
            moneda=moneda,
            tipo_operacion=tipo_operacion,
            tipo_propiedad=_INMOBUSQUEDA_TIPO_LABELS.get(tipo_key, 'otro'),
            ambientes=detalles.get('ambientes'),
            cocheras=detalles.get('cocheras'),
            m2_total=detalles.get('m2_total'),
            imagenes=imagenes[:_MAX_GALLERY],
            url_origen=url,
        ))
    return results


async def _scrape_inmobusqueda(
    filters: ScrapingFilters, on_progress: ProgressCb,
) -> list[RawProperty]:
    """Sequential page walk over the portal's own paginated URLs.

    Stops early on an empty page or a short one (the listing's last), so a
    zona with 30 results costs two requests instead of the full page budget.
    """
    from app.core.config import settings
    await on_progress('inmobusqueda', 'running', 0)

    # Fan-out unit: localidad on the map path, zona on the chat path.
    zona = filters.localidades[0] if filters.localidades else (filters.zona or '')
    zona_slug = await _inmobusqueda_resolve_zona_slug(zona)
    if not zona_slug:
        # No confident slug → the portal would serve the whole country. Report
        # zero rather than flooding the search with unrelated listings.
        await on_progress('inmobusqueda', 'done', 0)
        return []

    urls = _inmobusqueda_search_urls(filters, settings.INMOBUSQUEDA_MAX_PAGES, zona_slug)
    results: list[RawProperty] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(
        timeout=20, follow_redirects=True,
        headers={'User-Agent': 'Mozilla/5.0 (compatible; PropSearchBot/1.0)'},
    ) as client:
        for url in urls:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except Exception:
                break

            page_props = _parse_inmobusqueda_page(resp.text, filters)
            new = 0
            for prop in page_props:
                key = str(prop.url_origen or '')
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                results.append(prop)
                new += 1

            if new == 0 or len(page_props) < _INMOBUSQUEDA_PAGE_SIZE:
                break
            await on_progress('inmobusqueda', 'running', len(results))

    await on_progress('inmobusqueda', 'done', len(results))
    return results


# ── Mudafy — Next.js App Router site. The listing data is NOT in the DOM and
# there is no `__NEXT_DATA__`: it rides inside the React Server Components
# flight payload (`self.__next_f.push([...])`), JSON-escaped in a JS string.
#
# We do NOT decode the flight format — it's an internal React protocol that
# shifts between Next releases. Unescaping the page and scanning for
# `"publication":{…}` objects keeps the coupling to a single property name.
#
# Worth the effort because the payload is the richest in this catalog: `street`
# and `street_number` arrive as separate fields, which is exactly what the
# cross-portal dedup fingerprint anchors on.
#
# robots.txt disallows `/api/` and `/*?` — so path-based URLs only, never a
# query string. ─────────────────────────────────────────────────────────────
_MUDAFY_BASE = 'https://mudafy.com.ar'
_MUDAFY_PAGE_SIZE = 25

# Mudafy searches by broad REGION only: city and barrio slugs 404. Zonas are
# mapped onto the region that contains them, and the zona guard then does the
# precision filtering locally over what comes back.
_MUDAFY_REGIONS: tuple[str, ...] = (
    'caba',
    'provincia-de-buenos-aires-gba-norte',
    'provincia-de-buenos-aires-gba-oeste',
    'provincia-de-buenos-aires-gba-sur',
    'provincia-de-buenos-aires-costa-atlantica',
    'provincia-de-buenos-aires-interior-de-buenos-aires',
    'cordoba',
    'rio-negro',
)
_MUDAFY_DEFAULT_REGION = 'provincia-de-buenos-aires-gba-sur'
# Zona → region. Only what this deployment actually searches; anything else
# falls back to the default region and is filtered by the zona guard.
_MUDAFY_ZONA_REGION: dict[str, str] = {
    'la-plata': 'provincia-de-buenos-aires-gba-sur',
    'city-bell': 'provincia-de-buenos-aires-gba-sur',
    'manuel-b-gonnet': 'provincia-de-buenos-aires-gba-sur',
    'gonnet': 'provincia-de-buenos-aires-gba-sur',
    'villa-elisa': 'provincia-de-buenos-aires-gba-sur',
    'tolosa': 'provincia-de-buenos-aires-gba-sur',
    'berisso': 'provincia-de-buenos-aires-gba-sur',
    'ensenada': 'provincia-de-buenos-aires-gba-sur',
}
_MUDAFY_CABA_ZONAS = frozenset({
    'palermo', 'belgrano', 'caballito', 'recoleta', 'almagro', 'villa-crespo',
    'nunez', 'colegiales', 'puerto-madero', 'san-telmo', 'flores', 'devoto',
})

# Mudafy DOES serve city and barrio pages — the earlier read that "city and
# barrio slugs 404" was true only for a BARE slug (`/la-plata` → 404). The real
# segment carries its full ancestry: `{region}-{city}` and
# `{region}-{city}-{barrio}` (verified live, all 200).
#
# This matters far more than tidiness. The region pages are huge — GBA Sur is 14
# pages of ~25 — while a city like La Plata has ~10 listings in the whole
# portal. Searching the region for a city means sweeping ~350 rows to keep ~10,
# and the paging heuristic gives up on the first page that contributes none.
#
# Only zonas whose Mudafy spelling differs from ours need an entry; everything
# else derives as `{region}-{zona}` and falls through to the region on a 404.
_MUDAFY_ZONA_LOCATION: dict[str, str] = {
    'la-plata': 'provincia-de-buenos-aires-gba-sur-la-plata',
    'city-bell': 'provincia-de-buenos-aires-gba-sur-la-plata-city-bell',
    'gonnet': 'provincia-de-buenos-aires-gba-sur-la-plata-manuel-b-gonnet',
    'manuel-b-gonnet': 'provincia-de-buenos-aires-gba-sur-la-plata-manuel-b-gonnet',
    'villa-elisa': 'provincia-de-buenos-aires-gba-sur-la-plata-villa-elisa',
    'tolosa': 'provincia-de-buenos-aires-gba-sur-la-plata-tolosa',
    'berisso': 'provincia-de-buenos-aires-gba-sur-berisso',
    'ensenada': 'provincia-de-buenos-aires-gba-sur-ensenada',
}

_MUDAFY_URL_OP: dict[str, str] = {'venta': 'venta', 'alquiler': 'alquiler', 'alquiler_temp': 'alquiler'}
_MUDAFY_URL_TIPO: dict[str, str] = {
    'departamento': 'departamentos', 'casa': 'casas', 'ph': 'ph',
    'oficina': 'oficinas', 'terreno': 'terrenos', 'local': 'propiedades',
}
_MUDAFY_KIND: dict[str, str] = {
    'apartment': 'departamento', 'house': 'casa', 'ph': 'ph',
    'land': 'terreno', 'office': 'oficina', 'commercial': 'local', 'store': 'local',
}
# The listing-URL segment mirrors the kind, not our canonical type.
_MUDAFY_KIND_PATH: dict[str, str] = {
    'apartment': 'apartment', 'house': 'house', 'ph': 'ph',
    'land': 'land', 'office': 'office', 'commercial': 'commercial',
}


def _mudafy_region_for(zona: str) -> str:
    key = _slugify(zona)
    if key in _MUDAFY_ZONA_REGION:
        return _MUDAFY_ZONA_REGION[key]
    if key in _MUDAFY_CABA_ZONAS:
        return 'caba'
    if key in _MUDAFY_REGIONS:
        return key
    return _MUDAFY_DEFAULT_REGION


def _mudafy_location_slug(zona: str, region: str) -> str | None:
    """The most precise location segment Mudafy might serve for this zona, or
    None when the zona IS the region and there is nothing narrower to try."""
    key = _slugify(zona)
    if not key or key == region or key in _MUDAFY_REGIONS:
        return None
    if key in _MUDAFY_ZONA_LOCATION:
        return _MUDAFY_ZONA_LOCATION[key]
    return f'{region}-{key}'


def _mudafy_search_bases(filters: ScrapingFilters) -> list[str]:
    """Listing bases to walk, most precise first.

    The city page is tried before the region so a city search reads ~10 rows
    instead of sweeping ~350. A derived slug that 404s costs one request and
    then falls through to the region, which is exactly the old behaviour.
    """
    zona = filters.localidades[0] if filters.localidades else (filters.zona or '')
    region = _mudafy_region_for(zona)
    op = _MUDAFY_URL_OP.get(filters.tipo_operacion or 'venta', 'venta')
    tipos = filters.tipos_propiedad or []
    tipo = _MUDAFY_URL_TIPO.get(tipos[0], 'propiedades') if len(tipos) == 1 else 'propiedades'

    bases = []
    location = _mudafy_location_slug(zona, region)
    if location:
        bases.append(f'{_MUDAFY_BASE}/{op}/{tipo}/{location}')
    bases.append(f'{_MUDAFY_BASE}/{op}/{tipo}/{region}')
    return bases


def _mudafy_search_urls(
    filters: ScrapingFilters, max_pages: int, base: str | None = None,
) -> Iterator[str]:
    """Listing URLs for one base, page 1..N — lazily, so `max_pages=0` means "no
    cap" and the caller stops when a page brings nothing new. Later pages take
    the `/{N}-p` suffix — read off the site's own pagination hrefs, and
    query-string free so the crawl stays inside what robots.txt allows."""
    root = base or _mudafy_search_bases(filters)[0]
    yield root
    for n in count(2):
        if max_pages > 0 and n > max_pages:
            return
        yield f'{root}/{n}-p'


# Mudafy ships each photo as a bag of pre-rendered CDN variants rather than one
# URL, so the gallery is built by walking the ladder from the size the cards
# want down to whatever the entry actually carries. `large` (~200KB webp) is the
# balance point: sharp on the ficha, cheap enough for a results grid.
_MUDAFY_PHOTO_SIZES = (
    'large_link', 'full_size_link', 'standard_link', 'medium_link',
    'small_link', 'original_link',
)


def _mudafy_photo_urls(pub: dict[str, Any]) -> list[str]:
    """`publication.photos[]` → gallery URLs, in the order the site renders them.

    The payload calls this `photos` — NOT `pictures` — and `order` is the
    seller's chosen sequence, which the array itself does not always follow.
    Withdrawn photos (`is_enabled: false`) and non-photo assets (blueprints and
    the like) are not gallery material, so they never reach a card.
    """
    photos = pub.get('photos') or []
    if not isinstance(photos, list):
        return []

    usable: list[tuple[int, str]] = []
    for i, photo in enumerate(photos):
        if not isinstance(photo, dict):
            continue
        if photo.get('is_enabled') is False:
            continue
        # An entry with no `type` is still a photo — only a named non-photo is out.
        if str(photo.get('type') or 'photo').lower() != 'photo':
            continue
        url = next(
            (str(photo[size]).strip() for size in _MUDAFY_PHOTO_SIZES
             if str(photo.get(size) or '').strip().startswith('http')),
            None,
        )
        if url is None:
            continue
        order = photo.get('order')
        usable.append((order if isinstance(order, int) else i, url))

    urls: list[str] = []
    for _, url in sorted(usable, key=lambda pair: pair[0]):
        if url not in urls:
            urls.append(url)
    return urls[:_MAX_GALLERY]


def _norm_mudafy(pub: dict[str, Any]) -> RawProperty | None:
    """One `publication` object → RawProperty."""
    addr = pub.get('address') or {}
    direccion = str(addr.get('full_address') or addr.get('public_address') or '').strip()
    if not direccion:
        return None

    price = pub.get('price') or {}
    amount = price.get('amount')
    dims = pub.get('dimensions') or {}
    prop = pub.get('property') or {}
    rooms = prop.get('rooms') or {}
    kind = str(prop.get('kind') or '').lower()

    slug = str(pub.get('slug') or '')
    kind_path = _MUDAFY_KIND_PATH.get(kind, 'property')
    url = f'{_MUDAFY_BASE}/{kind_path}/{slug}' if slug else None

    def _num(value: Any) -> float | None:
        return float(value) if isinstance(value, (int, float)) and value else None

    def _int(value: Any) -> int | None:
        try:
            return int(value) if value not in (None, '', 0) else None
        except (TypeError, ValueError):
            return None

    return RawProperty(
        fuente='mudafy',
        titulo=str(pub.get('title') or '') or None,
        descripcion=str(pub.get('description') or '') or None,
        direccion=direccion,
        precio=float(amount) if isinstance(amount, (int, float)) and amount else None,
        moneda=str(price.get('currency') or 'USD'),
        tipo_propiedad=_MUDAFY_KIND.get(kind, 'otro'),
        ambientes=_int(rooms.get('total_count')),
        banos=_int(rooms.get('bathrooms')),
        # `garages: 0` is real data (no parking), unlike a missing key.
        cocheras=int(rooms['garages']) if isinstance(rooms.get('garages'), int) else None,
        piso=_int(addr.get('floor_number')),
        m2_total=_num(dims.get('total_area')),
        m2_cubiertos=_num(dims.get('roofed_area')),
        antiguedad=_int(prop.get('construction_year')),
        imagenes=_mudafy_photo_urls(pub),
        url_origen=url,
        raw={'id': pub.get('id'), 'coordinates': (addr.get('coordinates') or {})},
    )


def _mudafy_publications(page: str) -> list[dict[str, Any]]:
    """Every `"publication":{…}` in the flight payload, deduplicated by id.

    Kept separate from the zona filtering so the scraper can tell "this page was
    empty" (the base is exhausted) apart from "this page held nothing for the
    searched zona" — which is NOT a reason to stop paging.
    """
    import json

    # The payload lives JSON-escaped inside a JS string literal.
    text = page.replace('\\"', '"').replace('\\\\', '\\')
    decoder = json.JSONDecoder()

    pubs: list[dict[str, Any]] = []
    seen: set[Any] = set()
    marker = '"publication":'
    idx = text.find(marker)
    while idx != -1:
        start = idx + len(marker)
        try:
            pub, end = decoder.raw_decode(text, start)
        except ValueError:
            idx = text.find(marker, start)
            continue
        idx = text.find(marker, end)

        if not isinstance(pub, dict):
            continue
        pub_id = pub.get('id')
        if pub_id is not None:
            if pub_id in seen:
                continue
            seen.add(pub_id)
        pubs.append(pub)
    return pubs


def _mudafy_zona_haystack(pub: dict[str, Any], prop: RawProperty) -> str:
    """What the zona guard matches against.

    `address.full_address` is free text the seller typed — on a live page 17 of
    25 rows carried no locality in it at all ("Belgrano 838", "LINEO 19"), so
    matching on the address alone silently drops real hits. The canonical
    locality rides in the `location_*` fields, which are filled on every row.
    """
    return _slugify(' '.join(str(part) for part in (
        prop.direccion,
        prop.titulo or '',
        pub.get('location_name') or '',
        pub.get('location_short_name') or '',
        # Already a slug, and it spells the barrio the site itself files it under.
        str(pub.get('location_slug') or '').replace('-', ' '),
    )))


def _parse_mudafy_payload(page: str, filters: ScrapingFilters) -> list[RawProperty]:
    """Every publication in the flight payload → RawProperty, zona-guarded.

    The city page already narrows most searches; this guard is what keeps a
    region-wide fallback honest.
    """
    phrase_parts = [
        parts for parts in
        ([_slugify(p) for p in z.split(',') if _slugify(p)] for z in _guard_phrases(filters))
        if parts
    ]
    return _mudafy_filter(_mudafy_publications(page), filters, phrase_parts)


def _mudafy_filter(
    pubs: list[dict[str, Any]],
    filters: ScrapingFilters,
    phrase_parts: list[list[str]],
) -> list[RawProperty]:
    results: list[RawProperty] = []
    for pub in pubs:
        prop = _norm_mudafy(pub)
        if prop is None:
            continue
        if phrase_parts:
            haystack = _mudafy_zona_haystack(pub, prop)
            if not any(all(part in haystack for part in parts) for parts in phrase_parts):
                continue
        # The payload never names the operation — the listing URL chose it.
        prop.tipo_operacion = filters.tipo_operacion or 'venta'
        results.append(prop)
    return results


async def _scrape_mudafy(
    filters: ScrapingFilters, on_progress: ProgressCb,
) -> list[RawProperty]:
    from app.core.config import settings
    await on_progress('mudafy', 'running', 0)

    phrase_parts = [
        parts for parts in
        ([_slugify(p) for p in z.split(',') if _slugify(p)] for z in _guard_phrases(filters))
        if parts
    ]

    results: list[RawProperty] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(
        timeout=30, follow_redirects=True,
        headers={'User-Agent': 'Mozilla/5.0 (compatible; PropSearchBot/1.0)'},
    ) as client:
        for base in _mudafy_search_bases(filters):
            base_ids: set[Any] = set()
            for url in _mudafy_search_urls(filters, settings.MUDAFY_MAX_PAGES, base):
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                except Exception:
                    # A derived city slug that 404s just means "try the region".
                    break

                pubs = _mudafy_publications(resp.text)
                # City pages serve no pagination hrefs and answer `/{N}-p` with
                # page 1 again, so exhaustion shows up as a page of repeats.
                fresh = [pub for pub in pubs if pub.get('id') not in base_ids]
                base_ids.update(pub.get('id') for pub in fresh)
                if not fresh:
                    break

                for prop in _mudafy_filter(fresh, filters, phrase_parts):
                    key = str(prop.url_origen or '')
                    if key and key in seen:
                        continue
                    if key:
                        seen.add(key)
                    results.append(prop)

                await on_progress('mudafy', 'running', len(results))

            # The precise page answered — sweeping the whole region on top of it
            # would only re-read what the zona guard already rejected.
            if results:
                break

    await on_progress('mudafy', 'done', len(results))
    return results


# ── MercadoLibre via public listing HTML ──────────────────────────────────────
# `api.mercadolibre.com/sites/MLA/search` now answers 403 forbidden without
# OAuth (verified live, every query). The public listing pages are still open,
# so we parse those — same route argenprop/inmobusqueda/mudafy already take.
_ML_HTML_BASE = 'https://inmuebles.mercadolibre.com.ar'
_ML_HTML_PAGE_SIZE = 48          # cards per listing page, counted live
_ML_HTML_URL_TIPO: dict[str, str] = {
    'departamento': 'departamentos', 'casa': 'casas', 'ph': 'ph',
    'oficina': 'oficinas', 'terreno': 'terrenos-y-lotes', 'local': 'locales',
}


def _ml_zona_slugs(filters: ScrapingFilters) -> list[str]:
    """Zona → the slugs to try, most specific first.

    A composite slug is usually unknown to MercadoLibre: `gonnet-la-plata`
    404s while the bare `gonnet` serves the real Gonnet listings (verified
    live). Without the bare fallback the whole slug fails and the zona
    candidate chain degrades to the localidad, answering a Gonnet search with
    the centre of La Plata. Same idea as ZonaProp's composite retry.

    Precision does not suffer: the caller keeps guarding on the ORIGINAL
    composite zona, so `gonnet` results still have to name La Plata too and a
    homonym in another province stays out.
    """
    zona = filters.localidades[0] if filters.localidades else (filters.zona or '')
    slugs = [_slugify(zona)]
    head = _slugify(zona.split(',')[0])
    if head and head not in slugs:
        slugs.append(head)
    return [x for x in slugs if x]


def _ml_search_urls(
    filters: ScrapingFilters, max_pages: int, zona_slug: str | None = None,
) -> Iterator[str]:
    """Listing URLs, page 1..N — lazily, so `max_pages=0` means "no cap" and the
    caller stops on the first page that yields nothing new. Later pages take the
    site's own `/_Desde_<offset>` suffix (49, 97, ... — read off its pagination
    links), which keeps the crawl on plain paths with no query string."""
    if zona_slug is None:
        zona_slug = _ml_zona_slugs(filters)[0]
    op = 'alquiler' if filters.tipo_operacion == 'alquiler' else 'venta'
    tipos = filters.tipos_propiedad or []
    tipo = _ML_HTML_URL_TIPO.get(tipos[0], 'inmuebles') if len(tipos) == 1 else 'inmuebles'
    base = f'{_ML_HTML_BASE}/{tipo}/{op}/{zona_slug}'
    yield base
    for n in count(1):
        if max_pages > 0 and n >= max_pages:
            return
        yield f'{base}/_Desde_{n * _ML_HTML_PAGE_SIZE + 1}'


# Dos números unidos por un separador de rango. MercadoLibre usa DOS, y en la
# MISMA card (relevado en vivo): " a " en ambientes y baños ("3 a 4 baños"),
# guion en la superficie ("139 - 166 m² cubiertos"). Cazar sólo el primero deja
# pasar los m², que es justo el dato del que cuelga el precio por m².
# Exige dígitos a ambos lados, así que "2 ambientes a estrenar" no engancha.
_ML_RANGE_RE = re.compile(r'\d[^\d]*(?:\sa\s|[-–—])[^\d]*\d')


def _ml_card_number(text: str) -> float | None:
    """"55.000" → 55000.0. The dot is a thousands separator on MercadoLibre,
    never a decimal point, so it is stripped rather than parsed.

    A RANGE reads as no number at all. An emprendimiento publishes the span of
    its units instead of one value per attribute ("1 a 4 ambs.", "33 m² a 92 m²
    cubiertos"), and stripping every non-digit glued both ends together: 14
    ambientes, 3392 m² cubiertos. Invented figures are worse than missing ones
    here — they reach the properties table and skew any price-per-m² read,
    whereas a None is already handled by `_matches_filters` without dropping
    the listing. Relevado en vivo: `/departamentos/venta/palermo` page 1 is
    48 of 48 emprendimientos, so this is the common path on a venta search,
    not an edge case.
    """
    if _ML_RANGE_RE.search(text):
        return None
    cleaned = re.sub(r'[^\d,]', '', text.replace('.', '')).replace(',', '.')
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


# El muro anti-bot de MercadoLibre. Marcadores del HTML servido a IPs de
# datacenter (relevado en vivo): la URL de listado redirige a una pantalla de
# verificación de cuenta de ~39 KB, contra 1.98 MB del listado real.
_ML_BLOCK_MARKERS = ('account-verification', 'account_verification')


def _ml_page_is_blocked(page: str) -> bool:
    """True cuando MercadoLibre sirvió el muro en vez del listado.

    Hace falta mirar el CUERPO porque el bloqueo llega como **200 con HTML
    válido**: no hay excepción que atrapar ni status que revisar. Sin esto, una
    IP bloqueada y una zona sin avisos son indistinguibles — que es exactamente
    cómo producción estuvo devolviendo `0 propiedades en mercadolibre` en cada
    búsqueda sin que nada lo dijera.

    Una página vacía NO es un bloqueo: una zona sin publicaciones es un
    resultado legítimo y confundirlos rompería la cadena de candidatos de zona.
    """
    if not page:
        return False
    low = page.lower()
    return any(m in low for m in _ML_BLOCK_MARKERS)


def _parse_mercadolibre_page(page: str, filters: ScrapingFilters) -> list[RawProperty]:
    """Every `li.ui-search-layout__item` on a listing page → RawProperty.

    Zona-guarded: an unknown slug does not 404 here, it quietly serves a wider
    set — `manuel-b-gonnet` returns a SANTA FE listing among the Buenos Aires
    ones (verified live) — so the searched zona is enforced over whatever the
    page returned, same as every other portal.
    """
    from bs4 import BeautifulSoup

    phrase_parts = [
        parts for parts in
        ([_slugify(p) for p in z.split(',') if _slugify(p)] for z in _guard_phrases(filters))
        if parts
    ]
    soup = BeautifulSoup(page, 'html.parser')
    results: list[RawProperty] = []
    for card in soup.select('li.ui-search-layout__item'):
        location_el = card.select_one('.poly-component__location')
        direccion = location_el.get_text(' ', strip=True) if location_el else ''
        if not direccion:
            continue
        # Guard on the LOCATION only, never the whole card. MercadoLibre repeats
        # the city in the title ("Departamento En Venta, 2 Dormitorios, Centro,
        # La Plata"), so a full-text haystack would let a Rosario listing
        # through on the strength of a La Plata title.
        if phrase_parts:
            haystack = _slugify(direccion)
            if not any(all(part in haystack for part in parts) for parts in phrase_parts):
                continue

        fraction = card.select_one('.andes-money-amount__fraction')
        precio = _ml_card_number(fraction.get_text(strip=True)) if fraction else None
        if not precio:
            continue
        symbol_el = card.select_one('.andes-money-amount__currency-symbol')
        symbol = symbol_el.get_text(strip=True) if symbol_el else 'US$'

        title_el = card.select_one('a.poly-component__title')
        headline_el = card.select_one('.poly-component__headline')
        headline = headline_el.get_text(' ', strip=True).lower() if headline_el else ''

        ambientes = banos = None
        m2_total = m2_cubiertos = None
        for item in card.select('.poly-attributes_list__item'):
            text = item.get_text(' ', strip=True).lower()
            value = _ml_card_number(text)
            if value is None:
                continue
            if 'amb' in text:
                ambientes = int(value)
            elif 'baño' in text or 'bano' in text:
                banos = int(value)
            elif 'cubierto' in text:
                m2_cubiertos = value
            elif 'm²' in text or 'm2' in text:
                m2_total = value

        img = card.select_one('img')
        imagen = str((img.get('data-src') or img.get('src') or '')) if img else ''

        tipo = next((t for t in ('casa', 'ph', 'local', 'oficina', 'terreno')
                     if t in headline), 'departamento')

        results.append(RawProperty(
            fuente='mercadolibre',
            titulo=title_el.get_text(' ', strip=True) if title_el else None,
            direccion=direccion,
            precio=precio,
            moneda='ARS' if symbol.strip() == '$' else 'USD',
            tipo_operacion='alquiler' if 'alquiler' in headline else 'venta',
            tipo_propiedad=tipo,  # type: ignore[arg-type]
            ambientes=ambientes,
            banos=banos,
            m2_total=m2_total,
            m2_cubiertos=m2_cubiertos,
            imagenes=[imagen] if imagen.startswith('http') else [],
            url_origen=str(title_el.get('href')) if title_el and title_el.get('href') else None,
        ))
    return results


async def _scrape_mercadolibre(
    filters: ScrapingFilters, on_progress: ProgressCb,
) -> list[RawProperty]:
    """Page MercadoLibre's public listing HTML.

    Replaces `_scrape_mercadolibre_api`: `api.mercadolibre.com/sites/MLA/search`
    answers 403 forbidden without OAuth for EVERY query (verified live), and the
    old code swallowed that into an empty list — so this portal reported
    `0 props` on every single search and read as "nothing matched" rather than
    "this source is broken".

    Egress goes through `settings.SCRAPER_PROXY_URL` when set, because the
    listing HTML is only open to RESIDENTIAL IPs: the same URL with the same
    headers returns 1.98 MB of listings from a home connection and 39 KB of
    account-verification from a datacenter one (measured live). Railway is a
    datacenter, so without a proxy production gets the wall — as a 200, which
    is why it read as an empty zona instead of a blocked source.
    """
    from app.core.config import settings
    import logging
    log = logging.getLogger(__name__)
    await on_progress('mercadolibre', 'running', 0)

    results: list[RawProperty] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(
        timeout=30, follow_redirects=True,
        proxy=settings.SCRAPER_PROXY_URL or None,
        headers={
            'User-Agent': (
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
            ),
            'Accept-Language': 'es-AR,es;q=0.9',
        },
    ) as client:
        for zona_slug in _ml_zona_slugs(filters):
            for url in _ml_search_urls(filters, settings.MERCADOLIBRE_MAX_PAGES, zona_slug):
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                except Exception as exc:
                    # 404 on an unknown slug → try the next slug. Anything else
                    # has to be SAYABLE: the REST scraper this replaced ate a
                    # 403 in a bare `except Exception: break` and still reported
                    # `done, 0`, so a dead portal looked exactly like a zona
                    # with no listings and stayed broken for weeks.
                    log.warning(
                        'mercadolibre: %s falló (%s) — se corta la paginación de este slug',
                        url, exc,
                    )
                    break

                if _ml_page_is_blocked(resp.text):
                    log.warning(
                        'mercadolibre: BLOQUEADO en %s — sirvió la pantalla de '
                        'verificación de cuenta en vez del listado (%d KB). La IP de '
                        'salida es de datacenter; configurá SCRAPER_PROXY_URL con un '
                        'proxy RESIDENCIAL. Esto NO es una zona sin avisos.',
                        url, len(resp.text) // 1024,
                    )
                    break

                new = 0
                for prop in _parse_mercadolibre_page(resp.text, filters):
                    key = str(prop.url_origen or '')
                    if key and key in seen:
                        continue
                    if key:
                        seen.add(key)
                    results.append(prop)
                    new += 1

                # Nothing new means the listing is exhausted, the page fell
                # outside the zona, or an out-of-range `_Desde_` offset
                # re-served page 1 — all three are reasons to stop paging.
                if new == 0:
                    break
                await on_progress('mercadolibre', 'running', len(results))
            if results:
                break  # this slug answered; no need for the broader one

    await on_progress('mercadolibre', 'done', len(results))
    return results


_REMAX_CDN = 'https://d1acdg20u0pmxj.cloudfront.net'
_REMAX_PHOTO_SIZE = '1080xAUTO'
_MAX_GALLERY = 20  # what the property card/detail UI ever shows


def _remax_photo_urls(item: dict[str, Any]) -> list[str]:
    """`photos[].rawValue` → browsable CDN URLs.

    The API ships each photo as a bare path with no size segment and no
    extension (`listings/<listingId>/<photoId>`); the rendered ficha requests
    that same asset with a size segment spliced in before the file name and a
    `.jpg` suffix appended — verified live, resolves `200 image/jpg`:

        {cdn}/listings/<listingId>/1080xAUTO/<photoId>.jpg

    A rawValue with no directory part has nowhere to splice the size into, so
    it's dropped rather than guessed into a broken `<img>`.
    """
    urls: list[str] = []
    for photo in item.get('photos') or []:
        raw = str((photo or {}).get('rawValue') or '').strip('/ ')
        if not raw:
            continue
        if raw.startswith('http'):
            urls.append(raw)
            continue
        directory, _, filename = raw.rpartition('/')
        if not directory:
            continue
        urls.append(f'{_REMAX_CDN}/{directory}/{_REMAX_PHOTO_SIZE}/{filename}.jpg')
    return urls[:_MAX_GALLERY]


_REMAX_LISTING_SLUG_RE = re.compile(r'remax\.com\.ar/listings/([^/?#]+)', re.I)


async def remax_gallery_from_url(url: str) -> list[str]:
    """Galería completa de una ficha de RE/MAX, por su API pública.

    remax.com.ar es una SPA de Angular: el HTML servido — aun renderizado con
    Playwright — trae UNA sola URL de foto, la del `og:image`. El resto las pide
    el front por API después de hidratar, así que NO están en el DOM y ningún
    scraping las va a encontrar. Verificado en vivo sobre una ficha real: 380KB
    de HTML, 3 URLs de imagen, dos de ellas banderas de países.

    La misma API pública que usa `_scrape_remax_api` sirve el aviso completo por
    slug, y `_remax_photo_urls` ya arma las URLs del CDN. Es el mismo patrón que
    `ficha._mercadolibre_gallery`: cuando el portal tiene API oficial, se le
    pregunta a la API en vez de pelearse con el DOM.

    Devuelve ``[]`` ante cualquier fallo — la galería es un extra y no puede
    tumbar un import que ya tiene los datos de la propiedad.
    """
    m = _REMAX_LISTING_SLUG_RE.search(url or '')
    if not m:
        return []
    slug = m.group(1)
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(f'{_REMAX_API_BASE}/listings/findBySlug/{slug}')
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return []
    item = data.get('data') if isinstance(data, dict) else None
    return _remax_photo_urls(item) if isinstance(item, dict) else []


def _norm_remax(item: dict[str, Any], zona: str) -> RawProperty | None:
    precio = item.get('price')
    if not precio:
        return None

    moneda = ((item.get('currency') or {}).get('value')) or 'USD'
    op_val = (item.get('operation') or {}).get('value', 'sale')
    tipo_operacion = 'alquiler' if op_val in ('rent', 'temporal') else 'venta'
    type_val = (item.get('type') or {}).get('value', '')
    tipo_propiedad = _REMAX_TYPE_VALUE_TO_TIPO.get(type_val, 'otro')
    direccion = item.get('displayAddress') or item.get('geoLabel') or zona

    return RawProperty(
        fuente='remax',
        titulo=item.get('title', ''),
        direccion=direccion,
        precio=float(precio),
        moneda=moneda,  # type: ignore[arg-type]
        tipo_operacion=tipo_operacion,  # type: ignore[arg-type]
        tipo_propiedad=tipo_propiedad,  # type: ignore[arg-type]
        ambientes=item.get('totalRooms'),
        banos=item.get('bathrooms'),
        m2_total=item.get('dimensionTotalBuilt') or None,
        m2_cubiertos=item.get('dimensionCovered') or None,
        amenities=[],
        imagenes=_remax_photo_urls(item),
        url_origen=f'https://www.remax.com.ar/listings/{item.get("slug", "")}',
    )


def _remax_matches_zona(item: dict[str, Any], zona: str) -> bool:
    """RE/MAX's `locations` query param uses an undocumented hierarchical
    encoding (colon-delimited, unclear positions) — rather than guess it
    wrong, results are paged unfiltered and matched by text against
    `geoLabel`/`displayAddress`, same guard idea as ZonaProp's
    `_item_matches_zona`."""
    phrase = _slugify(zona)
    if not phrase:
        return True
    haystack = _slugify(' '.join(
        str(item.get(k) or '') for k in ('geoLabel', 'displayAddress')
    ))
    return phrase in haystack


async def _remax_resolve_location(zona: str) -> str | None:
    """Free-text zona → RE/MAX `locations` filter string, via the portal's
    own autocomplete API. This is what makes zona searches actually return
    results: without a server-side location filter the API serves the newest
    listings NATIONWIDE, and a specific zona almost never appears in that
    sample (the original "always 0 results" failure).

    Result labels carry `<b>` highlight tags and fuzzy matches can outrank
    exact ones, so a candidate must have EVERY comma-part of the query in its
    slugified label (tags stripped) AND its first label component must EQUAL
    the query head — same exact-head rule the InmoBusqueda resolver uses.
    Containment alone is not enough: RE/MAX's only literal "Casco Urbano" is
    the gated community "Los Eucaliptus Casco Urbano", a real level-3 id that
    serves zero listings. Rejecting it lets the zona candidate chain degrade
    instead of burning the attempt on a plausible-looking wrong place.

    Among exact-head candidates the DEEPEST level wins. RE/MAX ranks the
    partido above the localidad, and "La Plata" the city (level 3, `cityId`)
    is what the user means — level 2 (`countyId`) drags in City Bell, Villa
    Elisa, Gonnet, Tolosa and Los Hornos, which measured live as 77 of 200
    listings. Returns None on no confident match or API failure — callers
    fall back to nationwide paging plus the text zona guard."""
    query_parts = [p.strip() for p in zona.split(',') if p.strip()]
    if not query_parts:
        return None
    cache_key = _slugify(zona)
    if cache_key in _REMAX_LOCATION_CACHE:
        return _REMAX_LOCATION_CACHE[cache_key]

    location: str | None = None
    try:
        from urllib.parse import quote
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                f'{_REMAX_API_BASE}/search/findAll/{quote(query_parts[0])}',
                params={'level': 1},
            )
            resp.raise_for_status()
            geo_results = (resp.json().get('data') or {}).get('geoSearch') or []
        wanted = [_slugify(p) for p in query_parts]
        best_rank: tuple[int, int] | None = None
        for idx, entry in enumerate(geo_results):
            label = re.sub(r'</?b>', '', str(entry.get('label') or ''))
            label_slug = _slugify(label)
            if not all(part in label_slug for part in wanted):
                continue
            level = entry.get('level') or 0
            id_field = _REMAX_LEVEL_ID_FIELD.get(level)
            loc_id = entry.get(id_field) if id_field else None
            if not loc_id:
                continue
            # An exact head is the strong signal, and among those the DEEPEST
            # level wins — that is what picks "La Plata, La Plata" (level 3,
            # the localidad) over "La Plata, Buenos Aires" (level 2, the
            # partido). Without an exact head the portal simply spells the
            # zona more fully than the user did ("Gonnet" → "Manuel B
            # Gonnet"), so its own relevance order decides.
            rank = (1, level) if _slugify(label.split(',')[0]) == wanted[0] else (0, -idx)
            if best_rank is None or rank > best_rank:
                best_rank = rank
                slots = [''] * _REMAX_LOCATION_SLOTS
                slots[level] = str(loc_id)
                location = 'in:' + ':'.join(slots)
    except Exception:
        return None  # transient failure — don't cache, retry next search

    _REMAX_LOCATION_CACHE[cache_key] = location
    return location


async def _scrape_remax_api(
    filters: ScrapingFilters,
    on_progress: ProgressCb,
) -> list[RawProperty]:
    from app.core.config import settings

    zona = filters.zona or 'Buenos Aires'
    op_id = 2 if filters.tipo_operacion == 'alquiler' else 1
    tipos = filters.tipos_propiedad or []

    in_params = [f'operationId:{op_id}']
    if len(tipos) == 1 and tipos[0] in _REMAX_TYPE_IDS:
        ids_csv = ','.join(str(i) for i in _REMAX_TYPE_IDS[tipos[0]])
        in_params.append(f'typeId:{ids_csv}')

    # Server-side location filter — the fan-out unit (localidad on the map
    # path, zona on the chat path) is what the user actually asked for.
    loc_zona = filters.localidades[0] if filters.localidades else zona
    location = await _remax_resolve_location(loc_zona)

    await on_progress('remax', 'running', 0)

    # 0 = uncapped: page until `totalPages`. That is safe ONLY once `locations`
    # bounded the result set to the zona server-side. Without it the API serves
    # the newest listings NATIONWIDE (66k+, verified live) and the zona is
    # matched client-side on text — sweeping all of that means hundreds of
    # round-trips to keep a handful of rows, so the unlocated fallback keeps its
    # own ceiling. An explicit REMAX_MAX_PAGES always wins over that fallback.
    max_pages = settings.REMAX_MAX_PAGES
    if max_pages <= 0 and location is None:
        max_pages = max(1, settings.REMAX_UNLOCATED_MAX_PAGES)
    page_size = max(1, settings.REMAX_PAGE_SIZE)

    results: list[RawProperty] = []
    async with httpx.AsyncClient(timeout=20) as client:
        page = 0
        while max_pages <= 0 or page < max_pages:
            params: dict[str, Any] = {
                'page': page, 'pageSize': page_size,
                'sort': '-createdAt', 'in': in_params,
            }
            if location:
                params['locations'] = location
            try:
                resp = await client.get(
                    f'{_REMAX_API_BASE}/listings/findAllWithEntrepreneurships',
                    params=params,
                )
                resp.raise_for_status()
                body = resp.json()
            except Exception:
                break

            # Response is double-nested: {"data": {"data": [...items...],
            # "page":.., "totalPages":..}, "code":200, "message":.., "errors":..}
            # — confirmed against a real request.
            paging = body.get('data') or {}
            items = paging.get('data', [])
            if not items:
                break

            for item in items:
                if not _remax_matches_zona(item, zona):
                    continue
                prop = _norm_remax(item, zona)
                if prop is not None:
                    results.append(prop)

            if page + 1 >= paging.get('totalPages', 0):
                break
            page += 1
            await on_progress('remax', 'running', len(results))

    await on_progress('remax', 'done', len(results))
    return results


def _extract_instagram_handle(website: str | None) -> str | None:
    if not website:
        return None
    m = re.search(r'instagram\.com/([A-Za-z0-9_.]+)', website)
    return m.group(1) if m else None


def _norm_googlemaps_agency(item: dict[str, Any], zona: str) -> Agency | None:
    import uuid
    name = item.get('title') or item.get('name', '')
    if not name:
        return None
    website = item.get('website') or ''
    # try social media links first, then fallback to website URL
    social = item.get('socialMediaProfiles') or {}
    ig_handle = (
        _extract_instagram_handle(social.get('instagram'))
        or _extract_instagram_handle(website)
    )
    return Agency(
        id=str(uuid.uuid4()),
        nombre=name,
        direccion=item.get('address'),
        telefono=item.get('phone'),
        sitio_web=website or None,
        google_maps_url=item.get('url'),
        instagram_handle=ig_handle,
        calificacion=item.get('totalScore'),
        zona=zona,
    )


def _norm_instagram(item: dict[str, Any]) -> RawProperty | None:
    caption = item.get('caption') or item.get('text', '')
    if not caption:
        return None
    images = []
    if item.get('displayUrl'):
        images.append(item['displayUrl'])
    for img in item.get('images') or []:
        if isinstance(img, str):
            images.append(img)
        elif isinstance(img, dict) and img.get('url'):
            images.append(img['url'])
    return RawProperty(
        fuente='instagram',
        titulo=caption[:120],
        descripcion=caption,
        direccion='',
        precio=None,
        moneda='USD',
        tipo_operacion='venta',
        tipo_propiedad='otro',
        ambientes=None,
        m2_total=None,
        m2_cubiertos=None,
        antiguedad=None,
        amenities=[],
        imagenes=images[:10],
        url_origen=item.get('url') or item.get('postUrl', ''),
    )


# ── Direct website scraper (no Apify, uses httpx + BeautifulSoup) ────────────

# Tokens that flag an image as UI chrome rather than a property photo.
_IMG_JUNK = (
    'logo', 'icon', 'favicon', 'sprite', 'bandera', 'flag', 'banner',
    'placeholder', 'blank', 'pixel', 'avatar', 'whatsapp', 'tile.osm',
)


# Contenedores cuyas fotos NO son de la propiedad de esta página: bloques de
# "propiedades similares/relacionadas" y widgets de sidebar. Se podan ANTES de
# recolectar, porque filtrar despues por nombre de archivo es imposible: la foto
# de una propiedad similar es una foto de propiedad legitima y no matchea ningun
# patron de basura. Verificado en vivo sobre una ficha de Argenprop, donde
# `ul.similar-properties__list` metia 4 propiedades ajenas en cada import.
_FOREIGN_CONTAINER_PATTERNS = (
    'similar', 'related', 'relacionad', 'recomend', 'sugerid',
    'otras-propiedades', 'otras_propiedades', 'form-widget',
    # Isotipo del portal: InmoBusqueda lo sirve como .jpg desde
    # `div.logoresultados`, así que por extensión es indistinguible de una foto.
    'logo',
    # El mapa de la ficha no es una foto de la propiedad. ZonaProp lo pone en
    # `div.static-map-container`, FUERA del footer.
    'static-map', 'article-map', 'map-container',
)

# Chrome del sitio. `_visible_text` ya los podaba; esta función no, y por eso se
# colaban el logo del portal, el sello de data fiscal y el QR de registro.
# Se podan por TAG, nunca por clase: un `div.gallery-header` es parte de la
# ficha y llevárselo puesto sería cambiar un bug por otro peor.
_CHROME_TAGS = ('header', 'footer', 'nav')

# `style="background: center url(...)"`. El visor de Argenprop no usa <img>:
# sin esto la ficha queda con UNA foto (la del og:image) aunque el DOM traiga 5.
_CSS_URL_RE = re.compile(r'url\(\s*[\'"]?([^\'")]+)[\'"]?\s*\)', re.I)


def _is_foreign_container(tag: Any) -> bool:
    ident = f"{' '.join(tag.get('class') or [])} {tag.get('id') or ''}".lower()
    return any(p in ident for p in _FOREIGN_CONTAINER_PATTERNS)


def _url_dir(url: str) -> str:
    """El 'directorio' de una URL de imagen — todo hasta la ultima barra."""
    return url.rsplit('/', 1)[0] if '/' in url else url


def _extract_images_from_html(html: str, base: str, anchor_to_og: bool = False) -> list[str]:
    """Property photos visible in server HTML: og:image/twitter:image metas plus
    content <img> tags (honoring lazy-load attrs and srcset) and inline
    `background: url(...)` photos, junk filtered.

    Foreign blocks (similar/related listings, sidebar widgets) are pruned first —
    see `_FOREIGN_CONTAINER_PATTERNS`.

    ``anchor_to_og`` is for pages holding ONE property (fichas): the og:image is
    by definition this page's main photo, so photos sharing its directory are
    this property's and the rest are not. It is opt-in because a real-estate
    agency LISTING legitimately shows many properties, and anchoring there would
    wipe the catalog. It also degrades safely: CDNs that give every photo its own
    hashed directory yield no grouping evidence, and then nothing is filtered —
    losing real photos is worse than letting a foreign one through.
    """
    from bs4 import BeautifulSoup  # type: ignore[import-untyped]

    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(list(_CHROME_TAGS)):
        tag.decompose()
    for tag in soup.find_all(_is_foreign_container):
        tag.decompose()

    imgs: list[str] = []
    seen: set[str] = set()

    def _add_img(raw: str) -> None:
        src = (raw or '').strip()
        if not src or src.startswith('data:'):
            return
        full = src if src.startswith('http') else base.rstrip('/') + '/' + src.lstrip('/')
        low = full.lower()
        if not any(ext in low for ext in ('.jpg', '.jpeg', '.png', '.webp')):
            return
        if any(j in low for j in _IMG_JUNK):
            return
        if full not in seen:
            seen.add(full)
            imgs.append(full)

    og_image = ''
    for meta in soup.find_all('meta'):
        key = str(meta.get('property') or meta.get('name') or '').lower()
        if key in ('og:image', 'og:image:url', 'og:image:secure_url', 'twitter:image', 'twitter:image:src'):
            content = str(meta.get('content') or '')
            if key.startswith('og:image') and content and not og_image:
                og_image = content
            _add_img(content)

    for el in soup.find_all(style=True):
        for raw in _CSS_URL_RE.findall(str(el.get('style') or '')):
            _add_img(raw)

    for img in soup.find_all('img'):
        srcset = img.get('srcset') or img.get('data-srcset') or ''
        srcset_url = srcset.split(',')[-1].strip().split(' ')[0] if srcset else ''
        for raw in (
            img.get('data-src'), img.get('data-lazy-src'), img.get('data-original'),
            img.get('data-echo'), srcset_url, img.get('src'),
        ):
            if raw:
                _add_img(str(raw))
                break

    if anchor_to_og and og_image:
        anchor = _url_dir(og_image if og_image.startswith('http')
                          else base.rstrip('/') + '/' + og_image.lstrip('/'))
        owned = [i for i in imgs if _url_dir(i) == anchor]
        # Un solo match es el propio og:image: no prueba que el portal agrupe
        # por directorio, asi que filtrar ahi seria adivinar y perder fotos.
        if len(owned) > 1:
            imgs = owned

    return imgs[:20]


# Below this many httpx-parsed images a ficha likely hides its gallery behind JS
# (e.g. only og:image in server HTML) and is worth a headless-browser pass.
_GALLERY_MIN_IMGS = 4


async def harvest_page_images(urls: list[str], render_budget: int = 8) -> dict[str, list[str]]:
    """Gallery per URL, for pages known to hold a SINGLE property (fichas).

    All URLs are fetched concurrently with httpx — most real-estate fichas
    (tokko/xintel) carry the full gallery in server HTML. Playwright rendering is
    reserved for the few pages where httpx found under ``_GALLERY_MIN_IMGS``
    images, capped at ``render_budget`` since each render costs seconds.
    Returns ``{url: [image_urls]}``; URLs that fail are simply absent.
    """
    out: dict[str, list[str]] = {}
    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; PropSearchBot/1.0)',
        'Accept-Language': 'es-AR,es;q=0.9',
    }
    sem = asyncio.Semaphore(5)

    async def _fetch(client: httpx.AsyncClient, u: str) -> tuple[str, list[str]]:
        async with sem:
            try:
                resp = await client.get(u)
                resp.raise_for_status()
                # Estas URLs son fichas de UNA propiedad (ver docstring), así que
                # el og:image sirve de ancla para descartar fotos ajenas.
                return u, _extract_images_from_html(resp.text, u, anchor_to_og=True)
            except Exception:
                return u, []

    async with httpx.AsyncClient(headers=headers, timeout=15, follow_redirects=True) as client:
        for u, imgs in await asyncio.gather(*(_fetch(client, u) for u in urls)):
            if imgs:
                out[u] = imgs

    needs_render = [u for u in urls if len(out.get(u, [])) < _GALLERY_MIN_IMGS][:render_budget]
    if needs_render:
        try:
            rendered = await _render_gallery_images(needs_render)
            for u, gallery in rendered.items():
                if len(gallery) > len(out.get(u, [])):
                    out[u] = gallery
        except Exception:
            pass

    return out


async def _render_gallery_images(urls: list[str]) -> dict[str, list[str]]:
    """Load pages in a headless browser and collect gallery photos from network
    image responses. This is the only way to capture galleries on JS-rendered
    real-estate sites (xintel/tokko), where photos live in CSS `background-image`
    or are lazy-loaded and never appear in the server HTML.

    Returns ``{url: [image_urls]}``. On any failure (Playwright missing, launch
    error) it returns whatever it gathered so far so callers fall back to httpx.
    """
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return {}

    out: dict[str, list[str]] = {}

    def _keep(u: str) -> str | None:
        clean = u.split()[0].rstrip('")\'')
        low = clean.lower()
        if not any(ext in low for ext in ('.jpg', '.jpeg', '.png', '.webp')):
            return None
        if any(j in low for j in _IMG_JUNK):
            return None
        return clean

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                for u in urls:
                    found: list[str] = []
                    seen: set[str] = set()
                    page = await browser.new_page()

                    def _on_response(resp: Any) -> None:
                        try:
                            ct = resp.headers.get('content-type', '')
                            if not ct.startswith('image/') or 'svg' in ct:
                                return
                            img = _keep(resp.url)
                            if img and img not in seen:
                                seen.add(img)
                                found.append(img)
                        except Exception:
                            pass

                    page.on('response', _on_response)
                    try:
                        await page.goto(u, wait_until='networkidle', timeout=25000)
                        for _ in range(6):  # trigger lazy-loaded photos
                            await page.mouse.wheel(0, 700)
                            await page.wait_for_timeout(250)
                        await page.wait_for_timeout(800)
                    except Exception:
                        pass
                    finally:
                        await page.close()
                    if found:
                        out[u] = found[:20]
            finally:
                await browser.close()
    except Exception:
        return out

    return out


# ── Single-page HTML fetchers for WAF'd portals ───────────────────────────────
#
# Two escalations for the same problem: a portal that 403s a plain httpx GET.
# Neither is used for search (that path runs its own actor per portal) — both
# exist for the one-page fetches, where paying a full actor run up front would
# be absurd for the majority of sites that answer a plain GET just fine.
#
# The caller owns the ladder and the blocked/not-blocked decision; these two
# only know how to fetch. Both return None on failure instead of raising, so a
# missing Chromium or an unset Apify token degrades to the next rung rather
# than killing the import.

_BROWSER_UA = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
)


async def render_page_html(url: str, user_agent: str | None = None) -> str | None:
    """Load one page in headless Chromium and return its rendered HTML.

    Gets past the plain-UA blocks and the JS-hydration walls that make httpx
    come back empty. NOT a guaranteed WAF bypass: Argenprop's AWS WAF Bot
    Control challenges default headless Chromium too (see the module header),
    which is exactly why the caller checks the returned HTML before trusting it.

    `user_agent` lo fija el llamador cuando el portal sirve markup distinto
    según el dispositivo (MercadoLibre manda 5 fotos al desktop y 28 al mobile).
    """
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return None

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(args=['--disable-blink-features=AutomationControlled'])
            try:
                context = await browser.new_context(
                    user_agent=user_agent or _BROWSER_UA,
                    locale='es-AR',
                    viewport={'width': 1440, 'height': 900},
                )
                page = await context.new_page()
                try:
                    await page.goto(url, wait_until='domcontentloaded', timeout=25000)
                    # WAF challenges resolve themselves a beat after load; the
                    # rendered DOM is worthless if we snapshot it mid-challenge.
                    await page.wait_for_timeout(2500)
                    return str(await page.content())
                except Exception:
                    return None
                finally:
                    await page.close()
            finally:
                await browser.close()
    except Exception:
        return None


async def fetch_page_html_via_actor(url: str) -> str | None:
    """Last resort: fetch one page through Apify's website-content-crawler.

    This is the only fetcher verified to get past Argenprop's WAF, and the only
    one that costs real money — hence last. `htmlTransformer: 'none'` for the
    same reason the search path sets it: the Readability DOM drops the photo
    carousel, and the gallery is half the point of a ficha.
    """
    from app.core.config import settings
    if settings.APIFY_USE_MOCK or not settings.APIFY_API_TOKEN:
        return None

    service = ApifyService(api_token=settings.APIFY_API_TOKEN)
    try:
        pages = await service._run_actor('ficha_propio', _ACTORS['website'], {
            'startUrls': [{'url': url}],
            'maxCrawlPages': 1,
            'maxCrawlDepth': 0,
            'crawlerType': 'playwright:chrome',
            'saveHtml': True,
            'htmlTransformer': 'none',
        })
    except Exception:
        return None
    finally:
        await service._client.aclose()

    for page in pages:
        if html := page.get('html'):
            return str(html)
    return None


async def _scrape_website_direct(url: str, on_progress: ProgressCb) -> list[dict[str, str]]:
    from bs4 import BeautifulSoup  # type: ignore[import-untyped]

    label = url.replace('https://', '').replace('http://', '').split('/')[0]
    await on_progress(f'web:{label}', 'running', 0)

    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; PropSearchBot/1.0)',
        'Accept-Language': 'es-AR,es;q=0.9',
    }

    def parse_page(html: str, base: str) -> tuple[str, list[str]]:
        from urllib.parse import urljoin, urlparse
        soup = BeautifulSoup(html, 'html.parser')
        base_domain = urlparse(base).netloc

        # Harvest images before stripping markup. Real-estate sites are usually
        # JS-rendered, so the reliable server-side photo is og:image; content <img>
        # tags are often UI chrome (flags, logos) or lazy-loaded via data-* attrs.
        imgs = _extract_images_from_html(html, base)

        # Remove noise before processing links
        for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
            tag.decompose()

        # Render same-domain anchors as markdown links so the LLM can see individual property URLs
        for a in soup.find_all('a', href=True):
            href = str(a.get('href', ''))
            if not href or href.startswith(('#', 'mailto:', 'tel:', 'javascript:')):
                continue
            full_href = urljoin(base, href) if not href.startswith('http') else href
            if urlparse(full_href).netloc != base_domain:
                continue
            link_text = a.get_text(strip=True)
            if link_text:
                a.replace_with(f'[{link_text}]({full_href})')

        text = soup.get_text(separator='\n')
        return re.sub(r'\n{3,}', '\n\n', text).strip(), imgs[:20]

    pages: list[dict] = []
    visited: set[str] = set()

    async with httpx.AsyncClient(headers=headers, timeout=15, follow_redirects=True) as client:
        # Fetch the main page
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            text, imgs = parse_page(resp.text, url)
            if text:
                pages.append({'url': url, 'text': text[:8000], 'images': imgs})
            visited.add(url)
        except Exception:
            await on_progress(f'web:{label}', 'error', 0)
            return []

        # Find property-related sub-pages (up to 5)
        soup = BeautifulSoup(resp.text, 'html.parser')
        prop_keywords = ['propiedad', 'propiedades', 'inmueble', 'venta', 'alquiler', 'listing']
        sub_urls: list[str] = []
        for a in soup.find_all('a', href=True):
            href = str(a['href'])
            if any(k in href.lower() for k in prop_keywords):
                full = href if href.startswith('http') else url.rstrip('/') + '/' + href.lstrip('/')
                if full not in visited and label in full:
                    sub_urls.append(full)
                    if len(sub_urls) >= 5:
                        break

        for sub_url in sub_urls:
            try:
                r = await client.get(sub_url)
                r.raise_for_status()
                t, sub_imgs = parse_page(r.text, sub_url)
                if t:
                    pages.append({'url': sub_url, 'text': t[:8000], 'images': sub_imgs})
                visited.add(sub_url)
            except Exception:
                continue

    # Enrich with full galleries via a headless browser. JS-rendered sites hide
    # their photos from the httpx HTML, so this recovers the real galleries.
    # Falls back silently to the httpx-parsed images if Playwright is unavailable.
    try:
        rendered = await _render_gallery_images([p['url'] for p in pages])
        for p in pages:
            gallery = rendered.get(p['url'])
            if gallery:
                p['images'] = gallery
    except Exception:
        pass

    await on_progress(f'web:{label}', 'done', len(pages))
    return pages


# ── Base interface ─────────────────────────────────────────────────────────────

class BaseApifyService(ABC):
    @abstractmethod
    async def scrape_source(
        self,
        source: str,
        filters: ScrapingFilters,
        on_progress: ProgressCb,
    ) -> list[RawProperty]:
        ...

    @abstractmethod
    async def scrape_website(
        self,
        url: str,
        on_progress: ProgressCb,
    ) -> list[dict[str, str]]:
        """Returns list of {url, text} dicts — one per crawled page."""
        ...

    @abstractmethod
    async def scrape_instagram_profile(
        self,
        handle: str,
        on_progress: ProgressCb,
    ) -> list[RawProperty]:
        ...

    @abstractmethod
    async def scrape_agencies(
        self,
        zona: str,
        on_progress: ProgressCb,
    ) -> list[Agency]:
        ...


# ── Real implementation ────────────────────────────────────────────────────────

class ApifyService(BaseApifyService):
    def __init__(self, api_token: str) -> None:
        self._token = api_token
        # The token travels as a HEADER, never as `?token=...`. httpx formats
        # the full URL into every error it raises, so a query-string
        # credential ends up verbatim in the funnel's `stop_reason` and in the
        # log — which is exactly how a live 403 published this account's key.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(_HTTP_TIMEOUT, connect=_HTTP_CONNECT_TIMEOUT),
            headers={'Authorization': f'Bearer {api_token}'},
        )

    # Map internal property types → ZonaProp actor values
    _PROP_TYPE_MAP = {
        'departamento': 'apartment',
        'casa': 'house',
        'ph': 'ph',
        'local': 'commercial',
        'oficina': 'commercial',
        'terreno': 'land',
        'otro': 'all',
    }

    # URL slugs for ZonaProp search URLs
    _ZP_URL_SLUG = {
        'departamento': 'departamentos',
        'casa': 'casas',
        'ph': 'ph',
        'local': 'locales-comerciales',
        'oficina': 'oficinas',
        'terreno': 'terrenos',
    }

    # URL slugs for MercadoLibre search URLs
    _ML_URL_SLUG = {
        'departamento': 'departamentos',
        'casa': 'casas',
        'ph': 'ph',
        'local': 'locales-y-fondos-de-comercio',
        'oficina': 'oficinas-y-locales',
        'terreno': 'terrenos-y-lotes',
    }

    def _input_for(self, source: str, filters: ScrapingFilters) -> dict[str, Any]:
        zona = filters.zona or 'Buenos Aires'
        op = filters.tipo_operacion or 'venta'

        if source == 'zonaprop':
            # Portal-known localidad slug when available (ADR-1/spec: unknown
            # barrio slugs 404/redirect nationwide on ZonaProp) — else the
            # existing barrio slug, unchanged for the chat path.
            search_url = _zonaprop_search_url(filters)
            from app.core.config import settings
            input_data: dict[str, Any] = {'searchUrl': search_url}
            if settings.ZONAPROP_MAX_RESULTS > 0:
                input_data['maxResults'] = settings.ZONAPROP_MAX_RESULTS
            return input_data

        if source == 'mercadolibre':
            op_slug = 'alquiler' if op == 'alquiler' else 'venta'
            zona_slug = _slugify(zona)
            tipos = filters.tipos_propiedad
            cat_slug = self._ML_URL_SLUG.get(tipos[0], 'inmuebles') if len(tipos) == 1 else 'inmuebles'
            url = f'https://inmuebles.mercadolibre.com.ar/{cat_slug}/{op_slug}/{zona_slug}/'
            return {'searchUrl': url, 'maxItems': 10}

        if source == 'googlemaps':
            # The place cap used to be a hard-coded 20 here, so raising
            # GOOGLEMAPS_MAX_PLACES left this path capped anyway — the knob lied.
            from app.core.config import settings
            gm_input: dict[str, Any] = {
                'searchStringsArray': [f'inmobiliarias en {zona}'],
                'language': 'es',
                'countryCode': 'ar',
            }
            if settings.GOOGLEMAPS_MAX_PLACES > 0:
                gm_input['maxCrawledPlacesPerSearch'] = settings.GOOGLEMAPS_MAX_PLACES
            return gm_input

        if source == 'instagram':
            # Uses handles stored in filters or falls back to empty
            handles = getattr(filters, 'instagram_handles', None) or []
            from app.core.config import settings
            ig_input: dict[str, Any] = {'usernames': handles}
            if settings.INSTAGRAM_RESULTS_LIMIT > 0:
                ig_input['resultsLimit'] = settings.INSTAGRAM_RESULTS_LIMIT
            return ig_input

        return {}

    async def _run_actor(
        self,
        source: str,
        actor_id: str,
        input_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        run_url = f'{_APIFY_BASE}/acts/{actor_id}/runs'

        # Start run
        resp = await self._client.post(run_url, json=input_data)
        resp.raise_for_status()
        run_id = resp.json()['data']['id']

        # Poll until done. The run object we poll already carries `usageTotalUsd`,
        # so booking the spend costs no extra request — just don't discard it.
        status_url = f'{_APIFY_BASE}/actor-runs/{run_id}'
        elapsed = 0.0
        run_data: dict[str, Any] = {}
        while elapsed < _TIMEOUT:
            await asyncio.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL
            status_resp = await self._client.get(status_url)
            status_resp.raise_for_status()
            run_data = status_resp.json()['data']
            status = run_data['status']
            if status == 'SUCCEEDED':
                break
            if status in ('FAILED', 'ABORTED', 'TIMED-OUT'):
                # Apify bills failed runs too — book the spend before bailing out.
                record_run_cost(source, run_data.get('usageTotalUsd'))
                raise RuntimeError(f'Apify run {run_id} ended with status {status}')

        # Reached on SUCCEEDED and on poll-timeout alike; on timeout this books
        # the usage as of the last poll, which is the truest number we have.
        record_run_cost(source, run_data.get('usageTotalUsd'))

        # Fetch dataset
        dataset_url = f'{_APIFY_BASE}/actor-runs/{run_id}/dataset/items'
        items_resp = await self._client.get(dataset_url, params={'format': 'json'})
        items_resp.raise_for_status()
        return items_resp.json()  # type: ignore[no-any-return]

    async def scrape_source(
        self,
        source: str,
        filters: ScrapingFilters,
        on_progress: ProgressCb,
    ) -> list[RawProperty]:
        """Scrape one portal, walking the zona's candidate chain until a
        candidate returns something.

        Resolving a barrio fails in three ways and only the last is visible
        from inside a resolver: no autocomplete match at all (Argenprop
        answers "Casco Urbano" with a barrio in San Luis), a match that is
        the wrong place (RE/MAX's only literal one is the gated community
        "Los Eucaliptus Casco Urbano"), or a good slug over an empty listing.
        Text inspection cannot separate the second from a real hit, so the
        retry keys on the only honest signal — zero results — and lives here,
        the one entry point every portal shares.
        """
        results: list[RawProperty] = []
        # Stamped ONCE, before the walk: every candidate below rewrites `zona`,
        # and the guard must keep answering the question that was asked.
        # An explicit value from the caller wins — it already scoped the guard.
        filters = filters.model_copy(
            update={'zona_pedida': filters.zona_pedida or filters.zona},
        )
        for candidate in zona_candidates(filters.zona or '') or [filters.zona or '']:
            # `localidades` outranks `zona` in every portal's resolver, so a
            # stale barrio left there would re-resolve the same dead slug.
            update: dict[str, Any] = {'zona': candidate}
            if filters.localidades:
                update['localidades'] = [candidate]
            results = await self._scrape_source_once(
                source, filters.model_copy(update=update), on_progress,
            )
            if results:
                break
        return results

    async def _scrape_source_once(
        self,
        source: str,
        filters: ScrapingFilters,
        on_progress: ProgressCb,
    ) -> list[RawProperty]:
        from app.core.config import settings

        if source == 'mercadolibre':
            return await _scrape_mercadolibre(filters, on_progress)
        if source == 'remax':
            return await _scrape_remax_api(filters, on_progress)
        if source == 'argenprop':
            return await self._scrape_argenprop(filters, on_progress)
        if source == 'inmobusqueda':
            return await _scrape_inmobusqueda(filters, on_progress)
        if source == 'mudafy':
            return await _scrape_mudafy(filters, on_progress)
        if source == 'zonaprop' and not settings.ZONAPROP_USE_APIFY:
            # Default path. `ZONAPROP_USE_APIFY=true` hands ZonaProp back to the
            # Apify actor below, which is left intact for exactly that reason.
            return await _scrape_zonaprop_direct(filters, on_progress)

        actor_id = _ACTORS.get(source)
        if not actor_id:
            return []

        await on_progress(source, 'running', 0)

        if source == 'zonaprop':
            results, _funnel = await self._scrape_zonaprop_paginated(actor_id, filters)

            # Composite slug ("city-bell-la-plata") unknown to ZonaProp →
            # nationwide redirect → the guard rejects everything (or the page
            # 404s and the actor returns nothing). Retry ONCE with the plain
            # localidad slug, which is what the portal's own URL uses
            # (.../departamentos-venta-city-bell-...), rather than giving up.
            #
            # Gated on the SLUG being composite, not on which path built it:
            # this used to require `filters.localidades`, so a typed query —
            # which leaves it empty — never reached the retry even though it
            # hits the identical redirect. Note the retry strips to the FIRST
            # part (the barrio), never degrading to the partido; widening the
            # search is the candidate chain's job, not this one's.
            composite = filters.localidades[0] if filters.localidades else (filters.zona or '')
            if ',' in composite and (not results or _funnel.redirect_suspected):
                plain = composite.split(',')[0].strip()
                # `localidades` outranks `zona` in `_input_for`, so a stale
                # composite left there would rebuild the same dead slug.
                update: dict[str, Any] = {'zona': plain}
                if filters.localidades:
                    update['localidades'] = [plain]
                plain_filters = filters.model_copy(update=update)
                retry, _retry_funnel = await self._scrape_zonaprop_paginated(
                    actor_id, plain_filters,
                )
                # The retry is an attempt to do better, never a commitment to
                # its result: if the plain slug turns out to be the worse one,
                # keep what the first pass already found.
                if len(retry) > len(results):
                    results = retry
        else:
            raw_items = await self._run_actor(source, actor_id, self._input_for(source, filters))
            results = []
            for item in raw_items:
                prop: RawProperty | None = None
                if source == 'instagram':
                    prop = _norm_instagram(item)
                # googlemaps uses scrape_agencies, not scrape_source
                if prop is not None:
                    results.append(prop)

        await on_progress(source, 'done', len(results))
        return results

    # The crawlerbros actor's browser dies after page 1 (every run returns one
    # page regardless of maxResults), so WE paginate: one actor run per
    # `...-pagina-N.html` URL.
    #
    # 30 is only the SEED for the cap arithmetic. The real page size is
    # whatever page 1 returns — hardcoding it made the "short page = last
    # page" rule fire on page 1 of any portal serving fewer, cutting a
    # five-page listing down to one.
    _ZP_PAGE_SIZE = 30

    async def _scrape_zonaprop_paginated(
        self, actor_id: str, filters: ScrapingFilters,
    ) -> tuple[list[RawProperty], ZonaPropFunnel]:
        from app.core.config import settings
        cap = settings.ZONAPROP_MAX_RESULTS
        base_input = self._input_for('zonaprop', filters)
        base_url: str = base_input['searchUrl']
        phrases = _guard_phrases(filters)
        # `cap <= 0` = no cap: the loop below already stops on an empty page, an
        # all-duplicate page (an out-of-range `-pagina-N` redirects to page 1),
        # an all-rejected page, or a short page — no synthetic page ceiling
        # needed on top, and the old 20-page one was truncating busy zonas.
        max_pages = -(-cap // self._ZP_PAGE_SIZE) if cap > 0 else 0

        funnel = ZonaPropFunnel(search_url=base_url)
        results: list[RawProperty] = []
        seen: set[str] = set()
        page = 1
        page_size: int | None = None   # measured off page 1, never assumed
        attempt = 1                    # of the page currently being fetched
        retried: set[int] = set()      # pages already given a second chance
        # `try/finally`: `_run_actor` raises on a FAILED/ABORTED run, a non-2xx
        # from the Apify API or a bad token, and `run_portal_scraper` turns
        # that into a bare "0 props" in the UI — indistinguishable from a
        # search that legitimately found nothing. The funnel is exactly what
        # tells those apart, so it is logged whatever happens.
        try:
            while (max_pages <= 0 or page <= max_pages) and (cap <= 0 or len(results) < cap):
                input_data = dict(base_input)
                if page > 1:
                    input_data['searchUrl'] = base_url.replace('.html', f'-pagina-{page}.html')
                if cap > 0:
                    input_data['maxResults'] = cap - len(results)

                raw_items = await self._run_actor('zonaprop', actor_id, input_data)
                row = ZonaPropPage(page=page, raw=len(raw_items), attempt=attempt)
                funnel.pages.append(row)

                # Page 1 defines the portal's real page size; re-derive the
                # page ceiling from it so a smaller page cannot quietly shrink
                # the cap (200 items over 20-item pages is 10 pages, not 7).
                if page_size is None and raw_items:
                    page_size = len(raw_items)
                    if cap > 0:
                        max_pages = -(-cap // page_size)

                for item in raw_items:
                    key = str(item.get('listingId') or item.get('url') or '')
                    if key and key in seen:
                        row.duplicates += 1
                        continue
                    if key:
                        seen.add(key)
                    if not _item_matches_zona(item, phrases):
                        row.zona_rejected += 1
                        row.note_rejected(str(item.get('neighborhood') or ''))
                        continue
                    prop = _norm_zonaprop(item, filters.zona or '')
                    if prop is None:
                        row.no_price += 1
                        continue
                    if cap > 0 and len(results) >= cap:
                        row.capped += 1
                        continue
                    results.append(prop)
                    row.kept += 1

                # A page that came back SHORT and carried nothing new was cut
                # off mid-read — the actor died partway and re-served a slice
                # of page 1. Ask once more before believing the listing ended:
                # observed live, a stunted page 2 cost pages 2 AND 3 of a
                # three-page listing.
                #
                # Narrow on purpose, because every attempt is a PAID run. A
                # FULL sterile page is an out-of-range `-pagina-N` bouncing
                # back to page 1, and an EMPTY one says the same thing more
                # cheaply — both are the real end and are not re-asked.
                stunted = (
                    raw_items
                    and row.new_unique == 0
                    and page_size is not None
                    and len(raw_items) < page_size
                )
                if stunted and page not in retried:
                    retried.add(page)
                    attempt += 1
                    continue
                attempt = 1

                # Stop on: empty page, all-duplicates (out-of-range page redirects
                # back to page 1), all-rejected (drifted into a nationwide
                # redirect), or a short page (the listing's last one). Each break
                # is named so the log says which one truncated the search.
                if not raw_items:
                    funnel.stop_reason = 'empty_page'
                    break
                if row.new_unique == 0:
                    funnel.stop_reason = 'all_duplicates'
                    break
                if row.kept == 0:
                    funnel.stop_reason = 'all_rejected'
                    break
                # Short RELATIVE to what this portal actually serves. Page 1
                # can never be short — it IS the measurement — so a one-page
                # listing costs one extra request to confirm the end, rather
                # than a guess that silently drops four pages.
                if page > 1 and page_size is not None and len(raw_items) < page_size:
                    funnel.stop_reason = 'short_page'
                    break
                # A healthy actor run may span multiple pages; skip what it covered.
                page += max(1, -(-len(raw_items) // (page_size or self._ZP_PAGE_SIZE)))
            else:
                # Fell out of the `while` condition rather than a `break`: only a
                # cap (or the page ceiling it implies) can do that.
                funnel.stop_reason = 'cap_reached' if cap > 0 else 'max_pages'
        except Exception as exc:
            funnel.stop_reason = f'actor_error: {type(exc).__name__}: {exc}'
            # Every page is a PAID actor run. If earlier pages already came
            # back, KEEP them: discarding thirty listings because page 31
            # timed out is the worst possible trade — the caller
            # (`run_portal_scraper`) turns any exception into
            # `collected_properties: []`, so re-raising here throws away work
            # the user has already been billed for. Observed live: page 1
            # returned 30 City Bell listings, page 2 hit a ReadTimeout, and
            # all 30 were lost.
            #
            # With nothing salvaged there is nothing to protect, and the
            # exception must reach the graph so `state['errors']` records it
            # rather than reporting a clean "found nothing".
            if not results:
                logger.warning('%s', funnel.summary())
                raise
            logger.warning('%s', funnel.summary())
            return results, funnel
        logger.info('%s', funnel.summary())
        return results, funnel

    async def _scrape_argenprop(
        self, filters: ScrapingFilters, on_progress: ProgressCb,
    ) -> list[RawProperty]:
        """One actor run carrying every paginated URL as `startUrls` — unlike
        ZonaProp's per-page runs, this avoids paying for a fresh browser
        cold-start (and a fresh WAF challenge) on every page."""
        from app.core.config import settings
        await on_progress('argenprop', 'running', 0)

        # Resolve the zona to Argenprop's canonical slug via its autocomplete
        # API — the fan-out unit (localidad on the map path, zona on the chat
        # path) is what the user actually asked for.
        zona = filters.localidades[0] if filters.localidades else (filters.zona or '')
        zona_slug = await _argenprop_resolve_zona_slug(zona)
        urls = _argenprop_search_urls(filters, settings.ARGENPROP_MAX_PAGES, zona_slug=zona_slug)
        input_data = {
            'startUrls': [{'url': u} for u in urls],
            'maxCrawlPages': len(urls),
            'crawlerType': 'playwright:chrome',
            'saveHtml': True,
            # Without this the actor hands back Readability (reader-mode) DOM,
            # which strips the whole `ul.card__photos` carousel — every result
            # then reaches the UI photoless. The raw server HTML already has
            # the full gallery rendered, so no per-ficha fetch is needed.
            'htmlTransformer': 'none',
        }
        raw_pages = await self._run_actor('argenprop', _ACTORS['argenprop'], input_data)

        results: list[RawProperty] = []
        seen: set[str] = set()
        for page in raw_pages:
            html = page.get('html')
            if not html:
                continue
            for prop in _parse_argenprop_page(html, filters):
                key = str(prop.url_origen or '')
                if key in seen:
                    continue
                seen.add(key)
                results.append(prop)

        await on_progress('argenprop', 'done', len(results))
        return results

    async def scrape_agencies(self, zona: str, on_progress: ProgressCb) -> list[Agency]:
        await on_progress('googlemaps', 'running', 0)
        from app.core.config import settings
        input_data: dict[str, Any] = {
            'searchStringsArray': [f'inmobiliarias en {zona}'],
            'language': 'es',
            'countryCode': 'ar',
            'includeWebResults': False,
        }
        # `0` = uncapped, and uncapped means OMITTING the key: the actor reads a
        # literal `0` as "crawl zero places", which would silence discovery
        # instead of freeing it.
        if settings.GOOGLEMAPS_MAX_PLACES > 0:
            input_data['maxCrawledPlacesPerSearch'] = settings.GOOGLEMAPS_MAX_PLACES
        raw_items = await self._run_actor('googlemaps', _ACTORS['googlemaps'], input_data)
        agencies = [a for item in raw_items
                    if (a := _norm_googlemaps_agency(item, zona)) is not None]
        await on_progress('googlemaps', 'done', len(agencies))
        return agencies

    async def scrape_website(self, url: str, on_progress: ProgressCb) -> list[dict[str, str]]:
        return await _scrape_website_direct(url, on_progress)

    async def scrape_instagram_profile(self, handle: str, on_progress: ProgressCb) -> list[RawProperty]:
        await on_progress(f'instagram:{handle}', 'running', 0)
        from app.core.config import settings
        input_data: dict[str, Any] = {
            'username': [handle],
            'onlyPostsNewerThan': '3 months',
            'dataDetailLevel': 'basicData',
        }
        # `0` = uncapped; omitted for the same reason as the Google Maps cap.
        # `onlyPostsNewerThan` stays as the real bound on this actor's spend.
        if settings.INSTAGRAM_RESULTS_LIMIT > 0:
            input_data['resultsLimit'] = settings.INSTAGRAM_RESULTS_LIMIT
        raw_items = await self._run_actor('instagram', _ACTORS['instagram'], input_data)
        results = [p for item in raw_items if (p := _norm_instagram(item)) is not None]
        await on_progress(f'instagram:{handle}', 'done', len(results))
        return results


# ── Mock implementation ────────────────────────────────────────────────────────

_BARRIOS = ['Palermo', 'Belgrano', 'Recoleta', 'Caballito', 'Villa Crespo',
            'Núñez', 'Colegiales', 'Almagro', 'San Telmo', 'Puerto Madero']
_CALLES = ['Av. Santa Fe', 'Thames', 'Gorriti', 'Av. Cabildo', 'Honduras',
           'Av. Córdoba', 'Juramento', 'Malabia', 'Av. del Libertador', 'Gurruchaga']
_AMENITIES = ['pileta', 'gimnasio', 'sum', 'cochera', 'parrilla', 'seguridad 24hs',
              'laundry', 'balcón', 'terraza']


_MOCK_AGENCIES = [
    ('RE/MAX Palermo', 'remax_palermo', 'remax.com.ar/palermo', 4.8),
    ('Toribio Achával', 'toribioachaval', 'toribioachaval.com', 4.7),
    ('Bullrich Propiedades', 'bullrichprop', 'bullrich.com.ar', 4.6),
    ('L.J. Ramos', 'ljramos', 'ljramos.com.ar', 4.5),
    ('Soldati Propiedades', 'soldatiprop', 'soldati.com.ar', 4.4),
    ('Reporte Inmobiliario', None, 'reporteinmobiliario.com', 4.2),
    ('Inmobiliaria Local', None, None, 3.8),
]


class MockApifyService(BaseApifyService):
    DELAYS = {'zonaprop': 1.2, 'mercadolibre': 0.9, 'argenprop': 1.4, 'remax': 0.9, 'instagram': 1.0}
    COUNTS = {'zonaprop': (3, 5), 'mercadolibre': (3, 5), 'argenprop': (3, 5), 'remax': (3, 5), 'instagram': (2, 3)}

    async def scrape_source(self, source: str, filters: ScrapingFilters, on_progress: ProgressCb) -> list[RawProperty]:
        delay = self.DELAYS.get(source, 1.0)
        await on_progress(source, 'running', 0)
        await asyncio.sleep(delay * 0.4)
        total = random.randint(*self.COUNTS.get(source, (5, 8)))
        await on_progress(source, 'running', total // 2)
        await asyncio.sleep(delay * 0.6)
        props = [self._fake_property(source, filters) for _ in range(total)]
        await on_progress(source, 'done', total)
        return props

    async def scrape_agencies(self, zona: str, on_progress: ProgressCb) -> list[Agency]:
        import uuid
        await on_progress('googlemaps', 'running', 0)
        await asyncio.sleep(1.2)
        agencies = [
            Agency(
                id=str(uuid.uuid4()),
                nombre=name,
                direccion=f'Av. Santa Fe {random.randint(100, 4000)}, {zona}, CABA',
                telefono=f'+54 11 {random.randint(4000, 5999)}-{random.randint(1000, 9999)}',
                sitio_web=website,
                instagram_handle=ig,
                calificacion=rating,
                zona=zona,
            )
            for name, ig, website, rating in _MOCK_AGENCIES
        ]
        await on_progress('googlemaps', 'done', len(agencies))
        return agencies

    async def scrape_website(self, url: str, on_progress: ProgressCb) -> list[dict[str, str]]:
        label = url.replace('https://', '').replace('http://', '').split('/')[0]
        await on_progress(f'web:{label}', 'running', 0)
        await asyncio.sleep(1.0)
        zona = random.choice(_BARRIOS)
        pages = [
            {
                'url': f'{url.rstrip("/")}/propiedades/pagina-{i + 1}',
                'text': '\n'.join(
                    f'Departamento {random.randint(1, 4)} ambientes en {zona} - '
                    f'USD {random.randint(80, 350) * 1000} - '
                    f'{random.randint(35, 150)}m² - {random.choice(_CALLES)} {random.randint(100, 4000)}'
                    for _ in range(random.randint(2, 5))
                ),
            }
            for i in range(random.randint(3, 6))
        ]
        await on_progress(f'web:{label}', 'done', len(pages))
        return pages

    async def scrape_instagram_profile(self, handle: str, on_progress: ProgressCb) -> list[RawProperty]:
        await on_progress(f'instagram:{handle}', 'running', 0)
        await asyncio.sleep(1.0)
        total = random.randint(4, 8)
        filters = ScrapingFilters(zona=random.choice(_BARRIOS))
        posts = [self._fake_property('instagram', filters) for _ in range(total)]
        await on_progress(f'instagram:{handle}', 'done', total)
        return posts

    def _fake_property(self, source: str, f: ScrapingFilters) -> RawProperty:
        zona = f.zona or random.choice(_BARRIOS)
        calle = random.choice(_CALLES)
        op = f.tipo_operacion or random.choice(['venta', 'alquiler'])
        tipo = (f.tipos_propiedad[0] if f.tipos_propiedad else None) or random.choice(['departamento', 'casa', 'ph'])
        amb = random.randint(1, 5)
        m2 = round(random.uniform(28, 180), 1)
        precio = round(random.uniform(70_000, 480_000) if op == 'venta' else random.uniform(350, 2200), 0)
        tipo_str = tipo or 'propiedad'
        caption = (
            f'{tipo_str.capitalize()} en {op} · {amb} ambientes · {m2}m² · '
            f'{"USD" if op == "venta" else "$"} {precio:,.0f} · {calle} {random.randint(100,4000)}, {zona} 🏠'
        )
        return RawProperty(
            fuente=source,  # type: ignore[arg-type]
            titulo=caption if source == 'instagram' else f'{tipo_str.capitalize()} {amb} amb en {zona}',
            direccion=f'{calle} {random.randint(100, 4500)}, {zona}, CABA',
            precio=precio,
            moneda='USD',
            tipo_operacion=op,  # type: ignore[arg-type]
            tipo_propiedad=tipo,  # type: ignore[arg-type]
            ambientes=amb,
            m2_total=m2,
            m2_cubiertos=round(m2 * random.uniform(0.7, 1.0), 1),
            antiguedad=random.randint(0, 40),
            amenities=random.sample(_AMENITIES, k=random.randint(0, 4)),
            imagenes=[],
            url_origen=f'https://{source}.com.ar/propiedad/{random.randint(10**6, 10**7)}',
        )


# ── Factory ────────────────────────────────────────────────────────────────────

def get_apify_service() -> BaseApifyService:
    from app.core.config import settings
    if settings.APIFY_USE_MOCK:
        return MockApifyService()
    if not settings.APIFY_API_TOKEN:
        raise RuntimeError('APIFY_API_TOKEN is not set. Add it to backend/.env or set APIFY_USE_MOCK=true.')
    return ApifyService(api_token=settings.APIFY_API_TOKEN)
