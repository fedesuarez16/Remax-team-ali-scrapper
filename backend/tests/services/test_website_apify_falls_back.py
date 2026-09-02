"""Si Apify no puede correr, se vuelve al scraper directo — no a cero.

Caso real (2026-09-02): la cuenta de Apify se quedó sin crédito y el actor
contestó `402 Payment Required` a las 552 inmobiliarias de una búsqueda. El
código devolvía `[]` por cada sitio, así que un problema de FACTURACIÓN dejó la
búsqueda en cero resultados de inmobiliarias — cuando el scraper directo, con
todas sus limitaciones, igual recupera un tercio de los sitios.

Dos cosas se arreglan:

1. **Fallback.** Un sitio que el actor no pudo crawlear se reintenta por httpx
   directo. Peor que Apify, muchísimo mejor que nada.

2. **Cortocircuito.** Un 402 no es un problema DE ESE SITIO, es de la cuenta:
   los 551 intentos siguientes van a fallar igual. Reintentarlos son 551
   requests inútiles y 551 líneas de log que tapan el motivo real. Al primero,
   se deja de intentar y se avisa UNA vez, fuerte.

El corte es sólo para esta corrida del proceso: recargar el server (o
recargarle crédito a la cuenta) lo limpia.
"""
from typing import Any

import httpx
import pytest

from app.core.config import settings
from app.services import apify as mod
from app.services.apify import ApifyService


@pytest.fixture(autouse=True)
def _limpio(monkeypatch: pytest.MonkeyPatch) -> None:
    mod._apify_sin_credito = False
    monkeypatch.setattr(settings, 'WEBSITE_USE_APIFY', True)


@pytest.fixture()
def service() -> ApifyService:
    return ApifyService(api_token='apify_api_TEST')


@pytest.fixture()
def directo(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Registra a qué sitios llegó el scraper directo."""
    visto: list[str] = []

    async def _fake(url: str, on_progress: Any) -> list[dict[str, str]]:
        visto.append(url)
        return [{'url': url, 'text': 'Depto 2 amb USD 120.000', 'images': []}]

    monkeypatch.setattr(mod, '_scrape_website_direct', _fake)
    return visto


def _sin_credito(service: ApifyService, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """El actor contesta 402 a todo, como la cuenta sin crédito."""
    intentos: list[str] = []
    req = httpx.Request('POST', 'https://api.apify.com/v2/acts/x/runs')
    resp = httpx.Response(402, request=req)

    async def _boom(source: str, actor_id: str, input_data: dict[str, Any]) -> list[dict[str, Any]]:
        intentos.append(input_data['startUrls'][0]['url'])
        raise httpx.HTTPStatusError('402 Payment Required', request=req, response=resp)

    monkeypatch.setattr(service, '_run_actor', _boom)
    return intentos


async def _noop(_s: str, _st: str, _c: int) -> None:
    return None


# ── Fallback ──────────────────────────────────────────────────────────────────

async def test_sin_credito_el_sitio_igual_se_scrapea_directo(
    service: ApifyService, directo: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lo que costó una búsqueda entera: devolver [] en vez de intentar."""
    _sin_credito(service, monkeypatch)

    pages = await service.scrape_website('https://inmo.com/', _noop)

    assert directo == ['https://inmo.com/']
    assert pages and pages[0]['text']


async def test_cualquier_falla_del_actor_tambien_cae_al_directo(
    service: ApifyService, directo: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No sólo el 402: un actor caído o un timeout tampoco pueden costar el
    sitio entero."""
    async def _boom(source: str, actor_id: str, input_data: dict[str, Any]) -> list[dict[str, Any]]:
        raise RuntimeError('actor caído')

    monkeypatch.setattr(service, '_run_actor', _boom)

    pages = await service.scrape_website('https://inmo.com/', _noop)

    assert directo == ['https://inmo.com/']
    assert pages


# ── Cortocircuito ─────────────────────────────────────────────────────────────

async def test_despues_del_primer_402_no_se_intenta_mas(
    service: ApifyService, directo: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """551 reintentos de algo que ya sabemos que va a fallar son 551 requests
    inútiles y 551 líneas de log que tapan el motivo real."""
    intentos = _sin_credito(service, monkeypatch)

    for i in range(5):
        await service.scrape_website(f'https://inmo{i}.com/', _noop)

    assert len(intentos) == 1, f'se intentó {len(intentos)} veces contra una cuenta sin crédito'
    assert len(directo) == 5, 'los 5 sitios tienen que haber ido igual por el directo'


async def test_un_error_comun_no_apaga_el_actor_para_los_demas(
    service: ApifyService, directo: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un sitio que hace explotar al actor es problema DE ESE SITIO. Apagar
    Apify para toda la búsqueda por uno solo sería tirar el 50% de cobertura
    que acabamos de ganar."""
    intentos: list[str] = []

    async def _boom(source: str, actor_id: str, input_data: dict[str, Any]) -> list[dict[str, Any]]:
        intentos.append(input_data['startUrls'][0]['url'])
        raise RuntimeError('actor caído')

    monkeypatch.setattr(service, '_run_actor', _boom)

    for i in range(3):
        await service.scrape_website(f'https://inmo{i}.com/', _noop)

    assert len(intentos) == 3


async def test_con_credito_no_se_toca_el_directo(
    service: ApifyService, directo: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El camino feliz no cambia."""
    async def _ok(source: str, actor_id: str, input_data: dict[str, Any]) -> list[dict[str, Any]]:
        return [{'url': 'https://inmo.com/p', 'html': '<html><body><main>USD 120.000 en La Plata, 3 ambientes con cochera</main></body></html>'}]

    monkeypatch.setattr(service, '_run_actor', _ok)

    pages = await service.scrape_website('https://inmo.com/', _noop)

    assert directo == []
    assert 'USD 120.000' in pages[0]['text']
