"""Test-first for the Ficha Propio fetch ladder — httpx → Playwright → Apify.

Argenprop (y varios portales más) están detrás de AWS WAF Bot Control: un GET
pelado de httpx vuelve 403 y el import muere antes de llegar al LLM. El search
path ya resuelve esto con un browser (`_scrape_argenprop` corre el actor con
`crawlerType: 'playwright:chrome'`); el import path no lo hacía.

La escalera es una decisión de costo, no de prolijidad:

1. httpx — gratis, milisegundos, alcanza para tokko/xintel/inmobiliarias.
2. Playwright headless local — gratis pero cuesta segundos y un Chromium.
3. Actor de Apify — cuesta plata de verdad, y sólo se paga cuando los dos
   anteriores fallaron.

Por eso lo que más importa acá NO es que el happy path ande: es que un 200
nunca gaste browser ni actor, y que un 404 legítimo (aviso dado de baja) no
escale a nada — pagar un run de Apify para confirmar que una página no existe
es tirar plata.
"""
from __future__ import annotations

import httpx
import pytest

from app.services import importer


# ── Clasificación: qué cuenta como bloqueo ────────────────────────────────────

def test_headers_do_not_advertise_a_bot() -> None:
    """El UA viejo decía `PropSearchBot/1.0`: le regalábamos la firma al WAF."""
    ua = importer._BROWSER_HEADERS['User-Agent']
    assert 'bot' not in ua.lower()
    assert 'Mozilla/5.0' in ua and 'Chrome/' in ua


@pytest.mark.parametrize('status', [401, 403, 429, 503])
def test_bot_protection_statuses_are_blocked(status: int) -> None:
    assert importer._is_blocked_status(status) is True


@pytest.mark.parametrize('status', [404, 410, 500])
def test_a_dead_or_broken_listing_is_not_a_block(status: int) -> None:
    """404/410 = el aviso no existe. Escalar a browser/actor no lo va a revivir."""
    assert importer._is_blocked_status(status) is False


def test_waf_challenge_html_is_recognized_as_blocked() -> None:
    """Playwright puede devolver 200 con la página de challenge adentro.

    Sin este chequeo la escalera se corta en el tier 2 con HTML basura y el
    LLM extrae de un captcha. El challenge real es casi todo script: pesa
    kilobytes pero no tiene texto visible.
    """
    challenge = (
        '<html><head><title>Request blocked</title>'
        '<script src="https://de.captcha-sdk.awswaf.com/challenge.js"></script>'
        '<script>' + 'var _pad=1;' * 400 + '</script>'
        '</head><body><div id="challenge-container"></div></body></html>'
    )
    assert importer._looks_blocked(challenge) is True


def test_an_empty_page_is_treated_as_blocked() -> None:
    assert importer._looks_blocked('') is True
    assert importer._looks_blocked('<html><body></body></html>') is True


def test_a_real_ficha_is_not_flagged_as_blocked() -> None:
    ficha = '<html><body><h1>PH en venta en La Plata</h1>' + 'contenido ' * 500 + '</body></html>'
    assert importer._looks_blocked(ficha) is False


def test_the_waf_sdk_on_a_real_ficha_is_not_a_block() -> None:
    """Verificado en vivo contra argenprop.com: AWS WAF Bot Control inyecta su
    `challenge.js` en TODAS las páginas del sitio, no sólo en los bloqueos.

    Buscar el nombre del vendor en el HTML daba falso positivo sobre la ficha
    real y mandaba cada import a pagar un run de Apify que no hacía falta. La
    señal correcta no es quién sirve la página, es si tiene contenido.
    """
    ficha_con_waf = (
        '<html><head>'
        '<script src="https://de.captcha-sdk.awswaf.com/challenge.js"></script>'
        '</head><body><h1>Amplio PH disponible en la céntrica La Plata</h1>'
        '<p>' + 'Tres ambientes, patio, cochera. ' * 100 + '</p></body></html>'
    )
    assert importer._looks_blocked(ficha_con_waf) is False


# ── La escalera ───────────────────────────────────────────────────────────────

_FICHA_HTML = (
    '<html><body><h1>PH 3 ambientes en La Plata</h1>'
    '<p>' + 'Luminoso, patio, cochera. ' * 200 + '</p></body></html>'
)


