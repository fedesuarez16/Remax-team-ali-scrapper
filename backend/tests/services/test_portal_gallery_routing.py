"""Test-first: el parser propio del portal manda, en TODOS los caminos.

`importer.import_property_from_url` ya consulta `portal_gallery_from_url` antes
del harvest genérico (ver `test_remax_ficha_gallery.py`). Los otros dos caminos
que traen fotos no lo hacían y quedaron con la escalera vieja:

  1. `ficha._fetch_full_gallery` — despachaba `fuente in ('googlemaps',
     'argenprop', 'remax')` al harvest genérico. RE/MAX es una SPA de Angular:
     su HTML trae UNA foto (el `og:image`), así que la ficha se regeneraba con
     1 sola. Medido en vivo sobre
     `https://www.remax.com.ar/listings/departamentos-en-venta-la-plata`:
     `findBySlug` devuelve `photos[19]`, el DOM devuelve 1.

  2. `nodes.extract_website_properties_llm` — la cola de fotos llamaba
     `harvest_page_images` para TODA ficha. Si el aviso vive en un portal con
     parser propio (RE/MAX, MercadoLibre, ZonaProp, Century21) esa es la peor
     fuente disponible y encima la única consultada.

Contrato que fijan estos tests: si el host tiene parser propio, se le pregunta
al parser PRIMERO; el harvest genérico queda para los que no lo tienen.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.graphs.extraction import nodes
from app.graphs.extraction.nodes import extract_website_properties_llm
from app.services import ficha
from tests.conftest import listing_text

_REMAX_URL = 'https://www.remax.com.ar/listings/departamentos-en-venta-la-plata'


# ── 1. Regenerar la ficha de una propiedad de RE/MAX ──────────────────────────


async def test_a_remax_ficha_asks_the_portal_api_not_the_dom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El caso reportado: la ficha se regeneraba con 1 foto de las 19."""
    from app.services import apify

    harvested: list[list[str]] = []

    async def fake_portal(url: str, allow_escalation: bool = True) -> list[str]:
        return [f'api-{i}.jpg' for i in range(19)]

    async def fake_harvest(urls: list[str], render_budget: int = 8) -> dict[str, list[str]]:
        harvested.append(urls)
        return {urls[0]: ['solo-el-og-image.jpg']}

    monkeypatch.setattr(ficha, 'portal_gallery_from_url', fake_portal)
    monkeypatch.setattr(apify, 'harvest_page_images', fake_harvest)

    gallery = await ficha._fetch_full_gallery(
        {'fuente': 'remax', 'url_origen': _REMAX_URL}
    )

    assert len(gallery) == 19, 'la ficha se quedó con el og:image en vez de la API'
    assert harvested == [], 'no hace falta el harvest genérico: la API ya resolvió'


async def test_a_portal_without_a_parser_keeps_the_generic_harvest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Argenprop no tiene parser propio: la escalera vieja sigue intacta."""
    from app.services import apify

    url = 'https://www.argenprop.com/ph--9044111'

    async def fake_harvest(urls: list[str], render_budget: int = 8) -> dict[str, list[str]]:
        return {urls[0]: [f'h{i}.jpg' for i in range(6)]}

    monkeypatch.setattr(apify, 'harvest_page_images', fake_harvest)

    gallery = await ficha._fetch_full_gallery(
        {'fuente': 'argenprop', 'url_origen': url}
    )
    assert len(gallery) == 6


async def test_the_generic_harvest_still_rescues_a_dead_portal_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si la API del portal vuelve vacía, el harvest genérico es mejor que nada."""
    from app.services import apify

    async def no_api(url: str, allow_escalation: bool = True) -> list[str]:
        return []

    async def fake_harvest(urls: list[str], render_budget: int = 8) -> dict[str, list[str]]:
        return {urls[0]: ['portada.jpg']}

    monkeypatch.setattr(ficha, 'portal_gallery_from_url', no_api)
    monkeypatch.setattr(apify, 'harvest_page_images', fake_harvest)

    gallery = await ficha._fetch_full_gallery(
        {'fuente': 'remax', 'url_origen': _REMAX_URL}
    )
    assert gallery == ['portada.jpg']


# ── 2. Cola de fotos de la extracción por sitio web ───────────────────────────


class _ToolUse:
    type = 'tool_use'

    def __init__(self, propiedades: list[dict[str, Any]]) -> None:
        self.input = {'propiedades': propiedades}


class _Msg:
    usage = None

    def __init__(self, propiedades: list[dict[str, Any]]) -> None:
        self.content = [_ToolUse(propiedades)]


def _stub_llm(monkeypatch: pytest.MonkeyPatch, propiedades: list[dict[str, Any]]) -> None:
    class _Messages:
        async def create(self, **kwargs: Any) -> Any:
            return _Msg(propiedades)

    async def _noop_usage(*a: Any, **kw: Any) -> None:
        return None

    async def _dispatch(name: str, data: dict[str, Any], config: Any = None) -> None:
        return None

    monkeypatch.setattr(nodes._client, 'messages', _Messages(), raising=False)
    monkeypatch.setattr(nodes, 'record_llm_usage', _noop_usage)
    monkeypatch.setattr(nodes, 'adispatch_custom_event', _dispatch)


@pytest.mark.asyncio
async def test_the_photo_queue_prefers_the_portal_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una ficha de RE/MAX linkeada desde un sitio no puede salir con 1 foto."""
    _stub_llm(monkeypatch, [{
        'titulo': 'Depto', 'precio': 100000, 'url_ficha': _REMAX_URL,
    }])

    harvested: list[list[str]] = []

    async def fake_portal(url: str, allow_escalation: bool = True) -> list[str]:
        return [f'api-{i}.jpg' for i in range(19)]

    async def fake_harvest(urls: list[str], render_budget: int = 8) -> dict[str, list[str]]:
        harvested.append(urls)
        return {}

    monkeypatch.setattr(nodes, 'portal_gallery_from_url', fake_portal)
    monkeypatch.setattr(nodes, 'harvest_page_images', fake_harvest)

    out = await extract_website_properties_llm(
        {'website_pages': [{'url': 'https://inmo.com/listado', 'text': listing_text()}],
         'job_id': 'job-1'},
        {'configurable': {}},
    )

    props = out['website_properties']
    assert len(props[0].imagenes) == 19, 'la ficha de RE/MAX salió sin galería'
    assert harvested == [], 'RE/MAX no se resuelve con el harvest genérico'


@pytest.mark.asyncio
async def test_the_photo_queue_still_harvests_sites_without_a_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La ficha de una inmobiliaria común sigue yendo al harvest genérico."""
    ficha_url = 'https://inmo.com/propiedad/42'
    _stub_llm(monkeypatch, [{
        'titulo': 'Casa', 'precio': 200000, 'url_ficha': ficha_url,
    }])

    async def no_portal(url: str, allow_escalation: bool = True) -> list[str]:
        return []

    async def fake_harvest(urls: list[str], render_budget: int = 8) -> dict[str, list[str]]:
        return {urls[0]: [f'h{i}.jpg' for i in range(7)]}

    monkeypatch.setattr(nodes, 'portal_gallery_from_url', no_portal)
    monkeypatch.setattr(nodes, 'harvest_page_images', fake_harvest)

    out = await extract_website_properties_llm(
        {'website_pages': [{'url': 'https://inmo.com/listado', 'text': listing_text()}],
         'job_id': 'job-2'},
        {'configurable': {}},
    )
    assert len(out['website_properties'][0].imagenes) == 7
