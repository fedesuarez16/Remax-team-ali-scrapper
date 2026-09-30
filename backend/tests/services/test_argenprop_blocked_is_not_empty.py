"""Argenprop blocking us is NOT "there are no properties".

THE BUG, MEASURED 2026-09-18. Every Argenprop search returned 0 — City Bell
included, so nothing to do with gated communities — while paying for the actor
run. Dumping the raw dataset item showed why:

    crawl.httpStatusCode : 405
    html (3045 bytes)    : <title>Human Verification</title>
                           <div id="captcha-container" ...
    text (514 bytes)     : "Let's confirm you are human. Complete the
                            security check..."

The portal served a captcha to Apify's datacenter IP. `_scrape_argenprop` read
`page['html']`, handed 3 KB of challenge markup to the card parser, got no
cards, and reported `done count=0` — indistinguishable from an honest empty
listing. Nobody could see it: the number was plausible and the log was silent.

Two things this pins:

1. A BLOCK IS AN ERROR, NOT AN ANSWER. `importer.py` already draws this line
   (`PortalBlocked`: "No dice NADA sobre si la propiedad existe") and its
   `_BLOCKED_STATUS` already contains 405. `_scrape_argenprop` ignored the
   status the actor hands it.

2. DETECTION IS BY STATUS AND VISIBLE TEXT, NEVER BY VENDOR NAME. Verified in
   `importer.py` against the live site: argenprop.com injects
   `captcha-sdk.awswaf.com/challenge.js` into EVERY page, real listings
   included. Matching the vendor flagged perfectly good pages.

And the fallback is not speculative: plain httpx with a browser User-Agent
pulls the real page — measured, 673 KB for `casas/venta/miralagos`, which the
existing card parser turns into 6 properties.
"""
from __future__ import annotations

import httpx
import pytest

from app.models.property import ScrapingFilters
from app.services import apify

# The real challenge, trimmed. Two signals: a blocked status on `crawl`, and a
# body with almost no visible text.
_DESAFIO_HTML = (
    '<html><head><title>Human Verification</title></head><body>'
    '<div id="captcha-container" dir="ltr"><div id="root">'
    "Let's confirm you are human. Complete the security check."
    '</div></div><script src="https://captcha-sdk.awswaf.com/challenge.js"></script>'
    '</body></html>'
)

# A real listing page. The card attributes are copied off the live
# `casas/venta/miralagos` DOM (`idaviso`, `montonormalizado`, `idmoneda` …)
# because the parser reads those, not classes — a hand-waved `<div>` parses to
# zero and would make this file pass while proving nothing.
#
# It also carries the SAME awswaf script argenprop injects everywhere.
# Detecting the vendor would flag this page too: that is the regression the
# visible-text rule exists for.
def _card(n: int) -> str:
    return (
        f'<div class="listing__item" id="{n}">'
        f'<a href="/casa-en-venta-en-miralagos-4-ambientes--{n}" class="card" '
        f'idaviso="{n}" idtipopropiedad="3" idtipooperacion="1" dormitorios="3" '
        f'idmoneda="2" montonormalizado="245000" montooperacion="245000">'
        '<h2 class="card__title">Casa en venta en Miralagos 4 ambientes</h2>'
        '<p class="card__address">Miralagos, La Plata</p>'
        '<p class="card__info">Casa en Miralagos con pileta, quincho, tres '
        'dormitorios y cochera doble. Lote de 1200 m2 parquizado con riego, '
        'excelente estado.</p></a></div>'
    )


_PAGINA_REAL = (
    '<html><body><script src="https://captcha-sdk.awswaf.com/challenge.js"></script>'
    + ''.join(_card(17292947 + i) for i in range(3))
    + '</body></html>'
)


def _item(html: str, status: int = 200) -> dict:
    """One `apify~website-content-crawler` dataset item, real shape."""
    return {
        'url': 'https://www.argenprop.com/casas/venta/miralagos',
        'crawl': {'httpStatusCode': status,
                  'loadedUrl': 'https://www.argenprop.com/casas/venta/miralagos'},
        'html': html,
    }