class _Spy:
    """Cuenta invocaciones para poder afirmar que un tier NO se pagó."""

    def __init__(self, result: str | None = None, blocked: bool = False) -> None:
        self.result = result
        self.blocked = blocked
        self.calls: list[str] = []

    async def __call__(self, url: str, **_kwargs: object) -> str | None:
        self.calls.append(url)
        if self.blocked:
            raise importer.PortalBlocked('bloqueado')
        return self.result


def _patch_ladder(
    monkeypatch: pytest.MonkeyPatch,
    *,
    httpx_tier: _Spy,
    browser: _Spy,
    actor: _Spy,
) -> None:
    monkeypatch.setattr(importer, '_fetch_html_httpx', httpx_tier)
    monkeypatch.setattr(importer, 'render_page_html', browser)
    monkeypatch.setattr(importer, 'fetch_page_html_via_actor', actor)


async def test_a_plain_200_never_spends_browser_or_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El 95% de los portales no tiene WAF. Ese caso tiene que salir gratis."""
    tier1, browser, actor = _Spy(_FICHA_HTML), _Spy(_FICHA_HTML), _Spy(_FICHA_HTML)
    _patch_ladder(monkeypatch, httpx_tier=tier1, browser=browser, actor=actor)

    assert await importer._fetch_html('https://portal.com/ficha/1') == _FICHA_HTML
    assert tier1.calls == ['https://portal.com/ficha/1']
    assert browser.calls == []
    assert actor.calls == []


async def test_a_block_escalates_to_the_local_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tier1 = _Spy(blocked=True)
    browser, actor = _Spy(_FICHA_HTML), _Spy(_FICHA_HTML)
    _patch_ladder(monkeypatch, httpx_tier=tier1, browser=browser, actor=actor)

    assert await importer._fetch_html('https://www.argenprop.com/x') == _FICHA_HTML
    assert browser.calls == ['https://www.argenprop.com/x']
    assert actor.calls == []  # Playwright alcanzó: no se paga Apify


async def test_a_challenged_browser_escalates_to_apify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chromium headless por defecto también come challenge en Argenprop.

    Un 200 con el challenge adentro NO es éxito: si lo aceptáramos, el tier 3
    (el único que este portal respeta) nunca correría.
    """
    challenge = (
        '<html><head><script>' + 'var _pad=1;' * 400 + '</script></head>'
        '<body><div id="challenge-container"></div></body></html>'
    )
    tier1 = _Spy(blocked=True)
    browser, actor = _Spy(challenge), _Spy(_FICHA_HTML)
    _patch_ladder(monkeypatch, httpx_tier=tier1, browser=browser, actor=actor)

    assert await importer._fetch_html('https://www.argenprop.com/x') == _FICHA_HTML
    assert actor.calls == ['https://www.argenprop.com/x']


async def test_a_browser_that_cannot_launch_escalates_to_apify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin Chromium instalado (`render_page_html` devuelve None) seguimos vivos."""
    tier1 = _Spy(blocked=True)
    browser, actor = _Spy(None), _Spy(_FICHA_HTML)
    _patch_ladder(monkeypatch, httpx_tier=tier1, browser=browser, actor=actor)

    assert await importer._fetch_html('https://www.argenprop.com/x') == _FICHA_HTML


async def test_a_dead_listing_does_not_escalate(monkeypatch: pytest.MonkeyPatch) -> None:
    """404 propaga tal cual: ni browser ni actor. No se paga por una baja."""
    async def gone(url: str) -> str:
        raise httpx.HTTPStatusError(
            '404', request=httpx.Request('GET', url),
            response=httpx.Response(404, request=httpx.Request('GET', url)),
        )

    browser, actor = _Spy(_FICHA_HTML), _Spy(_FICHA_HTML)
    monkeypatch.setattr(importer, '_fetch_html_httpx', gone)
    monkeypatch.setattr(importer, 'render_page_html', browser)
    monkeypatch.setattr(importer, 'fetch_page_html_via_actor', actor)

    with pytest.raises(httpx.HTTPStatusError):
        await importer._fetch_html('https://portal.com/vendida')
    assert browser.calls == []
    assert actor.calls == []


async def test_every_tier_blocked_raises_a_readable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El error tiene que decir 'nos bloquearon', no 'no hay propiedad'.

    Son diagnósticos distintos: uno se reintenta, el otro no.
    """
    tier1 = _Spy(blocked=True)
    _patch_ladder(monkeypatch, httpx_tier=tier1, browser=_Spy(None), actor=_Spy(None))

    with pytest.raises(importer.PortalBlocked) as exc:
        await importer._fetch_html('https://www.argenprop.com/x')
    assert 'bloque' in str(exc.value).lower()


