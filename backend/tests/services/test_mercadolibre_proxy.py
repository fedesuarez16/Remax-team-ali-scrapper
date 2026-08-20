"""MercadoLibre bloquea por IP de datacenter, y lo hace con un 200.

Medido en vivo el 2026-08-20, misma URL y mismos headers, sólo cambia la IP:

    IP residencial      → HTTP 200, 1.98 MB, listado completo
    proxy DATACENTER    → HTTP 200,   39 KB, /gz/account-verification
    proxy RESIDENTIAL   → HTTP 200, 1.98 MB, listado completo

Railway sale por datacenter, así que producción devolvía `0 propiedades en
mercadolibre` en TODA búsqueda mientras local traía 96. Y el modo de falla es
el peor posible: no hay excepción que atrapar ni status que mirar — el muro es
un 200 con HTML válido. Un `except` no lo ve; el warning que agregamos tampoco.

De ahí las dos piezas que se testean acá:
  1. salir por `SCRAPER_PROXY_URL` cuando está configurado, y
  2. reconocer el muro para que NUNCA vuelva a leerse como "no hay avisos".
"""
from __future__ import annotations

import logging

import httpx
import pytest

from app.core.config import settings
from app.models.property import ScrapingFilters
from app.services.apify import ApifyService, _ml_page_is_blocked

_WALL = (
    '<html><body><div class="gz-account-verification">'
    'Para continuar, verificá tu cuenta</div></body></html>'
)


def _card(href: str) -> str:
    return f'''
    <li class="ui-search-layout__item"><div class="poly-card">
      <a class="poly-component__title" href="{href}">Depto 2 amb</a>
      <span class="poly-component__headline">Departamento en venta</span>
      <span class="andes-money-amount__currency-symbol">US$</span>
      <span class="andes-money-amount__fraction">55.000</span>
      <span class="poly-component__location">C. 56 720, La Plata, Buenos Aires</span>
      <img data-src="https://http2.mlstatic.com/D_NQ_NP_1-MLA1_012026-E.webp"/>
    </div></li>'''


def _page(*cards: str) -> str:
    return f'<html><body><ol class="ui-search-layout">{"".join(cards)}</ol></body></html>'


@pytest.fixture
def fetched(monkeypatch):
    state: dict = {'kwargs': [], 'urls': [], 'text': _page(_card('https://x.com/MLA-1'))}

    class _Resp:
        def __init__(self, text: str) -> None:
            self.text = text
            self.status_code = 200

        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, *a, **kw) -> None:
            state['kwargs'].append(kw)

        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None

        async def get(self, url, *a, **kw):
            state['urls'].append(url)
            return _Resp(state['text'])

    monkeypatch.setattr(httpx, 'AsyncClient', _Client)
    return state


@pytest.fixture
def service() -> ApifyService:
    return ApifyService(api_token='unused')


async def _noop(src: str, status: str, count: int) -> None:
    return None


def _filters() -> ScrapingFilters:
    return ScrapingFilters(zona='La Plata', tipo_operacion='venta',
                           tipos_propiedad=['departamento'])


class TestReconoceElMuro:
    def test_la_pagina_de_verificacion_es_un_bloqueo(self):
        assert _ml_page_is_blocked(_WALL) is True

    def test_un_listado_real_no_es_un_bloqueo(self):
        assert _ml_page_is_blocked(_page(_card('https://x.com/MLA-1'))) is False

    def test_una_pagina_vacia_no_es_un_bloqueo(self):
        """Una zona sin avisos es un resultado legítimo, no un muro: si se
        confundieran, la cadena de candidatos de zona dejaría de degradar."""
        assert _ml_page_is_blocked(_page()) is False


class TestSaleporElProxy:
    async def test_usa_el_proxy_configurado(self, service, fetched, monkeypatch):
        monkeypatch.setattr(settings, 'SCRAPER_PROXY_URL',
                            'http://groups-RESIDENTIAL:pw@proxy.apify.com:8000')
        await service.scrape_source('mercadolibre', _filters(), _noop)

        assert fetched['kwargs'], 'no se construyó ningún cliente'
        assert any(
            kw.get('proxy') == 'http://groups-RESIDENTIAL:pw@proxy.apify.com:8000'
            for kw in fetched['kwargs']
        ), fetched['kwargs']

    async def test_sin_proxy_configurado_sale_directo(self, service, fetched, monkeypatch):
        """En local la IP ya sirve; nadie debería pagar tráfico sin necesidad."""
        monkeypatch.setattr(settings, 'SCRAPER_PROXY_URL', '')
        await service.scrape_source('mercadolibre', _filters(), _noop)

        assert all(kw.get('proxy') in (None, '') for kw in fetched['kwargs'])


class TestElMuroNoSeLeeComoCero:
    async def test_avisa_cuando_lo_bloquean(self, service, fetched, monkeypatch, caplog):
        """El bug que costó producción: 0 propiedades sin una sola señal."""
        fetched['text'] = _WALL
        monkeypatch.setattr(settings, 'SCRAPER_PROXY_URL', '')
        with caplog.at_level(logging.WARNING):
            res = await service.scrape_source('mercadolibre', _filters(), _noop)

        assert res == []
        mensajes = [r.getMessage().lower() for r in caplog.records]
        assert any('mercadolibre' in m for m in mensajes), mensajes
        assert any('bloque' in m or 'verificaci' in m for m in mensajes), mensajes

    async def test_un_listado_normal_no_avisa_nada(self, service, fetched, monkeypatch, caplog):
        monkeypatch.setattr(settings, 'SCRAPER_PROXY_URL', '')
        with caplog.at_level(logging.WARNING):
            await service.scrape_source('mercadolibre', _filters(), _noop)

        assert not [r for r in caplog.records if 'bloque' in r.getMessage().lower()]