class TestReconocerElBloqueo:
    def test_un_405_es_un_bloqueo(self):
        """El status que devolvió Argenprop en vivo. Ya estaba en
        `importer._BLOCKED_STATUS`; acá nadie lo miraba."""
        assert apify._argenprop_usable_html(_item(_PAGINA_REAL, status=405)) is None

    @pytest.mark.parametrize('status', [401, 403, 405, 429, 503])
    def test_todos_los_status_de_rechazo(self, status: int):
        """Misma lista que el importer: el portal nos rechaza a NOSOTROS, no a
        la URL."""
        assert apify._argenprop_usable_html(_item(_PAGINA_REAL, status=status)) is None

    def test_un_desafio_con_200_tambien_es_un_bloqueo(self):
        """Un challenge de WAF suele volver 200 con HTML válido, así que el
        status solo no alcanza."""
        assert apify._argenprop_usable_html(_item(_DESAFIO_HTML)) is None

    def test_una_pagina_real_pasa(self):
        assert apify._argenprop_usable_html(_item(_PAGINA_REAL)) == _PAGINA_REAL

    def test_el_script_del_vendor_NO_es_la_señal(self):
        """La regresión que el importer ya documenta: argenprop inyecta
        `captcha-sdk.awswaf.com` en TODAS sus páginas, ficha real incluida.
        Detectar por vendor marcaba páginas buenas y mandaba cada búsqueda a
        pagar un run al pedo."""
        assert 'awswaf' in _PAGINA_REAL
        assert apify._argenprop_usable_html(_item(_PAGINA_REAL)) is not None

    def test_un_item_sin_html_no_rompe(self):
        assert apify._argenprop_usable_html({'crawl': {'httpStatusCode': 200}}) is None

    def test_un_item_sin_crawl_se_juzga_por_el_cuerpo(self):
        """Items viejos del dataset pueden no traer `crawl`."""
        assert apify._argenprop_usable_html({'html': _PAGINA_REAL}) == _PAGINA_REAL
        assert apify._argenprop_usable_html({'html': _DESAFIO_HTML}) is None


class TestElFallbackDirecto:
    """El actor bloqueado no puede ser el final del camino: httpx con UA de
    browser trae la página real (medido: 673 KB, 6 propiedades)."""

    @pytest.fixture(autouse=True)
    def _sin_actor(self, monkeypatch):
        """El actor siempre contesta con el challenge."""
        async def _run(self, source, actor, input_data):
            return [_item(_DESAFIO_HTML, status=405)]

        monkeypatch.setattr(apify.ApifyService, '_run_actor', _run)

    def _filters(self) -> ScrapingFilters:
        return ScrapingFilters(
            zona='Miralagos, La Plata', tipo_operacion='venta',
            tipos_propiedad=['casa'], barrio_portal_refs={'argenprop': 'miralagos'},
        )

    async def test_el_fallback_rescata_la_busqueda(self, monkeypatch):
        pedidos: list[str] = []

        async def _directo(url: str) -> str | None:
            pedidos.append(url)
            return _PAGINA_REAL

        monkeypatch.setattr(apify, '_argenprop_html_directo', _directo)
        svc = apify.ApifyService('tok')
        out = await svc._scrape_argenprop(self._filters(), _noop)
        assert out, 'el fallback tenía que traer propiedades'
        assert pedidos and 'miralagos' in pedidos[0]

    async def test_el_actor_es_el_ULTIMO_recurso(self, monkeypatch):
        """El orden está invertido a propósito (ver
        `TestDirectoPrimeroElActorDeUltima`): el actor cuesta plata y come
        captcha 10/10, así que sólo corre si el directo no trajo nada. Acá el
        directo SÍ trae, y el actor no tiene que ejecutarse."""
        corrio_actor = False

        async def _directo(url: str) -> str | None:
            return _PAGINA_REAL

        async def _run(self, source, actor, input_data):
            nonlocal corrio_actor
            corrio_actor = True
            return [_item(_PAGINA_REAL)]

        monkeypatch.setattr(apify, '_argenprop_html_directo', _directo)
        monkeypatch.setattr(apify.ApifyService, '_run_actor', _run)
        svc = apify.ApifyService('tok')
        assert await svc._scrape_argenprop(self._filters(), _noop)
        assert corrio_actor is False

    async def test_si_el_fallback_tambien_falla_no_explota(self, monkeypatch):
        """Una búsqueda de siete portales no puede morir porque uno bloquee.
        Devolver vacío es correcto acá — lo que NO puede pasar es hacerlo en
        silencio, y de eso se encarga el log."""
        async def _directo(url: str) -> str | None:
            return None

        monkeypatch.setattr(apify, '_argenprop_html_directo', _directo)
        svc = apify.ApifyService('tok')
        assert await svc._scrape_argenprop(self._filters(), _noop) == []

    async def test_el_bloqueo_se_loguea_fuerte(self, monkeypatch, caplog):
        """El corazón del bug: 0 propiedades era indistinguible de un listado
        vacío de verdad. Tiene que quedar dicho."""
        async def _directo(url: str) -> str | None:
            return None

        monkeypatch.setattr(apify, '_argenprop_html_directo', _directo)
        svc = apify.ApifyService('tok')
        with caplog.at_level('WARNING'):
            await svc._scrape_argenprop(self._filters(), _noop)
        assert any('bloque' in r.message.lower() or 'blocked' in r.message.lower()
                   for r in caplog.records), caplog.text