async def test_fetch_page_parses_whatever_tier_won(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_fetch_page` no debe saber de qué tier vino el HTML: sólo parsearlo."""
    html = (
        '<html><body><h1>PH en La Plata</h1>'
        '<img src="/foto1.jpg"><img src="/foto2.jpg">'
        '<script>basura()</script>'
        '<p>' + 'Tres ambientes con patio. ' * 200 + '</p></body></html>'
    )

    async def only_actor_wins(url: str) -> str:
        return html

    monkeypatch.setattr(importer, '_fetch_html', only_actor_wins)

    text, images = await importer._fetch_page('https://www.argenprop.com/x')
    assert 'PH en La Plata' in text
    assert 'basura()' not in text          # scripts fuera del texto del LLM
    assert len(text) <= 8000               # el recorte del prompt sigue vigente
    assert any('foto1.jpg' in i for i in images)


# ── Bloqueo probabilístico por IP: InmoBúsqueda ───────────────────────────────
#
# Verificado en vivo (2026-09-16) contra dos fichas reales de inmobusqueda.com.ar:
# el PRIMER GET volvió con el interstitial `<title>No soy bot</title>` (Cloudflare
# Turnstile + captcha de imagen) y los DIEZ siguientes, misma IP, mismos headers,
# mismas URLs, volvieron la ficha completa. 10 OK / 0 bloqueos.
#
# La conclusión importa más que el número: el muro NO depende de la URL ni de los
# headers, depende de la reputación de la IP de salida EN ESE MOMENTO. Y contra
# eso la herramienta correcta es rotar la sesión del proxy —gratis, milisegundos—
# y no escalar a un browser o, peor, pagar un actor de Apify.
#
# `_fetch_html_httpx` pide una sesión nueva en cada llamada, así que reintentar
# ya implica salir por otra IP. Lo que faltaba era, simplemente, reintentar.


class _FlakySpy:
    """httpx que bloquea las primeras `fail_times` salidas y después pasa.

    Registra `use_proxy` de cada intento: la escalera tiene que agotar el proxy
    ANTES de probar directo, no al revés.
    """

    def __init__(self, fail_times: int, result: str | None = None) -> None:
        self.fail_times = fail_times
        self.result = result
        self.calls: list[bool] = []

    async def __call__(self, url: str, *, use_proxy: bool = True) -> str:
        self.calls.append(use_proxy)
        if len(self.calls) <= self.fail_times:
            raise importer.PortalBlocked('El portal devolvió un challenge en vez de la ficha')
        if self.result is None:
            raise importer.PortalBlocked('El portal devolvió un challenge en vez de la ficha')
        return self.result


async def test_a_probabilistic_block_retries_on_a_fresh_exit_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un bloqueo aislado se resuelve con otra IP, no con un browser.

    Éste es EL caso de InmoBúsqueda. Antes se rendía en el primer challenge y
    mandaba el import a gastar Playwright y un run de Apify para nada.
    """
    tier1 = _FlakySpy(fail_times=1, result=_FICHA_HTML)
    browser, actor = _Spy(_FICHA_HTML), _Spy(_FICHA_HTML)
    monkeypatch.setattr(importer, '_fetch_html_httpx', tier1)
    monkeypatch.setattr(importer, 'render_page_html', browser)
    monkeypatch.setattr(importer, 'fetch_page_html_via_actor', actor)

    assert await importer._fetch_html('https://www.inmobusqueda.com.ar/fib-x.html') == _FICHA_HTML
    assert tier1.calls == [True, True]  # dos IPs residenciales distintas
    assert browser.calls == []
    assert actor.calls == []


async def test_the_proxied_retries_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rotar es gratis, pero no infinito: un portal caído no puede colgar el request."""
    tier1 = _FlakySpy(fail_times=99)
    browser, actor = _Spy(_FICHA_HTML), _Spy(_FICHA_HTML)
    monkeypatch.setattr(importer, '_fetch_html_httpx', tier1)
    monkeypatch.setattr(importer, 'render_page_html', browser)
    monkeypatch.setattr(importer, 'fetch_page_html_via_actor', actor)

    await importer._fetch_html('https://www.inmobusqueda.com.ar/fib-x.html')
    assert tier1.calls.count(True) == importer._HTTPX_PROXY_ATTEMPTS


async def test_a_direct_attempt_runs_before_paying_for_a_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La IP del proxy puede estar quemada y la del server limpia.

    Un GET más es gratis; el browser cuesta segundos y el actor cuesta plata.
    """
    tier1 = _FlakySpy(fail_times=importer._HTTPX_PROXY_ATTEMPTS, result=_FICHA_HTML)
    browser, actor = _Spy(_FICHA_HTML), _Spy(_FICHA_HTML)
    monkeypatch.setattr(importer, '_fetch_html_httpx', tier1)
    monkeypatch.setattr(importer, 'render_page_html', browser)
    monkeypatch.setattr(importer, 'fetch_page_html_via_actor', actor)

    assert await importer._fetch_html('https://www.inmobusqueda.com.ar/fib-x.html') == _FICHA_HTML
    assert tier1.calls[-1] is False  # el último intento salió sin proxy
    assert browser.calls == []
    assert actor.calls == []


async def test_a_dead_proxy_does_not_burn_the_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cuenta de proxy sin crédito: rotar la sesión no la va a recargar.

    Antes un `ProxyError` ni siquiera se capturaba y mataba el import entero
    sin probar browser ni actor — un fallo de NUESTRA infra reportado al
    usuario como si el portal lo hubiera bloqueado.
    """
    calls: list[bool] = []

    async def proxy_caido(url: str, *, use_proxy: bool = True) -> str:
        calls.append(use_proxy)
        if use_proxy:
            raise httpx.ProxyError('407 Proxy Authentication Required')
        return _FICHA_HTML

    avisado: list[str | None] = []

    async def chequear_limite(proxy_url: str | None) -> None:
        avisado.append(proxy_url)

    monkeypatch.setattr(importer, '_fetch_html_httpx', proxy_caido)
    monkeypatch.setattr(importer, 'check_apify_proxy_limit', chequear_limite)
    monkeypatch.setattr(importer, 'render_page_html', _Spy(None))
    monkeypatch.setattr(importer, 'fetch_page_html_via_actor', _Spy(None))

    assert await importer._fetch_html('https://portal.com/ficha/1') == _FICHA_HTML
    assert calls == [True, False]  # un intento proxied, corte, directo
    # Una cuenta sin crédito tiene que salir como "recargá el proxy", no como
    # "el portal te bloqueó": son dos acciones distintas para el usuario.
    assert avisado == [importer.settings.SCRAPER_PROXY_URL]


async def test_the_browser_goes_out_through_the_residential_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chromium lanzado pelado usa la IP de datacenter del contenedor.

    Es la IP que los portales bloquean MÁS fuerte que la residencial que
    veníamos de agotar (ver `apify._playwright_proxy`). Escalar a algo peor no
    es escalar. El path de ZonaProp ya lo hacía bien; éste no.
    """
    recibido: dict[str, object] = {}

    async def browser(url: str, **kwargs: object) -> str:
        recibido.update(kwargs)
        return _FICHA_HTML

    monkeypatch.setattr(importer.settings, 'SCRAPER_PROXY_URL', 'http://user:pass@proxy.apify.com:8000')
    monkeypatch.setattr(importer, '_fetch_html_httpx', _FlakySpy(fail_times=99))
    monkeypatch.setattr(importer, 'render_page_html', browser)
    monkeypatch.setattr(importer, 'fetch_page_html_via_actor', _Spy(None))

    await importer._fetch_html('https://www.argenprop.com/x')
    assert recibido.get('proxy') == {
        'server': 'http://proxy.apify.com:8000', 'username': 'user', 'password': 'pass',
    }


async def test_the_blocked_error_names_the_antibot_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El mensaje tiene que decir QUÉ pasó y que reintentar es lo correcto.

    Con un bloqueo por reputación de IP, reintentar es la acción acertada —
    pero sólo si el texto se lo dice al usuario en vez de dejarlo adivinando.
    """
    monkeypatch.setattr(importer, '_fetch_html_httpx', _FlakySpy(fail_times=99))
    monkeypatch.setattr(importer, 'render_page_html', _Spy(None))
    monkeypatch.setattr(importer, 'fetch_page_html_via_actor', _Spy(None))

    with pytest.raises(importer.PortalBlocked) as exc:
        await importer._fetch_html('https://www.inmobusqueda.com.ar/fib-x.html')
    mensaje = str(exc.value).lower()
    assert 'verificaci' in mensaje  # "verificación antibot"
    assert 'reintent' in mensaje
