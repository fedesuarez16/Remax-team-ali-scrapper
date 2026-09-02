"""El track de inmobiliarias vuelve a Apify — pero pidiendo el HTML.

`c6ea5f0` sacó el Website Content Crawler y puso un scraper directo con httpx.
El problema que eso trajo: la mayoría de los sitios de inmobiliarias son
JS-renderizados, y a httpx le contestan un cascarón vacío — sin texto y sin
fotos. De ahí las propiedades sin imágenes.

Pero el camino viejo por Apify tampoco servía para eso, y por un motivo
distinto: pedía `htmlTransformer: 'readableText'` y mapeaba sólo
`{url, text}`. Tiraba el HTML antes de que nadie pudiera mirarlo, así que
devolvía CERO imágenes. Restaurarlo tal cual habría empeorado el síntoma que
lo motivó.

Este camino pide `saveHtml` con `htmlTransformer: 'none'` y corre sobre esa
respuesta el MISMO `_clean_page_text` y el MISMO `_extract_images_from_html`
que usa el camino directo. El navegador lo pone Apify; la limpieza y las fotos
las seguimos haciendo nosotros, con el código que ya está probado.

Los portales NO se tocan: siguen por su camino directo. El interruptor es sólo
para inmobiliarias.
"""
from typing import Any

import pytest

from app.core.config import settings
from app.services.apify import ApifyService

_HTML = """
<html><head><meta property="og:image" content="https://inmo.com/foto1.jpg"></head>
<body>
  <ul class="main-menu"><li><a href="/">Inicio</a></li></ul>
  <main>
    <h2>Departamento 3 ambientes</h2>
    <p>USD 120.000 — Calle 50 nº 456, La Plata</p>
  </main>
</body></html>
"""


@pytest.fixture()
def service() -> ApifyService:
    return ApifyService(api_token='apify_api_TEST')


@pytest.fixture()
def por_apify(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, 'WEBSITE_USE_APIFY', True)


@pytest.fixture()
def actor(service: ApifyService, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Captura el input del actor y devuelve una página con HTML."""
    visto: dict[str, Any] = {}

    async def _fake_run(source: str, actor_id: str, input_data: dict[str, Any]) -> list[dict[str, Any]]:
        visto['source'] = source
        visto['actor_id'] = actor_id
        visto['input'] = input_data
        return [{'url': 'https://inmo.com/propiedades', 'html': _HTML}]

    monkeypatch.setattr(service, '_run_actor', _fake_run)
    return visto


async def _noop(_s: str, _st: str, _c: int) -> None:
    return None


# ── El interruptor ────────────────────────────────────────────────────────────

async def test_apagado_sigue_yendo_directo(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El default no puede cambiar el gasto de nadie en silencio."""
    monkeypatch.setattr(settings, 'WEBSITE_USE_APIFY', False)
    llamado: list[str] = []

    async def _directo(url: str, on_progress: Any) -> list[dict[str, str]]:
        llamado.append(url)
        return []

    monkeypatch.setattr('app.services.apify._scrape_website_direct', _directo)

    await service.scrape_website('https://inmo.com/', _noop)

    assert llamado == ['https://inmo.com/']


async def test_encendido_usa_el_actor(
    service: ApifyService, por_apify: None, actor: dict[str, Any],
) -> None:
    await service.scrape_website('https://inmo.com/', _noop)

    assert actor['source'] == 'website'
    assert actor['input']['startUrls'] == [{'url': 'https://inmo.com/'}]


# ── Lo que se le pide al actor ────────────────────────────────────────────────

async def test_se_pide_el_html_crudo(
    service: ApifyService, por_apify: None, actor: dict[str, Any],
) -> None:
    """El bug del camino viejo: `readableText` tiraba el HTML y con él las
    fotos. Sin estas dos claves este cambio no arregla nada."""
    await service.scrape_website('https://inmo.com/', _noop)

    assert actor['input']['saveHtml'] is True
    assert actor['input']['htmlTransformer'] == 'none'


async def test_no_se_bloquea_la_carga_de_imagenes(
    service: ApifyService, por_apify: None, actor: dict[str, Any],
) -> None:
    """`blockMedia` acelera el crawl a costa de no cargar las imágenes — que es
    exactamente lo que venimos a buscar."""
    await service.scrape_website('https://inmo.com/', _noop)

    assert actor['input'].get('blockMedia') is False


# ── Lo que sale ───────────────────────────────────────────────────────────────

async def test_el_texto_pasa_por_la_misma_limpieza(
    service: ApifyService, por_apify: None, actor: dict[str, Any],
) -> None:
    """El menú no puede volver a comerse el presupuesto de texto sólo porque el
    HTML ahora lo trae Apify."""
    pages = await service.scrape_website('https://inmo.com/', _noop)

    assert 'Inicio' not in pages[0]['text']
    assert 'USD 120.000' in pages[0]['text']


async def test_las_imagenes_salen_del_html(
    service: ApifyService, por_apify: None, actor: dict[str, Any],
) -> None:
    """Lo que el camino viejo por Apify no hacía."""
    pages = await service.scrape_website('https://inmo.com/', _noop)

    assert pages[0]['images'] == ['https://inmo.com/foto1.jpg']


async def test_una_pagina_sin_html_usable_no_entra(
    service: ApifyService, por_apify: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una página vacía cuesta una llamada al LLM y no puede dar nada."""
    async def _vacio(source: str, actor_id: str, input_data: dict[str, Any]) -> list[dict[str, Any]]:
        return [{'url': 'https://inmo.com/x', 'html': ''}, {'url': 'https://inmo.com/y'}]

    monkeypatch.setattr(service, '_run_actor', _vacio)

    assert await service.scrape_website('https://inmo.com/', _noop) == []


async def test_si_el_actor_falla_la_inmobiliaria_no_tira_la_busqueda(
    service: ApifyService, por_apify: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mismo contrato que el camino directo: un sitio que explota vale `[]`.
    Con 552 sitios, una excepción no puede tirar la corrida."""
    async def _boom(source: str, actor_id: str, input_data: dict[str, Any]) -> list[dict[str, Any]]:
        raise RuntimeError('actor caído')

    monkeypatch.setattr(service, '_run_actor', _boom)

    assert await service.scrape_website('https://inmo.com/', _noop) == []