class TestElFetchDirecto:
    async def test_manda_un_user_agent_de_browser(self, monkeypatch):
        """Es la diferencia medida entre 3 KB de captcha y 673 KB de listado."""
        visto: dict = {}

        class _Resp:
            status_code = 200
            text = _PAGINA_REAL

            def raise_for_status(self) -> None:
                return None

        class _Client:
            def __init__(self, *a, **kw) -> None:
                visto.update(kw)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def get(self, url, **kw):
                return _Resp()

        monkeypatch.setattr(httpx, 'AsyncClient', _Client)
        assert await apify._argenprop_html_directo('https://www.argenprop.com/x')
        ua = str((visto.get('headers') or {}).get('User-Agent', ''))
        assert 'Mozilla' in ua and 'Chrome' in ua

    async def test_un_desafio_directo_devuelve_None(self, monkeypatch):
        """Si la IP de salida también está quemada, no hay rescate — y decir
        "no hay propiedades" sería la misma mentira de antes."""
        class _Resp:
            status_code = 200
            text = _DESAFIO_HTML

            def raise_for_status(self) -> None:
                return None

        class _Client:
            def __init__(self, *a, **kw) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def get(self, url, **kw):
                return _Resp()

        monkeypatch.setattr(httpx, 'AsyncClient', _Client)
        assert await apify._argenprop_html_directo('https://www.argenprop.com/x') is None

    async def test_un_error_de_red_devuelve_None(self, monkeypatch):
        class _Client:
            def __init__(self, *a, **kw) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def get(self, url, **kw):
                raise RuntimeError('timeout')

        monkeypatch.setattr(httpx, 'AsyncClient', _Client)
        assert await apify._argenprop_html_directo('https://www.argenprop.com/x') is None


async def _noop(*_a, **_kw) -> None:
    return None


