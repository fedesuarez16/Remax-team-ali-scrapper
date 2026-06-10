from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable

import httpx

from app.models.property import Agency, RawProperty, ScrapingFilters

ProgressCb = Callable[[str, str, int], Awaitable[None]]

PORTAL_SOURCES = ('zonaprop', 'mercadolibre')   # phase-1 portal scrapers
SOURCES = ('zonaprop', 'mercadolibre', 'googlemaps', 'instagram')

# ── Actor IDs ─────────────────────────────────────────────────────────────────
_ACTORS: dict[str, str] = {
    'zonaprop':     'crawlerbros~zonaprop-scraper',
    'mercadolibre': 'easyapi~mercadolibre-search-results-scraper',
    'googlemaps':   'compass~crawler-google-places',
    'instagram':    'apify~instagram-post-scraper',
}

_APIFY_BASE = 'https://api.apify.com/v2'
_POLL_INTERVAL = 3.0   # seconds between status checks
_TIMEOUT = 300         # max seconds to wait for a run

# ── Normalisation helpers ──────────────────────────────────────────────────────

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
    return RawProperty(
        fuente='zonaprop',
        titulo=item.get('title', ''),
        direccion=item.get('address') or item.get('neighborhood') or zona,
        precio=float(precio),
        moneda=item.get('currency', 'USD'),
        tipo_operacion='alquiler' if item.get('operationType') == 'rent' else 'venta',
        tipo_propiedad=prop_type,  # type: ignore[arg-type]
        ambientes=item.get('rooms'),
        m2_total=item.get('totalArea'),
        m2_cubiertos=item.get('coveredArea'),
        antiguedad=item.get('yearBuilt'),
        amenities=[],
        imagenes=[img for img in (item.get('images') or []) if isinstance(img, str)][:5],
        url_origen=item.get('url', ''),
    )


def _norm_mercadolibre(item: dict[str, Any], zona: str) -> RawProperty | None:
    titulo = item.get('title', '')
    precio_raw = item.get('price') or item.get('currentPrice')
    if not precio_raw:
        return None
    return RawProperty(
        fuente='mercadolibre',
        titulo=titulo,
        direccion=item.get('location') or item.get('address') or zona,
        precio=float(str(precio_raw).replace('.', '').replace(',', '.')),
        moneda=item.get('currency', 'USD'),
        tipo_operacion='alquiler' if 'alquiler' in titulo.lower() else 'venta',
        tipo_propiedad='departamento',
        ambientes=None,
        m2_total=None,
        m2_cubiertos=None,
        antiguedad=None,
        amenities=[],
        imagenes=[item['thumbnail']] if item.get('thumbnail') else [],
        url_origen=item.get('url') or item.get('link', ''),
    )


def _extract_instagram_handle(website: str | None) -> str | None:
    """Pull handle from instagram.com URLs or return None."""
    if not website:
        return None
    import re
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
        imagenes=images[:5],
        url_origen=item.get('url') or item.get('postUrl', ''),
    )


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

    def _input_for(self, source: str, filters: ScrapingFilters) -> dict[str, Any]:
        zona = filters.zona or 'Buenos Aires'
        op = filters.tipo_operacion or 'venta'

        if source == 'zonaprop':
            prop_slug = zona.lower().replace(' ', '-')
            op_slug = 'alquiler' if op == 'alquiler' else 'venta'
            search_url = f'https://www.zonaprop.com.ar/departamentos-{op_slug}-{prop_slug}.html'
            return {'searchUrl': search_url, 'maxResults': 50}

        if source == 'mercadolibre':
            # Build a valid MercadoLibre inmuebles search URL
            cat = 'alquileres' if op == 'alquiler' else 'departamentos'
            slug = zona.lower().replace(' ', '-')
            url = f'https://inmuebles.mercadolibre.com.ar/{cat}/{slug}/'
            return {'searchUrl': url, 'maxItems': 50}

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
            return {
                'usernames': handles,
                'resultsLimit': 30,
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
        actor_id = _ACTORS.get(source)
        if not actor_id:
            return []

        await on_progress(source, 'running', 0)

        raw_items = await self._run_actor(source, actor_id, self._input_for(source, filters))

        results: list[RawProperty] = []
        for item in raw_items:
            prop: RawProperty | None = None
            if source == 'zonaprop':
                prop = _norm_zonaprop(item, filters.zona or '')
            elif source == 'mercadolibre':
                prop = _norm_mercadolibre(item, filters.zona or '')
            elif source == 'googlemaps':
                prop = None  # googlemaps uses scrape_agencies, not scrape_source
            elif source == 'instagram':
                prop = _norm_instagram(item)
            if prop is not None:
                results.append(prop)

        await on_progress(source, 'done', len(results))
        return results

    async def scrape_agencies(self, zona: str, on_progress: ProgressCb) -> list[Agency]:
        await on_progress('googlemaps', 'running', 0)
        input_data = {
            'searchStringsArray': [f'inmobiliarias en {zona}'],
            'maxCrawledPlacesPerSearch': 20,
            'language': 'es',
            'countryCode': 'ar',
            'includeWebResults': False,
        }
        raw_items = await self._run_actor('googlemaps', _ACTORS['googlemaps'], input_data)
        agencies = [a for item in raw_items
                    if (a := _norm_googlemaps_agency(item, zona)) is not None]
        await on_progress('googlemaps', 'done', len(agencies))
        return agencies


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
    DELAYS = {'zonaprop': 1.2, 'mercadolibre': 0.9, 'instagram': 1.0}
    COUNTS = {'zonaprop': (7, 10), 'mercadolibre': (5, 8), 'instagram': (4, 8)}

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

    def _fake_property(self, source: str, f: ScrapingFilters) -> RawProperty:
        zona = f.zona or random.choice(_BARRIOS)
        calle = random.choice(_CALLES)
        op = f.tipo_operacion or random.choice(['venta', 'alquiler'])
        tipo = f.tipo_propiedad or random.choice(['departamento', 'casa', 'ph'])
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
