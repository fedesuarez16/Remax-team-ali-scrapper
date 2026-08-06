from __future__ import annotations

import asyncio
import contextvars
import re
import random
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Awaitable, Callable, Iterable, Iterator, Mapping

import httpx

from app.models.property import Agency, RawProperty, ScrapingFilters

ProgressCb = Callable[[str, str, int], Awaitable[None]]

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

# ── MercadoLibre public REST API (no Apify) ───────────────────────────────────
_ML_API_BASE = 'https://api.mercadolibre.com'
_ML_CATEGORY = 'MLA1459'   # Inmuebles Argentina
_ML_MAX_PAGES = 5           # 5 × 50 = 250 results max

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
    """Phrase-set for the ZonaProp redirect guard. Map path (localidades
    present): barrios ∪ localidades ∪ zona — wide on purpose, the polygon is
    the precision gate downstream. Chat path (no localidades): ONLY this
    branch's zona, preserving the pre-change per-branch scoping for
    multi-zona chat queries."""
    if filters.localidades:
        phrases = set(filters.zonas) | set(filters.localidades) | {filters.zona or ''}
    else:
        phrases = {filters.zona or ''}
    phrases.discard('')
    return phrases


def _item_matches_zona(item: dict[str, Any], zonas: Iterable[str]) -> bool:
    """Guard against ZonaProp redirecting an unknown zona slug to a nationwide
    listing: keep items that mention ANY phrase in `zonas` (as a phrase) in
    their neighborhood, address, title or description.

    `zonas` is a phrase SET (ADR-1: union of barrios ∪ localidad for a
    localidad-branch, or a single-item set `{zona}` on the chat path — which
    preserves today's single-phrase behavior exactly). An empty set keeps
    everything, same as the old empty-string sentinel.

    A composite phrase ("Villa Elisa, La Plata") requires EVERY comma part in
    the haystack — the bare localidad also exists in other provinces, and the
    item's `city` field (ZonaProp = partido) is what tells them apart.
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
    return any(all(part in haystack for part in parts) for parts in phrase_parts)


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
    `Allow`s pagina-1 through pagina-10. `zona_slug` is the portal-resolved
    slug from `_argenprop_resolve_zona_slug`; without one, falls back to
    naive `_slugify` (the zona guard downstream rejects redirect garbage)."""
    zona = filters.zona or 'Buenos Aires'
    op_slug = 'alquiler' if filters.tipo_operacion == 'alquiler' else 'venta'
    tipos = filters.tipos_propiedad or []
    tipo_slug = _ARGENPROP_URL_SLUG.get(tipos[0], 'inmuebles') if len(tipos) == 1 else 'inmuebles'
    if zona_slug is None:
        zona_slug = _slugify(filters.localidades[0]) if filters.localidades else _slugify(zona)
    base_url = f'{_ARGENPROP_BASE}/{tipo_slug}/{op_slug}/{zona_slug}'

    capped = max(1, min(max_pages, _ARGENPROP_MAX_PAGES_HARD))
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
) -> list[str]:
    """Listing URLs for one search, page 1..N.

    Two shapes exist: `{tipo}-{operacion}-{zona}.html` when the search pins a
    single property type AND an operation, else the broader
    `propiedades-{zona}.html`. Later pages append `-pagina-N` before `.html`.
    """
    tipos = filters.tipos_propiedad or []
    tipo_slug = _INMOBUSQUEDA_URL_TIPO.get(tipos[0], '') if len(tipos) == 1 else ''
    op_slug = _INMOBUSQUEDA_URL_OP.get(filters.tipo_operacion or '', '')
    stem = f'{tipo_slug}-{op_slug}-{zona_slug}' if tipo_slug and op_slug else f'propiedades-{zona_slug}'

    pages = max(1, max_pages)
    return [f'{_INMOBUSQUEDA_BASE}/{stem}.html'] + [
        f'{_INMOBUSQUEDA_BASE}/{stem}-pagina-{n}.html' for n in range(2, pages + 1)
    ]


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