class TestDirectoPrimeroElActorDeUltima:
    """El actor sale bloqueado 10/10 SIEMPRE, así que pagarlo primero es tirar
    plata: cada búsqueda gastaba ~US$0.0066 en un run que nunca sirvió y
    después hacía el fetch directo igual.

    Invertido, el orden barato-primero es el mismo que ZonaProp ya usa
    (`ZONAPROP_USE_APIFY=False` por defecto, con el actor de marcha atrás), y
    por el mismo motivo: el camino directo mide mejor Y sale gratis. El actor
    queda alcanzable por env porque el bloqueo depende de la IP de salida — en
    otro deploy puede ser al revés.
    """

    def _filters(self) -> ScrapingFilters:
        return ScrapingFilters(
            zona='Miralagos, La Plata', tipo_operacion='venta',
            tipos_propiedad=['casa'], barrio_portal_refs={'argenprop': 'miralagos'},
        )

    async def test_si_el_directo_anda_no_se_paga_el_actor(self, monkeypatch):
        corrio_actor = False

        async def _run(self, source, actor, input_data):
            nonlocal corrio_actor
            corrio_actor = True
            return [_item(_DESAFIO_HTML, status=405)]

        async def _directo(url: str) -> str | None:
            return _PAGINA_REAL

        monkeypatch.setattr(apify.ApifyService, '_run_actor', _run)
        monkeypatch.setattr(apify, '_argenprop_html_directo', _directo)
        out = await apify.ApifyService('tok')._scrape_argenprop(self._filters(), _noop)
        assert out, 'el directo tenía que traer propiedades'
        assert corrio_actor is False, 'no había que pagar el actor'

    async def test_si_el_directo_falla_escala_al_actor(self, monkeypatch):
        """El fallback tiene que existir de verdad: desde una IP de datacenter
        el directo es lo PRIMERO que se quema."""
        async def _run(self, source, actor, input_data):
            return [_item(_PAGINA_REAL)]

        async def _directo(url: str) -> str | None:
            return None

        monkeypatch.setattr(apify.ApifyService, '_run_actor', _run)
        monkeypatch.setattr(apify, '_argenprop_html_directo', _directo)
        assert await apify.ApifyService('tok')._scrape_argenprop(self._filters(), _noop)

    async def test_si_fallan_los_dos_no_explota_y_avisa(self, monkeypatch, caplog):
        async def _run(self, source, actor, input_data):
            return [_item(_DESAFIO_HTML, status=405)]

        async def _directo(url: str) -> str | None:
            return None

        monkeypatch.setattr(apify.ApifyService, '_run_actor', _run)
        monkeypatch.setattr(apify, '_argenprop_html_directo', _directo)
        with caplog.at_level('WARNING'):
            assert await apify.ApifyService('tok')._scrape_argenprop(
                self._filters(), _noop) == []
        assert any('bloque' in r.message.lower() for r in caplog.records), caplog.text

    async def test_el_flag_devuelve_el_actor_al_frente(self, monkeypatch):
        """`ARGENPROP_USE_APIFY=true` = marcha atrás, igual que ZonaProp."""
        from app.core.config import settings

        orden: list[str] = []

        async def _run(self, source, actor, input_data):
            orden.append('actor')
            return [_item(_PAGINA_REAL)]

        async def _directo(url: str) -> str | None:
            orden.append('directo')
            return _PAGINA_REAL

        monkeypatch.setattr(settings, 'ARGENPROP_USE_APIFY', True, raising=False)
        monkeypatch.setattr(apify.ApifyService, '_run_actor', _run)
        monkeypatch.setattr(apify, '_argenprop_html_directo', _directo)
        await apify.ApifyService('tok')._scrape_argenprop(self._filters(), _noop)
        assert orden[0] == 'actor'

    async def test_el_directo_pide_TODAS_las_paginas(self, monkeypatch):
        """El actor traía las 10 en un run. El directo tiene que cubrir lo
        mismo, o invertir el orden recorta la búsqueda en silencio."""
        pedidas: list[str] = []

        async def _directo(url: str) -> str | None:
            pedidas.append(url)
            return _PAGINA_REAL

        monkeypatch.setattr(apify, '_argenprop_html_directo', _directo)
        await apify.ApifyService('tok')._scrape_argenprop(self._filters(), _noop)
        assert len(pedidas) > 1, f'sólo pidió {pedidas}'
        assert any('pagina-2' in u for u in pedidas), pedidas
