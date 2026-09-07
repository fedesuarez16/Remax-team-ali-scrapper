from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.config import settings
from app.models.property import ScrapingFilters
from app.services import apify


def card(listing_id, *, operation='Venta', price='U$S 95.000', location='City Bell'):
    return f'''<div class="ResultadoCaja" id="contenidoPropiedad{listing_id}">
      <div class="resultadoTipo"><a href="https://www.inmobusqueda.com.ar/ficha-{listing_id}">
        Casa en {operation}</a></div>
      <div class="resultadoLocalidad">Calle de prueba 100, {location}, La Plata</div>
      <div class="resultadoPrecio">{price}</div><div class="rdBox">3 amb</div>
    </div>'''


def client(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(
        transport=httpx.MockTransport(handler), follow_redirects=True,
    ))


@pytest.mark.parametrize('stage', ['autocomplete', 'listing'])
async def test_antibot_verification_is_reported_as_an_error(monkeypatch, stage):
    apify._INMOBUSQUEDA_SLUG_CACHE.clear()
    if stage == 'listing':
        monkeypatch.setattr(apify, '_resolve_inmobusqueda_zona', AsyncMock(return_value='city-bell'))
    client(monkeypatch, lambda r: httpx.Response(
        200, text='<html><title>No soy bot -</title><form action="nosoybot.do.php"></form></html>',
    ))
    with pytest.raises(apify.InmoBusquedaBlocked, match='verificación antibot'):
        await apify._scrape_inmobusqueda(ScrapingFilters(zona='City Bell'), AsyncMock())


async def test_unmatched_short_page_does_not_stop_the_search(monkeypatch):
    monkeypatch.setattr(settings, 'INMOBUSQUEDA_MAX_PAGES', 0)
    monkeypatch.setattr(apify, '_resolve_inmobusqueda_zona', AsyncMock(return_value='city-bell'))
    requests = []

    def handler(request):
        requests.append(request.url.path)
        if '-pagina-2' in request.url.path:
            return httpx.Response(200, text=card(2))
        if '-pagina-3' in request.url.path:
            return httpx.Response(200, text='<html>Sin resultados</html>')
        return httpx.Response(200, text=card(1, operation='Alquiler'))

    client(monkeypatch, handler)
    props = await apify._scrape_inmobusqueda(ScrapingFilters(
        zona='City Bell', tipo_operacion='venta', tipos_propiedad=['casa'], precio_max=100000,
    ), AsyncMock())
    assert [p.url_origen.rsplit('-', 1)[1] for p in props] == ['2']
    assert len(requests) == 3


async def test_repeated_out_of_zone_page_stops_without_relying_on_matches(monkeypatch):
    monkeypatch.setattr(settings, 'INMOBUSQUEDA_MAX_PAGES', 0)
    monkeypatch.setattr(apify, '_resolve_inmobusqueda_zona', AsyncMock(return_value='city-bell'))
    requests = []

    def handler(request):
        requests.append(request.url)
        return httpx.Response(200, text=card(1, location='Córdoba'))

    client(monkeypatch, handler)
    assert await apify._scrape_inmobusqueda(
        ScrapingFilters(zona='City Bell'), AsyncMock(),
    ) == []
    assert len(requests) == 2


async def test_http_block_is_not_reported_as_zero_results(monkeypatch):
    monkeypatch.setattr(apify, '_resolve_inmobusqueda_zona', AsyncMock(return_value='city-bell'))
    client(monkeypatch, lambda request: httpx.Response(403))
    with pytest.raises(RuntimeError, match='no se pudo leer el listado'):
        await apify._scrape_inmobusqueda(ScrapingFilters(zona='City Bell'), AsyncMock())
