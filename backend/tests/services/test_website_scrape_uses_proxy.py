"""Test-first: los sitios de inmobiliarias se scrapean como los portales.

El track de inmobiliarias era el único que salía DIRECTO y anunciándose como
bot (`User-Agent: PropSearchBot/1.0`). Desde Railway eso es una IP de
datacenter con cartel de robot: la mayoría de los sitios contesta 403 o HTML
vacío, el `except` lo convertía en `[]`, y la búsqueda volvía sin una sola
propiedad sin decir por qué. Los portales ya sabían esto (ver el docstring de
`_scrape_mercadolibre`): UA de browser y salida por `SCRAPER_PROXY_URL`.

Y encima cada sitio abría un Chromium para rehacer galerías — hasta 6 páginas
con `networkidle` y todas las imágenes bajadas por un proxy que se factura por
GB. Con 260 inmobiliarias, eso es el cuelgue y el gasto reportados.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.core.config import settings
from app.services import apify


class _Resp:
    status_code = 200

    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


_HOME = """
<html><body>
  <h1>Inmobiliaria Test</h1>
  <p>Departamento en venta USD 120.000, 2 ambientes, Palermo</p>
  <a href="/propiedades">Ver propiedades</a>
</body></html>
"""


class _SpyClient:
    """Captura cómo se construyó el cliente httpx y qué URLs pidió."""

    last: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        type(self).last = {**kwargs, 'urls': []}

    async def __aenter__(self) -> _SpyClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get(self, url: str) -> _Resp:
        type(self).last['urls'].append(url)
        return _Resp(_HOME)


async def _noop_progress(src: str, status: str, count: int) -> None:
    return None


@pytest.fixture
def spy(monkeypatch):
    monkeypatch.setattr(apify.httpx, 'AsyncClient', _SpyClient)
    return _SpyClient


@pytest.mark.asyncio
async def test_egress_goes_through_the_residential_proxy(spy, monkeypatch) -> None:
    monkeypatch.setattr(
        settings, 'SCRAPER_PROXY_URL',
        'http://groups-RESIDENTIAL:pw@proxy.apify.com:8000',
    )
    monkeypatch.setattr(settings, 'WEBSITE_RENDER_GALLERIES', False)

    await apify._scrape_website_direct('https://inmo.com.ar', _noop_progress)

    proxy = spy.last['proxy']
    assert proxy is not None
    assert 'proxy.apify.com:8000' in proxy
    # Sesión propia por sitio: una IP quemada no puede arrastrar a las otras 259.
    assert 'session-web' in proxy


@pytest.mark.asyncio
async def test_no_proxy_configured_means_direct_egress(spy, monkeypatch) -> None:
    """En local la salida directa es lo correcto: el tráfico residencial se
    factura por GB y no tiene sentido pagarlo para probar."""
    monkeypatch.setattr(settings, 'SCRAPER_PROXY_URL', '')
    monkeypatch.setattr(settings, 'WEBSITE_RENDER_GALLERIES', False)

    await apify._scrape_website_direct('https://inmo.com.ar', _noop_progress)

    assert spy.last['proxy'] is None


@pytest.mark.asyncio
async def test_identifies_as_a_real_browser(spy, monkeypatch) -> None:
    monkeypatch.setattr(settings, 'SCRAPER_PROXY_URL', '')
    monkeypatch.setattr(settings, 'WEBSITE_RENDER_GALLERIES', False)

    await apify._scrape_website_direct('https://inmo.com.ar', _noop_progress)

    ua = spy.last['headers']['User-Agent']
    assert 'PropSearchBot' not in ua
    assert ua == apify._BROWSER_UA


@pytest.mark.asyncio
async def test_no_headless_browser_per_site_by_default(spy, monkeypatch) -> None:
    """Un Chromium por inmobiliaria × 260 es el cuelgue. Tiene que estar
    apagado salvo que alguien lo encienda a propósito."""
    monkeypatch.setattr(settings, 'SCRAPER_PROXY_URL', '')
    monkeypatch.setattr(settings, 'WEBSITE_RENDER_GALLERIES', False)
    called = False

    async def _render(urls: list[str]) -> dict[str, list[str]]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(apify, '_render_gallery_images', _render)

    await apify._scrape_website_direct('https://inmo.com.ar', _noop_progress)

    assert called is False


@pytest.mark.asyncio
async def test_gallery_rendering_is_opt_in(spy, monkeypatch) -> None:
    monkeypatch.setattr(settings, 'SCRAPER_PROXY_URL', '')
    monkeypatch.setattr(settings, 'WEBSITE_RENDER_GALLERIES', True)
    seen: list[list[str]] = []

    async def _render(urls: list[str]) -> dict[str, list[str]]:
        seen.append(urls)
        return {u: ['https://cdn/foto.jpg'] for u in urls}

    monkeypatch.setattr(apify, '_render_gallery_images', _render)

    pages = await apify._scrape_website_direct('https://inmo.com.ar', _noop_progress)

    assert seen, 'con el flag encendido el render tiene que correr'
    assert pages[0]['images'] == ['https://cdn/foto.jpg']


@pytest.mark.asyncio
async def test_subpage_budget_is_configurable(spy, monkeypatch) -> None:
    monkeypatch.setattr(settings, 'SCRAPER_PROXY_URL', '')
    monkeypatch.setattr(settings, 'WEBSITE_RENDER_GALLERIES', False)
    monkeypatch.setattr(settings, 'WEBSITE_MAX_SUBPAGES', 0)

    await apify._scrape_website_direct('https://inmo.com.ar', _noop_progress)

    # Sólo la home: sin presupuesto de sub-páginas no se pide nada más.
    assert spy.last['urls'] == ['https://inmo.com.ar']


@pytest.mark.asyncio
async def test_image_harvest_also_uses_the_proxy(spy, monkeypatch) -> None:
    """Las fichas se defienden igual que las homes: si el harvest sale directo
    y con UA de bot, la propiedad se guarda sin fotos."""
    monkeypatch.setattr(
        settings, 'SCRAPER_PROXY_URL',
        'http://groups-RESIDENTIAL:pw@proxy.apify.com:8000',
    )

    async def _no_render(urls: list[str]) -> dict[str, list[str]]:
        return {}

    monkeypatch.setattr(apify, '_render_gallery_images', _no_render)

    await apify.harvest_page_images(['https://inmo.com.ar/ficha/1'])

    assert 'proxy.apify.com:8000' in spy.last['proxy']
    assert spy.last['headers']['User-Agent'] == apify._BROWSER_UA
