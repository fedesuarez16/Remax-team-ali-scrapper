from __future__ import annotations

import asyncio
import re
import random
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Awaitable, Callable, Iterable

import httpx

from app.models.property import Agency, RawProperty, ScrapingFilters

ProgressCb = Callable[[str, str, int], Awaitable[None]]

PORTAL_SOURCES = ('zonaprop', 'mercadolibre', 'argenprop', 'remax')   # phase-1 portal scrapers
SOURCES = ('zonaprop', 'mercadolibre', 'argenprop', 'remax', 'googlemaps', 'instagram')

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
_REMAX_MAX_PAGES = 5   # 5 × 20 = 100 results max
_REMAX_PAGE_SIZE = 20

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


def _argenprop_search_urls(filters: ScrapingFilters, max_pages: int) -> list[str]:
    """Argenprop listing-page URLs for one search, page 1..N. Page 1 has no
    query string; later pages append the literal `?pagina-N` token (no `=`,
    confirmed from real `href`s) — capped at 10 because robots.txt only
    `Allow`s pagina-1 through pagina-10."""
    zona = filters.zona or 'Buenos Aires'
    op_slug = 'alquiler' if filters.tipo_operacion == 'alquiler' else 'venta'
    tipos = filters.tipos_propiedad or []
    tipo_slug = _ARGENPROP_URL_SLUG.get(tipos[0], 'inmuebles') if len(tipos) == 1 else 'inmuebles'
    zona_slug = _slugify(filters.localidades[0]) if filters.localidades else _slugify(zona)
    base_url = f'{_ARGENPROP_BASE}/{tipo_slug}/{op_slug}/{zona_slug}'

    capped = max(1, min(max_pages, _ARGENPROP_MAX_PAGES_HARD))
    return [base_url] + [f'{base_url}?pagina-{page}' for page in range(2, capped + 1)]


def _parse_argenprop_page(html: str, filters: ScrapingFilters) -> list[RawProperty]:
    """Argenprop's search-results HTML, parsed deterministically (no LLM).

    `saveHtml: true` on the website-content-crawler actor does NOT return raw
    DOM — it's run through Readability (reader-mode extraction) first, which
    strips every `class` attribute site-wide. Verified against a real captured
    run: what survives per card is the bare (non-`data-`) attributes Argenprop
    puts on its `<a idaviso=... montonormalizado=... dormitorios=...>` card
    link, plus a few `data-*` attributes and stable sibling ordering of the
    `<p>`/`<ul>`/`<h2>` content underneath it — so cards are matched via
    `a[idaviso]` and fields via those attributes / sibling position, not CSS
    classes. The photo carousel is stripped entirely by Readability (only the
    agency logo `<img>` survives), so galleries are NOT extracted here — the
    ficha-level harvester (`harvest_page_images`) fills them in on demand.
    """
    from bs4 import BeautifulSoup  # type: ignore[import-untyped]

    if not html:
        return []
    soup = BeautifulSoup(html, 'html.parser')
    tipo_operacion = filters.tipo_operacion or 'venta'
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
        features_ul = card.find('ul')
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
            imagenes=[],
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
        imagenes=[],
        url_origen=f'https://www.remax.com.ar/listing/{item.get("slug", "")}-{item.get("id", "")}',
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


async def _scrape_remax_api(
    filters: ScrapingFilters,
    on_progress: ProgressCb,
) -> list[RawProperty]:
    zona = filters.zona or 'Buenos Aires'
    op_id = 2 if filters.tipo_operacion == 'alquiler' else 1
    tipos = filters.tipos_propiedad or []

    in_params = [f'operationId:{op_id}']
    if len(tipos) == 1 and tipos[0] in _REMAX_TYPE_IDS:
        ids_csv = ','.join(str(i) for i in _REMAX_TYPE_IDS[tipos[0]])
        in_params.append(f'typeId:{ids_csv}')

    await on_progress('remax', 'running', 0)

    results: list[RawProperty] = []
    async with httpx.AsyncClient(timeout=20) as client:
        for page in range(_REMAX_MAX_PAGES):
            try:
                resp = await client.get(
                    f'{_REMAX_API_BASE}/listings/findAllWithEntrepreneurships',
                    params={
                        'page': page, 'pageSize': _REMAX_PAGE_SIZE,
                        'sort': '-createdAt', 'in': in_params,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception:
                break

            items = data.get('data', [])
            if not items:
                break

            for item in items:
                if not _remax_matches_zona(item, zona):
                    continue
                prop = _norm_remax(item, zona)
                if prop is not None:
                    results.append(prop)

            if page + 1 >= data.get('totalPages', 0):
                break
            if page < _REMAX_MAX_PAGES - 1:
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

        # Poll until done
        status_url = f'{_APIFY_BASE}/actor-runs/{run_id}'
        elapsed = 0.0
        while elapsed < _TIMEOUT:
            await asyncio.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL
            status_resp = await self._client.get(status_url, params=params)
            status_resp.raise_for_status()
            status = status_resp.json()['data']['status']
            if status == 'SUCCEEDED':
                break
            if status in ('FAILED', 'ABORTED', 'TIMED-OUT'):
                raise RuntimeError(f'Apify run {run_id} ended with status {status}')

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

        urls = _argenprop_search_urls(filters, settings.ARGENPROP_MAX_PAGES)
        input_data = {
            'startUrls': [{'url': u} for u in urls],
            'maxCrawlPages': len(urls),
            'crawlerType': 'playwright:chrome',
            'saveHtml': True,
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
