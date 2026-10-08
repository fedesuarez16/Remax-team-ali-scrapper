"""InmoBúsqueda decide el muro "No soy bot" por la IP de salida, nada más.

Medido 2026-10-07 contra el pool `groups-RESIDENTIAL,country-AR` de Apify: 7 de
cada 8 IPs comen el muro — ficha y listado por igual — y un segundo GET con las
cookies del muro, desde la misma IP, lo vuelve a comer. La IP que pasa, en
cambio, pasa para todo. Una IP residencial argentina limpia pasa siempre.

Con un solo intento por búsqueda (chat) y tres por ficha, producción fallaba
casi siempre. La respuesta es rotar sesiones —el muro pesa 7 KB— y RECORDAR la
que pasó, para que la página siguiente y la próxima ficha no vuelvan a tirar
los dados.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services import apify, importer

_WALL = '<html><head><title>No soy bot - InmoBusqueda</title></head><body>' \
        '<form action="nosoybot.do.php"></form></body></html>'
_PAGE = '<html><head><title>Oficina en Venta - InmoBusqueda</title></head><body>ok</body></html>'


class _Pool:
    """Proxy falso: sólo las sesiones de `good` salen por una IP que pasa."""

    def __init__(self, good: set[int] | None = None) -> None:
        self.good = good or set()
        self.sessions: list[str | None] = []

    async def __call__(self, url: str, params: object, session: str | None) -> SimpleNamespace:
        self.sessions.append(session)
        ok = session is not None and int(session.removeprefix('ib')) in self.good
        return SimpleNamespace(text=_PAGE if ok else _WALL, status_code=200)


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch: pytest.MonkeyPatch) -> None:
    counter = iter(range(1000))
    monkeypatch.setattr(apify, '_next_proxy_session', lambda prefix='zp': f'ib{next(counter)}')
    monkeypatch.setattr(apify, '_inmobusqueda_sticky_session', None)
    monkeypatch.setattr(settings, 'SCRAPER_PROXY_URL', 'http://u:p@proxy.apify.com:8000')


async def test_rotates_exit_ips_until_one_is_not_walled(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _Pool(good={7})
    monkeypatch.setattr(apify, '_inmobusqueda_attempt', pool)

    resp = await apify.inmobusqueda_get('https://www.inmobusqueda.com.ar/ficha-1')

    assert resp.text == _PAGE
    assert apify._inmobusqueda_sticky_session == 'ib7'


async def test_exit_ips_are_tried_in_parallel_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    """~3 s por IP en serie llevaba la ficha a 45 s; en tandas, a un par de RTT."""
    in_flight = {'now': 0, 'max': 0}

    async def slow_wall(url: str, params: object, session: str | None) -> SimpleNamespace:
        in_flight['now'] += 1
        in_flight['max'] = max(in_flight['max'], in_flight['now'])
        await asyncio.sleep(0)
        in_flight['now'] -= 1
        return SimpleNamespace(text=_WALL, status_code=200)

    monkeypatch.setattr(apify, '_inmobusqueda_attempt', slow_wall)

    with pytest.raises(apify.InmoBusquedaBlocked):
        await apify.inmobusqueda_get('https://www.inmobusqueda.com.ar/a.html')

    assert in_flight['max'] == apify._INMOBUSQUEDA_PARALLEL_EXITS


async def test_the_ip_that_passed_is_reused_by_the_next_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _Pool(good={2})
    monkeypatch.setattr(apify, '_inmobusqueda_attempt', pool)

    await apify.inmobusqueda_get('https://www.inmobusqueda.com.ar/a.html')
    pool.sessions.clear()
    await apify.inmobusqueda_get('https://www.inmobusqueda.com.ar/a-pagina-2.html')

    assert pool.sessions == ['ib2']


async def test_a_sticky_ip_that_gets_walled_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _Pool(good={0})
    monkeypatch.setattr(apify, '_inmobusqueda_attempt', pool)
    await apify.inmobusqueda_get('https://www.inmobusqueda.com.ar/a.html')

    pool.good = {6}
    pool.sessions.clear()
    resp = await apify.inmobusqueda_get('https://www.inmobusqueda.com.ar/b.html')

    assert resp.text == _PAGE
    assert pool.sessions[0] == 'ib0'
    assert apify._inmobusqueda_sticky_session == 'ib6'


async def test_gives_up_after_a_bounded_number_of_exit_ips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _Pool()
    monkeypatch.setattr(apify, '_inmobusqueda_attempt', pool)

    with pytest.raises(apify.InmoBusquedaBlocked):
        await apify.inmobusqueda_get('https://www.inmobusqueda.com.ar/a.html')

    assert len(pool.sessions) == apify._INMOBUSQUEDA_EXIT_IP_ATTEMPTS
    assert apify._inmobusqueda_sticky_session is None


async def test_without_a_proxy_there_is_one_direct_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, 'SCRAPER_PROXY_URL', '')
    pool = _Pool()
    monkeypatch.setattr(apify, '_inmobusqueda_attempt', pool)

    with pytest.raises(apify.InmoBusquedaBlocked):
        await apify.inmobusqueda_get('https://www.inmobusqueda.com.ar/a.html')

    assert pool.sessions == [None]


# ── Ficha Propio ──────────────────────────────────────────────────────────────

_FICHA_HTML = (
    '<html><body><h1>Oficina en venta en La Plata</h1>'
    '<p>' + 'Dos niveles, sala de reunión, cocina. ' * 100 + '</p></body></html>'
)


async def test_an_inmobusqueda_ficha_goes_through_the_exit_ip_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def get(url: str, **_kw: object) -> SimpleNamespace:
        return SimpleNamespace(text=_FICHA_HTML, status_code=200)

    async def never(*_a: object, **_kw: object) -> str:
        raise AssertionError('no debería escalar')

    monkeypatch.setattr(importer, 'inmobusqueda_get', get)
    monkeypatch.setattr(importer, '_fetch_html_httpx', never)
    monkeypatch.setattr(importer, 'render_page_html', never)
    monkeypatch.setattr(importer, 'fetch_page_html_via_actor', never)

    html = await importer._fetch_html('https://www.inmobusqueda.com.ar/fib-oficina-310389.html')

    assert html == _FICHA_HTML


async def test_a_walled_inmobusqueda_ficha_still_tries_the_server_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def walled(url: str, **_kw: object) -> SimpleNamespace:
        raise apify.InmoBusquedaBlocked('muro')

    calls: list[bool] = []

    async def direct(url: str, *, use_proxy: bool = True) -> str:
        calls.append(use_proxy)
        return _FICHA_HTML

    monkeypatch.setattr(importer, 'inmobusqueda_get', walled)
    monkeypatch.setattr(importer, '_fetch_html_httpx', direct)

    html = await importer._fetch_html('https://www.inmobusqueda.com.ar/fib-oficina-310389.html')

    assert html == _FICHA_HTML
    assert calls == [False]