def _mudafy_search_urls(filters: ScrapingFilters, max_pages: int) -> list[str]:
    """Listing URLs, page 1..N. Later pages take the `/{N}-p` suffix — read off
    the site's own pagination hrefs, and query-string free so the crawl stays
    inside what robots.txt allows."""
    zona = filters.localidades[0] if filters.localidades else (filters.zona or '')
    region = _mudafy_region_for(zona)
    op = _MUDAFY_URL_OP.get(filters.tipo_operacion or 'venta', 'venta')
    tipos = filters.tipos_propiedad or []
    tipo = _MUDAFY_URL_TIPO.get(tipos[0], 'propiedades') if len(tipos) == 1 else 'propiedades'
    base = f'{_MUDAFY_BASE}/{op}/{tipo}/{region}'
    return [base] + [f'{base}/{n}-p' for n in range(2, max(1, max_pages) + 1)]


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

    imagenes = [
        str(pic.get('url')) for pic in (pub.get('pictures') or [])
        if isinstance(pic, dict) and pic.get('url')
    ]

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
        imagenes=imagenes[:_MAX_GALLERY],
        url_origen=url,
        raw={'id': pub.get('id'), 'coordinates': (addr.get('coordinates') or {})},
    )


def _parse_mudafy_payload(page: str, filters: ScrapingFilters) -> list[RawProperty]:
    """Every `"publication":{…}` in the flight payload → RawProperty, zona-guarded.

    Mudafy only filters by broad region, so the searched zona is enforced here
    over whatever the region page returned.
    """
    import json

    # The payload lives JSON-escaped inside a JS string literal.
    text = page.replace('\\"', '"').replace('\\\\', '\\')
    decoder = json.JSONDecoder()
    phrase_parts = [
        parts for parts in
        ([_slugify(p) for p in z.split(',') if _slugify(p)] for z in _guard_phrases(filters))
        if parts
    ]

    results: list[RawProperty] = []
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
        if pub_id is not None and pub_id in seen:
            continue
        if pub_id is not None:
            seen.add(pub_id)

        prop = _norm_mudafy(pub)
        if prop is None:
            continue
        if phrase_parts:
            haystack = _slugify(f'{prop.direccion} {prop.titulo or ""}')
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

    results: list[RawProperty] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(
        timeout=30, follow_redirects=True,
        headers={'User-Agent': 'Mozilla/5.0 (compatible; PropSearchBot/1.0)'},
    ) as client:
        for url in _mudafy_search_urls(filters, settings.MUDAFY_MAX_PAGES):
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except Exception:
                break

            page_props = _parse_mudafy_payload(resp.text, filters)
            new = 0
            for prop in page_props:
                key = str(prop.url_origen or '')
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                results.append(prop)
                new += 1

            # A region page that yielded nothing new is either exhausted or
            # entirely outside the zona — either way, stop paging.
            if new == 0:
                break
            await on_progress('mudafy', 'running', len(results))

    await on_progress('mudafy', 'done', len(results))
    return results


def _norm_mercadolibre_api(item: dict[str, Any], zona: str) -> RawProperty | None:
    precio = item.get('price')
    if not precio:
        return None

    addr = item.get('address') or {}
    parts = [p for p in [addr.get('street_name'), addr.get('city_name')] if p]
    direccion = ', '.join(parts) if parts else zona

    attrs: dict[str, str] = {
        a['id']: (a.get('value_name') or '')
        for a in (item.get('attributes') or [])
    }

    def parse_area(s: str) -> float | None:
        m = re.search(r'[\d.]+', s.replace(',', '.'))
        return float(m.group()) if m else None

    def parse_int(s: str) -> int | None:
        return int(s) if isinstance(s, str) and s.isdigit() else (int(s) if isinstance(s, int) else None)

    rooms_str = attrs.get('ROOMS', '')
    ambientes = int(rooms_str) if rooms_str.isdigit() else None

    op_val = attrs.get('OPERATION_TYPE', '').lower()
    tipo_operacion = 'alquiler' if 'alquiler' in op_val else 'venta'

    prop_type_raw = attrs.get('PROPERTY_TYPE', '').lower()
    tipo_propiedad = _ML_PROP_TYPE.get(prop_type_raw)
    if not tipo_propiedad:
        title = item.get('title', '').lower()
        if 'casa' in title:
            tipo_propiedad = 'casa'
        elif ' ph ' in title or title.startswith('ph ') or title.endswith(' ph'):
            tipo_propiedad = 'ph'
        elif 'local' in title:
            tipo_propiedad = 'local'
        elif 'oficina' in title:
            tipo_propiedad = 'oficina'
        elif 'terreno' in title or 'lote' in title:
            tipo_propiedad = 'terreno'
        else:
            tipo_propiedad = 'departamento'

    thumbnail = item.get('thumbnail', '')
    imagenes = [thumbnail.replace('-I.jpg', '-O.jpg')] if thumbnail else []

    return RawProperty(
        fuente='mercadolibre',
        titulo=item.get('title', ''),
        direccion=direccion,
        precio=float(precio),
        moneda=item.get('currency_id', 'USD'),
        tipo_operacion=tipo_operacion,
        tipo_propiedad=tipo_propiedad,
        ambientes=ambientes,
        banos=parse_int(attrs.get('FULL_BATHROOMS', '')),
        cocheras=parse_int(attrs.get('PARKING_LOTS', '')),
        m2_total=parse_area(attrs.get('TOTAL_AREA', '')),
        m2_cubiertos=parse_area(attrs.get('COVERED_AREA', '')),
        antiguedad=None,
        amenities=[],
        imagenes=imagenes,
        url_origen=item.get('permalink', ''),
    )


