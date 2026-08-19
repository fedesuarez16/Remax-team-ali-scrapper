"""Test-first: la galería completa de una ficha de RE/MAX sale de su API.

remax.com.ar es una SPA de Angular: el HTML que llega (aun renderizado con
Playwright) trae UNA sola URL de foto — la del `og:image` — porque el resto las
pide el front por API después de hidratar. Medido en vivo sobre
`/listings/casa-a-estrenar-en-venta-villa-elisa`: 380KB de HTML, 3 URLs de
imagen en total y dos eran banderas de países.

Resultado: la ficha importada quedaba con 1 foto de las 37 que tiene el aviso.

Ningún truco de scraping arregla eso, porque el dato no está en la página. Pero
la misma API pública que ya usa `_scrape_remax_api` sirve el aviso completo por
slug (`/listings/findBySlug/{slug}`, verificado en vivo: 200 con `photos[37]`),
y `_remax_photo_urls` ya sabe convertir ese `photos[]` a URLs del CDN.

Es el mismo patrón que `ficha._mercadolibre_gallery`: cuando el portal tiene API
oficial, se le pregunta a la API en vez de pelearse con el DOM.
"""
from __future__ import annotations

import httpx
import pytest

from app.services import apify
from app.services.apify import remax_gallery_from_url

_LISTING_ID = '6485dfc6-5705-4be4-91ac-060901affeec'
_URL = 'https://www.remax.com.ar/listings/casa-a-estrenar-en-venta-villa-elisa'


def _photo(n: int) -> dict:
    return {'rawValue': f'listings/{_LISTING_ID}/photo{n}'}


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Fake de la API de RE/MAX; registra qué URLs se pidieron."""
    state: dict = {'urls': [], 'photos': 3, 'status': 200}

    class _FakeResponse:
        def __init__(self, url: str) -> None:
            self.url = url
            self.status_code = state['status']

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    str(self.status_code),
                    request=httpx.Request('GET', self.url),
                    response=httpx.Response(self.status_code),
                )

        def json(self) -> dict:
            return {'data': {'id': _LISTING_ID,
                             'photos': [_photo(i) for i in range(state['photos'])]}}

    class _FakeAsyncClient:
        def __init__(self, *a, **kw) -> None: pass
        async def __aenter__(self) -> '_FakeAsyncClient': return self
        async def __aexit__(self, *a) -> None: return None

        async def get(self, url, **kw) -> _FakeResponse:
            state['urls'].append(url)
            return _FakeResponse(url)

    monkeypatch.setattr(httpx, 'AsyncClient', _FakeAsyncClient)
    return state


async def test_the_slug_is_looked_up_on_the_public_api(api: dict) -> None:
    await remax_gallery_from_url(_URL)
    assert api['urls'] == [
        f'{apify._REMAX_API_BASE}/listings/findBySlug/casa-a-estrenar-en-venta-villa-elisa'
    ]


async def test_every_photo_of_the_listing_comes_back(api: dict) -> None:
    """El punto entero: 37 fotos, no la portada sola."""
    api['photos'] = 37
    gallery = await remax_gallery_from_url(_URL)
    assert len(gallery) == 20, 'debe respetar el tope _MAX_GALLERY del catálogo'
    assert all(_LISTING_ID in u and u.endswith('.jpg') for u in gallery)


async def test_photos_are_built_as_browsable_cdn_urls(api: dict) -> None:
    """Reusa `_remax_photo_urls`: mismo formato que las fotos del scraping."""
    api['photos'] = 1
    gallery = await remax_gallery_from_url(_URL)
    assert gallery == [
        f'{apify._REMAX_CDN}/listings/{_LISTING_ID}/{apify._REMAX_PHOTO_SIZE}/photo0.jpg'
    ]


async def test_a_trailing_slash_or_query_does_not_break_the_slug(api: dict) -> None:
    await remax_gallery_from_url(_URL + '/?utm_source=whatsapp')
    assert api['urls'][0].endswith('/findBySlug/casa-a-estrenar-en-venta-villa-elisa')


@pytest.mark.parametrize('url', [
    'https://www.argenprop.com/ph-en-venta-en-la-plata--9044111',
    'https://www.remax.com.ar/',
    'https://www.remax.com.ar/oficinas/algo',
    '',
])
async def test_non_listing_urls_are_left_alone(api: dict, url: str) -> None:
    """Sin slug de listing no hay nada que preguntar — y no se pega a la API."""
    assert await remax_gallery_from_url(url) == []
    assert api['urls'] == []


async def test_an_api_failure_never_breaks_the_import(api: dict) -> None:
    """La galería es un extra: si la API falla, el import sigue con lo que tenga."""
    api['status'] = 500
    assert await remax_gallery_from_url(_URL) == []


# ── Integración con el import ─────────────────────────────────────────────────


class _Res:
    def __init__(self, data): self.data = data


class _Q:
    def __init__(self, data=None): self._data = data or []
    def eq(self, *a, **kw): return self
    def limit(self, *a, **kw): return self
    async def execute(self): return _Res(self._data)


class _Table:
    def __init__(self, store): self._store = store
    def select(self, *a, **kw): return _Q()
    def insert(self, row):
        self._store.append(row)
        return _Q([{**row, 'id': 'p1'}])


class _SB:
    def __init__(self): self.rows: list[dict] = []
    def table(self, name): return _Table(self.rows)


async def test_the_import_prefers_the_portal_api_gallery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El DOM da 1 foto; la API da 37. El import tiene que quedarse con las 37.

    Y NO debe gastar el harvest headless: la galería ya está resuelta.
    """
    from app.services import importer

    harvested = {'calls': 0}

    async def fake_fetch(url: str): return 'texto ' * 40, ['solo-la-portada.jpg']
    async def fake_llm(url: str, text: str): return {'titulo': 'Casa'}, None
    async def fake_gallery(url: str): return [f'api-foto-{i}.jpg' for i in range(37)]
    async def fake_harvest(urls, render_budget: int = 8):
        harvested['calls'] += 1
        return {}
    async def fake_usage(*a, **kw): return None

    monkeypatch.setattr(importer, '_fetch_page', fake_fetch)
    monkeypatch.setattr(importer, '_extract_llm', fake_llm)
    monkeypatch.setattr(importer, 'remax_gallery_from_url', fake_gallery)
    monkeypatch.setattr(importer, 'harvest_page_images', fake_harvest)
    monkeypatch.setattr(importer, 'record_llm_usage', fake_usage)

    sb = _SB()
    await importer.import_property_from_url(sb, _URL)
    saved = sb.rows[0]['imagenes']
    assert len(saved) == 20, 'el modelo topea la galería en 20'
    assert saved[0] == 'api-foto-0.jpg'
    assert harvested['calls'] == 0, 'no hace falta el harvest headless'


