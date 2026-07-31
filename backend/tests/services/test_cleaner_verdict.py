"""Test-first for the BOT LIMPIADOR's single-URL verdict — `app.services.cleaner`.

The bot visits every scraped `url_origen` and decides whether the listing is
still live. The critical safety property is that the verdict is TERNARY, never
boolean:

- `alive`   → the ficha is still published
- `dead`    → the listing is provably gone (404/410, soft-404 body, redirect to
              home, "publicación finalizada", "ya no está publicado", vendida…)
- `unknown` → we could NOT tell (timeout, 429, 403, 5xx, network error)

Only `dead` deletes. A portal that throttles or blocks us must NEVER be able to
wipe the database — that's the whole reason `unknown` exists as its own state.
"""
from __future__ import annotations

import httpx

from app.services import cleaner


class _FakeResponse:
    def __init__(self, status_code: int, text: str = '', url: str | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self.url = url or 'https://portal.com/ficha/123'


class _FakeClient:
    """Stands in for an httpx.AsyncClient built with follow_redirects=True."""

    def __init__(self, response: object | Exception) -> None:
        self._response = response
        self.calls: list[str] = []

    async def get(self, url: str) -> object:
        self.calls.append(url)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


async def _check(response: object | Exception, url: str = 'https://portal.com/ficha/123'):
    return await cleaner.check_url(url, client=_FakeClient(response))


# ── dead: HTTP status ────────────────────────────────────────────────────────


async def test_404_is_dead() -> None:
    result = await _check(_FakeResponse(404))
    assert result.verdict == 'dead'


async def test_410_gone_is_dead() -> None:
    result = await _check(_FakeResponse(410))
    assert result.verdict == 'dead'


async def test_dead_verdict_carries_a_human_readable_reason() -> None:
    result = await _check(_FakeResponse(404))
    assert result.reason
    assert '404' in result.reason


# ── unknown: everything we cannot prove ──────────────────────────────────────


async def test_429_throttled_is_unknown_not_dead() -> None:
    result = await _check(_FakeResponse(429))
    assert result.verdict == 'unknown'


async def test_403_blocked_is_unknown_not_dead() -> None:
    """Portals bot-block aggressively; a 403 says nothing about the listing."""
    result = await _check(_FakeResponse(403))
    assert result.verdict == 'unknown'


async def test_500_is_unknown_not_dead() -> None:
    result = await _check(_FakeResponse(503))
    assert result.verdict == 'unknown'


async def test_network_error_is_unknown_not_dead() -> None:
    result = await _check(httpx.ConnectError('boom'))
    assert result.verdict == 'unknown'


async def test_timeout_is_unknown_not_dead() -> None:
    result = await _check(httpx.ReadTimeout('slow'))
    assert result.verdict == 'unknown'


async def test_unexpected_exception_is_unknown_not_dead() -> None:
    result = await _check(RuntimeError('anything'))
    assert result.verdict == 'unknown'


async def test_401_is_unknown() -> None:
    result = await _check(_FakeResponse(401))
    assert result.verdict == 'unknown'


async def test_blank_url_is_unknown_and_does_not_hit_the_network() -> None:
    client = _FakeClient(_FakeResponse(200, 'x'))
    result = await cleaner.check_url('   ', client=client)
    assert result.verdict == 'unknown'
    assert client.calls == []


async def test_non_http_scheme_is_unknown() -> None:
    result = await _check(_FakeResponse(200, 'x'), url='ftp://portal.com/ficha/1')
    assert result.verdict == 'unknown'


# ── alive ────────────────────────────────────────────────────────────────────


LIVE_HTML = """
<html><head><title>Departamento 3 ambientes en City Bell</title></head>
<body><h1>Depto 3 amb</h1><p>USD 120.000 — 85 m2 — contactá al vendedor</p></body></html>
"""


async def test_live_ficha_is_alive() -> None:
    result = await _check(_FakeResponse(200, LIVE_HTML))
    assert result.verdict == 'alive'


async def test_200_with_empty_body_is_alive_not_dead() -> None:
    """No evidence of death is not evidence of death."""
    result = await _check(_FakeResponse(200, ''))
    assert result.verdict == 'alive'


async def test_the_word_vendida_inside_a_normal_description_does_not_kill_it() -> None:
    """An agency page bragging about 'propiedades vendidas' is still a live ficha
    — markers must be whole phrases that only a dead page renders."""
    html = '<html><body><p>La inmobiliaria con mas propiedades vendidas de la zona</p></body></html>'
    result = await _check(_FakeResponse(200, html))
    assert result.verdict == 'alive'


# ── dead: body markers (soft 404s served with status 200) ────────────────────


async def test_zonaprop_removed_notice_is_dead() -> None:
    html = '<html><body><h2>Este aviso ya no está publicado</h2></body></html>'
    result = await _check(_FakeResponse(200, html))
    assert result.verdict == 'dead'


async def test_mercadolibre_finished_publication_is_dead() -> None:
    html = '<html><body><p>Publicación finalizada</p></body></html>'
    result = await _check(_FakeResponse(200, html))
    assert result.verdict == 'dead'


async def test_argenprop_unavailable_property_is_dead() -> None:
    html = '<html><body><p>La propiedad que buscás ya no está disponible</p></body></html>'
    result = await _check(_FakeResponse(200, html))
    assert result.verdict == 'dead'


async def test_sold_property_is_dead() -> None:
    html = '<html><body><h1>Esta propiedad fue vendida</h1></body></html>'
    result = await _check(_FakeResponse(200, html))
    assert result.verdict == 'dead'


async def test_soft_404_page_is_dead() -> None:
    html = '<html><head><title>Error 404</title></head><body>Página no encontrada</body></html>'
    result = await _check(_FakeResponse(200, html))
    assert result.verdict == 'dead'


async def test_markers_are_accent_and_case_insensitive() -> None:
    html = '<html><body>ESTE AVISO YA NO ESTA PUBLICADO</body></html>'
    result = await _check(_FakeResponse(200, html))
    assert result.verdict == 'dead'


async def test_marker_inside_a_script_tag_is_ignored() -> None:
    """Portals ship every i18n string in their JS bundle — matching there would
    delete the entire database."""
    html = (
        '<html><head><script>var msgs={gone:"Este aviso ya no está publicado"}</script></head>'
        '<body><h1>Depto 3 ambientes</h1><p>USD 120.000</p></body></html>'
    )
    result = await _check(_FakeResponse(200, html))
    assert result.verdict == 'alive'


async def test_dead_marker_reason_names_the_matched_phrase() -> None:
    html = '<html><body><h2>Este aviso ya no está publicado</h2></body></html>'
    result = await _check(_FakeResponse(200, html))
    assert 'aviso' in result.reason.lower()


# ── dead: redirected away from the ficha ─────────────────────────────────────


async def test_redirect_to_portal_home_is_dead() -> None:
    """Zonaprop/Argenprop bounce removed fichas to the site root."""
    result = await _check(
        _FakeResponse(200, LIVE_HTML, url='https://portal.com/'),
        url='https://portal.com/propiedades/depto-city-bell-123.html',
    )
    assert result.verdict == 'dead'


async def test_redirect_within_the_site_is_not_dead() -> None:
    """A canonical-URL redirect (slug change) still lands on a ficha."""
    result = await _check(
        _FakeResponse(200, LIVE_HTML, url='https://portal.com/propiedades/depto-city-bell-123-nuevo.html'),
        url='https://portal.com/propiedades/depto-city-bell-123.html',
    )
    assert result.verdict == 'alive'


async def test_a_url_that_was_already_the_home_is_not_dead_by_redirect() -> None:
    result = await _check(
        _FakeResponse(200, LIVE_HTML, url='https://portal.com/'),
        url='https://portal.com/',
    )
    assert result.verdict == 'alive'