async def _scrape_mercadolibre_api(
    filters: ScrapingFilters,
    on_progress: ProgressCb,
) -> list[RawProperty]:
    zona = filters.zona or 'Buenos Aires'
    op = filters.tipo_operacion or 'venta'
    tipos = filters.tipos_propiedad or []

    q_parts = [zona, op]
    if tipos:
        q_parts.append(tipos[0])
    q = ' '.join(q_parts)

    await on_progress('mercadolibre', 'running', 0)

    results: list[RawProperty] = []
    async with httpx.AsyncClient(timeout=20) as client:
        for page in range(_ML_MAX_PAGES):
            offset = page * 50
            try:
                resp = await client.get(
                    f'{_ML_API_BASE}/sites/MLA/search',
                    params={'category': _ML_CATEGORY, 'q': q, 'limit': 50, 'offset': offset},
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception:
                break

            items = data.get('results', [])
            if not items:
                break

            for item in items:
                prop = _norm_mercadolibre_api(item, zona)
                if prop is not None:
                    results.append(prop)

            paging = data.get('paging', {})
            if offset + 50 >= paging.get('total', 0):
                break

            if page < _ML_MAX_PAGES - 1:
                await on_progress('mercadolibre', 'running', len(results))

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
    exact ones, so the winner is the first result whose slugified label
    (tags stripped) contains EVERY comma-part of the query. Returns None on
    no confident match or API failure — callers fall back to nationwide
    paging plus the text zona guard."""
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
        for entry in geo_results:
            label = re.sub(r'</?b>', '', str(entry.get('label') or ''))
            label_slug = _slugify(label)
            if not all(part in label_slug for part in wanted):
                continue
            id_field = _REMAX_LEVEL_ID_FIELD.get(entry.get('level') or 0)
            loc_id = entry.get(id_field) if id_field else None
            if loc_id:
                slots = [''] * _REMAX_LOCATION_SLOTS
                slots[entry['level']] = str(loc_id)
                location = 'in:' + ':'.join(slots)
                break
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

    max_pages = settings.REMAX_MAX_PAGES   # 0 = uncapped
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


def _extract_images_from_html(html: str, base: str) -> list[str]:
    """Property photos visible in server HTML: og:image/twitter:image metas plus
    content <img> tags (honoring lazy-load attrs and srcset), junk filtered."""
    from bs4 import BeautifulSoup  # type: ignore[import-untyped]

    soup = BeautifulSoup(html, 'html.parser')
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

    for meta in soup.find_all('meta'):
        key = str(meta.get('property') or meta.get('name') or '').lower()
        if key in ('og:image', 'og:image:url', 'og:image:secure_url', 'twitter:image', 'twitter:image:src'):
            _add_img(str(meta.get('content') or ''))

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
                return u, _extract_images_from_html(resp.text, u)
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
        self._client = httpx.AsyncClient(timeout=30)

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
            zona_slug = _slugify(filters.localidades[0]) if filters.localidades else _slugify(zona)
            op_slug = 'alquiler' if op == 'alquiler' else 'venta'
            tipos = filters.tipos_propiedad
            prop_slug = self._ZP_URL_SLUG.get(tipos[0], 'inmuebles') if len(tipos) == 1 else 'inmuebles'
            search_url = f'https://www.zonaprop.com.ar/{prop_slug}-{op_slug}-{zona_slug}.html'
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
            return {
                'searchStringsArray': [f'inmobiliarias en {zona}'],
                'maxCrawledPlacesPerSearch': 20,
                'language': 'es',
                'countryCode': 'ar',
            }

        if source == 'instagram':
            # Uses handles stored in filters or falls back to empty
            handles = getattr(filters, 'instagram_handles', None) or []
            from app.core.config import settings
            return {
                'usernames': handles,
                'resultsLimit': settings.INSTAGRAM_RESULTS_LIMIT,
            }

        return {}

    async def _run_actor(
        self,
        source: str,
        actor_id: str,
        input_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        run_url = f'{_APIFY_BASE}/acts/{actor_id}/runs'
        params = {'token': self._token}

        # Start run
        resp = await self._client.post(run_url, params=params, json=input_data)
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
            status_resp = await self._client.get(status_url, params=params)
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
        items_resp = await self._client.get(dataset_url, params={**params, 'format': 'json'})
        items_resp.raise_for_status()
        return items_resp.json()  # type: ignore[no-any-return]

    async def scrape_source(
        self,
        source: str,
        filters: ScrapingFilters,
        on_progress: ProgressCb,
    ) -> list[RawProperty]:
        if source == 'mercadolibre':
            return await _scrape_mercadolibre_api(filters, on_progress)
        if source == 'remax':
            return await _scrape_remax_api(filters, on_progress)
        if source == 'argenprop':
            return await self._scrape_argenprop(filters, on_progress)
        if source == 'inmobusqueda':
            return await _scrape_inmobusqueda(filters, on_progress)
        if source == 'mudafy':
            return await _scrape_mudafy(filters, on_progress)

        actor_id = _ACTORS.get(source)
        if not actor_id:
            return []

        await on_progress(source, 'running', 0)

        if source == 'zonaprop':
            results = await self._scrape_zonaprop_paginated(actor_id, filters)

            # Composite localidad slug ("villa-elisa-la-plata") unknown to
            # ZonaProp → nationwide redirect → the guard rejects everything
            # (or the page 404s and the actor returns nothing). Retry ONCE
            # with the plain localidad slug rather than returning 0 results.
            if not results and filters.localidades and ',' in filters.localidades[0]:
                plain = filters.localidades[0].split(',')[0].strip()
                plain_filters = filters.model_copy(update={'localidades': [plain], 'zona': plain})
                results = await self._scrape_zonaprop_paginated(actor_id, plain_filters)
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

    # ZonaProp listing pages hold ~30 items; the crawlerbros actor's browser
    # dies after page 1 (every run returns one page regardless of maxResults),
    # so WE paginate: one actor run per `...-pagina-N.html` URL.
    _ZP_PAGE_SIZE = 30
    # Hard page ceiling when ZONAPROP_MAX_RESULTS is 0 (uncapped).
    _ZP_MAX_PAGES_UNCAPPED = 20

    async def _scrape_zonaprop_paginated(
        self, actor_id: str, filters: ScrapingFilters,
    ) -> list[RawProperty]:
        from app.core.config import settings
        cap = settings.ZONAPROP_MAX_RESULTS
        base_input = self._input_for('zonaprop', filters)
        base_url: str = base_input['searchUrl']
        phrases = _guard_phrases(filters)
        max_pages = (
            -(-cap // self._ZP_PAGE_SIZE) if cap > 0 else self._ZP_MAX_PAGES_UNCAPPED
        )

        results: list[RawProperty] = []
        seen: set[str] = set()
        page = 1
        while page <= max_pages and (cap <= 0 or len(results) < cap):
            input_data = dict(base_input)
            if page > 1:
                input_data['searchUrl'] = base_url.replace('.html', f'-pagina-{page}.html')
            if cap > 0:
                input_data['maxResults'] = cap - len(results)

            raw_items = await self._run_actor('zonaprop', actor_id, input_data)

            new_unique = 0
            page_kept = 0
            for item in raw_items:
                key = str(item.get('listingId') or item.get('url') or '')
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                new_unique += 1
                if _item_matches_zona(item, phrases):
                    prop = _norm_zonaprop(item, filters.zona or '')
                    if prop is not None and (cap <= 0 or len(results) < cap):
                        results.append(prop)
                        page_kept += 1

            # Stop on: empty page, all-duplicates (out-of-range page redirects
            # back to page 1), all-rejected (drifted into a nationwide
            # redirect), or a short page (the listing's last one).
            if not raw_items or new_unique == 0 or page_kept == 0:
                break
            if len(raw_items) < self._ZP_PAGE_SIZE:
                break
            # A healthy actor run may span multiple pages; skip what it covered.
            page += max(1, -(-len(raw_items) // self._ZP_PAGE_SIZE))
        return results

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
        input_data = {
            'searchStringsArray': [f'inmobiliarias en {zona}'],
            'maxCrawledPlacesPerSearch': settings.GOOGLEMAPS_MAX_PLACES,
            'language': 'es',
            'countryCode': 'ar',
            'includeWebResults': False,
        }
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
        input_data = {
            'username': [handle],
            'resultsLimit': settings.INSTAGRAM_RESULTS_LIMIT,
            'onlyPostsNewerThan': '3 months',
            'dataDetailLevel': 'basicData',
        }
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