async def test_a_portal_without_api_still_falls_back_to_harvest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Argenprop no tiene API: la escalera vieja tiene que seguir intacta."""
    from app.services import importer

    async def fake_fetch(url: str): return 'texto ' * 40, ['una.jpg']
    async def fake_llm(url: str, text: str): return {'titulo': 'PH'}, None
    async def no_api(url: str): return []
    async def fake_harvest(urls, render_budget: int = 8):
        return {urls[0]: [f'h{i}.jpg' for i in range(6)]}
    async def fake_usage(*a, **kw): return None

    monkeypatch.setattr(importer, '_fetch_page', fake_fetch)
    monkeypatch.setattr(importer, '_extract_llm', fake_llm)
    monkeypatch.setattr(importer, 'remax_gallery_from_url', no_api)
    monkeypatch.setattr(importer, 'harvest_page_images', fake_harvest)
    monkeypatch.setattr(importer, 'record_llm_usage', fake_usage)

    sb = _SB()
    await importer.import_property_from_url(sb, 'https://www.argenprop.com/ph--9044111')
    assert len(sb.rows[0]['imagenes']) == 6


async def test_a_dead_client_never_breaks_the_import(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Boom:
        def __init__(self, *a, **kw) -> None: pass
        async def __aenter__(self) -> '_Boom': return self
        async def __aexit__(self, *a) -> None: return None
        async def get(self, *a, **kw): raise httpx.ConnectError('sin red')

    monkeypatch.setattr(httpx, 'AsyncClient', _Boom)
    assert await remax_gallery_from_url(_URL) == []
