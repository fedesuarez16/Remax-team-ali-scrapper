from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable

import httpx

from app.models.property import RawProperty, ScrapingFilters

ProgressCb = Callable[[str, str, int], Awaitable[None]]

SOURCES = ('zonaprop', 'mercadolibre', 'googlemaps', 'instagram')

# ── Actor IDs ─────────────────────────────────────────────────────────────────
_ACTORS: dict[str, str] = {
    'zonaprop':    'crawlerbros/zonaprop-scraper',
    'mercadolibre': 'easyapi/mercadolibre-search-results-scraper',
    'googlemaps':  'compass/crawler-google-places',
    'instagram':   'apify/instagram-post-scraper',
}

_APIFY_BASE = 'https://api.apify.com/v2'
_POLL_INTERVAL = 3.0   # seconds between status checks
_TIMEOUT = 300         # max seconds to wait for a run

# ── Normalisation helpers ──────────────────────────────────────────────────────

def _norm_zonaprop(item: dict[str, Any], zona: str) -> RawProperty | None:
    precio = item.get('price')
    if precio is None:
        return None
    return RawProperty(
        fuente='zonaprop',
        titulo=item.get('title', ''),
        direccion=item.get('address') or item.get('neighborhood') or zona,
        precio=float(precio),
        moneda=item.get('currency', 'USD'),
        tipo_operacion='alquiler' if item.get('operationType') == 'rent' else 'venta',
        tipo_propiedad=item.get('propertyType') or 'departamento',
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


def _norm_googlemaps(item: dict[str, Any]) -> RawProperty | None:
    name = item.get('title') or item.get('name', '')
    if not name:
        return None
    return RawProperty(
        fuente='googlemaps',
        titulo=name,
        direccion=item.get('address', ''),
        precio=None,
        moneda='USD',
        tipo_operacion='venta',
        tipo_propiedad='otro',
        ambientes=None,
        m2_total=None,
        m2_cubiertos=None,
        antiguedad=None,
        amenities=[],
        imagenes=[item['imageUrl']] if item.get('imageUrl') else [],
        url_origen=item.get('website') or item.get('url') or '',
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


# ── Real implementation ────────────────────────────────────────────────────────

class ApifyService(BaseApifyService):
    def __init__(self, api_token: str) -> None:
        self._token = api_token
        self._client = httpx.AsyncClient(timeout=30)

    def _input_for(self, source: str, filters: ScrapingFilters) -> dict[str, Any]:
        zona = filters.zona or 'Buenos Aires'
        op = filters.tipo_operacion or 'venta'

        if source == 'zonaprop':
            return {
                'location': zona.lower().replace(' ', '-'),
                'operationType': 'rent' if op == 'alquiler' else 'sale',
                'propertyType': filters.tipo_propiedad or 'all',
                'maxResults': 50,
            }

        if source == 'mercadolibre':
            cat = 'alquileres' if op == 'alquiler' else 'departamentos'
            slug = zona.lower().replace(' ', '-')
            url = f'https://inmuebles.mercadolibre.com.ar/{cat}/{slug}/'
            return {'searchUrls': [{'url': url}], 'maxItems': 50}

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
                prop = _norm_googlemaps(item)
            elif source == 'instagram':
                prop = _norm_instagram(item)
            if prop is not None:
                results.append(prop)

        await on_progress(source, 'done', len(results))
        return results


# ── Mock implementation ────────────────────────────────────────────────────────

_BARRIOS = ['Palermo', 'Belgrano', 'Recoleta', 'Caballito', 'Villa Crespo',
            'Núñez', 'Colegiales', 'Almagro', 'San Telmo', 'Puerto Madero']
_CALLES = ['Av. Santa Fe', 'Thames', 'Gorriti', 'Av. Cabildo', 'Honduras',
           'Av. Córdoba', 'Juramento', 'Malabia', 'Av. del Libertador', 'Gurruchaga']
_AMENITIES = ['pileta', 'gimnasio', 'sum', 'cochera', 'parrilla', 'seguridad 24hs',
              'laundry', 'balcón', 'terraza']


class MockApifyService(BaseApifyService):
    DELAYS = {'zonaprop': 1.2, 'mercadolibre': 0.9, 'googlemaps': 1.5, 'instagram': 1.0}
    COUNTS = {'zonaprop': (7, 10), 'mercadolibre': (5, 8), 'googlemaps': (5, 7), 'instagram': (4, 8)}

    async def scrape_source(self, source: str, filters: ScrapingFilters, on_progress: ProgressCb) -> list[RawProperty]:
        delay = self.DELAYS.get(source, 1.0)
        await on_progress(source, 'running', 0)
        await asyncio.sleep(delay * 0.4)
        total = random.randint(*self.COUNTS.get(source, (5, 8)))
        await on_progress(source, 'running', total // 2)
        await asyncio.sleep(delay * 0.6)
        props = [self._fake(source, filters) for _ in range(total)]
        await on_progress(source, 'done', total)
        return props

    def _fake(self, source: str, f: ScrapingFilters) -> RawProperty:
        zona = f.zona or random.choice(_BARRIOS)
        calle = random.choice(_CALLES)
        altura = random.randint(100, 4500)
        op = f.tipo_operacion or random.choice(['venta', 'alquiler'])
        tipo = f.tipo_propiedad or random.choice(['departamento', 'casa', 'ph'])
        amb = random.randint(1, 5)
        m2 = round(random.uniform(28, 180), 1)
        precio = round(random.uniform(70_000, 480_000) if op == 'venta' else random.uniform(350, 2200), 0)
        return RawProperty(
            fuente=source,  # type: ignore[arg-type]
            titulo=f'{(tipo or "Propiedad").capitalize()} {amb} amb en {zona}',
            direccion=f'{calle} {altura}, {zona}, CABA',
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
