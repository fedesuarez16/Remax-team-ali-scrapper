"""A blocked page gets one more try from a different exit IP.

Live run, a search across every portal: ZonaProp answered `403 Forbidden`
twice and contributed ZERO while the other sources delivered 424 properties.
The block is per exit IP, so the same request from a fresh proxy session
usually goes through — which is exactly what the Apify actor did for us before.
"""
import json
from typing import Any

import httpx
import pytest

from app.models.property import ScrapingFilters
from app.services.apify import _scrape_zonaprop_direct

_URL = 'https://www.zonaprop.com.ar/casas-venta-city-bell-450000-500000-dolar.html'


def _ok_page(n: int = 3) -> str:
    state = {'listStore': {
        'paging': {'total': n, 'totalPages': 1, 'currentPage': 1},
        'appliedFilters': [{'type': 'location', 'options': [
            {'label': 'City Bell', 'type': 'zone', 'min': '1001379'}]}],
        'listPostings': [{
            'postingId': str(i), 'url': f'/p/{i}.html', 'title': f'Casa {i}',
            'realEstateType': {'name': 'Casas'},
            'priceOperationTypes': [{'prices': [{'amount': 460000, 'currency': 'USD'}]}],
            'postingLocation': {'address': {'name': f'Calle {i}'},
                                'location': {'locationId': 'V1-D-1001379', 'name': 'City Bell'}},
            'mainFeatures': {}, 'visiblePictures': {'pictures': []},
        } for i in range(n)],
    }}
    return ('<html><script>window.__PRELOADED_STATE__ = '
            + json.dumps(state) + ';window.x={};</script></html>')


def _filters() -> ScrapingFilters:
    return ScrapingFilters(zona='City Bell', zona_pedida='City Bell',
                           tipo_operacion='venta', tipos_propiedad=['casa'],
                           precio_min=450_000, precio_max=500_000)


async def _noop(source: str, status: str, count: int) -> None:
    return None


def _serve(monkeypatch: pytest.MonkeyPatch, statuses: list[int]) -> list[str | None]:
    """Answers each successive request with the next status; records the proxy
    each client was built with."""
    proxies: list[str | None] = []
    calls = {'n': 0}

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            proxies.append(k.get('proxy'))
        async def __aenter__(self) -> '_Client': return self
        async def __aexit__(self, *a: Any) -> None: return None

        async def get(self, url: str, *a: Any, **k: Any) -> httpx.Response:
            i = calls['n']
            calls['n'] += 1
            code = statuses[i] if i < len(statuses) else 200
            body = _ok_page() if code == 200 else '<html>denegado</html>'
            return httpx.Response(code, text=body, request=httpx.Request('GET', url))

    monkeypatch.setattr(httpx, 'AsyncClient', _Client)
    return proxies


@pytest.fixture(autouse=True)
def _proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    monkeypatch.setattr(
        settings, 'SCRAPER_PROXY_URL',
        'http://groups-RESIDENTIAL:secret@proxy.apify.com:8000')


async def test_a_403_is_retried_from_a_new_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxies = _serve(monkeypatch, [403, 200])

    results = await _scrape_zonaprop_direct(_filters(), _noop)

    assert len(results) == 3
    assert len(proxies) == 2
    assert proxies[0] != proxies[1]
    assert 'session-' in (proxies[1] or '')


async def test_it_gives_up_after_the_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two burnt IPs in a row is a wall, not bad luck — do not spin."""
    proxies = _serve(monkeypatch, [403, 403, 200])

    results = await _scrape_zonaprop_direct(_filters(), _noop)

    assert results == []
    assert len(proxies) == 2


async def test_a_clean_first_try_is_not_repeated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxies = _serve(monkeypatch, [200])

    results = await _scrape_zonaprop_direct(_filters(), _noop)

    assert len(results) == 3
    assert len(proxies) == 1


async def test_every_attempt_pins_its_own_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even the first one: reusing whatever session the proxy last handed out
    is how an already-burnt IP gets inherited."""
    proxies = _serve(monkeypatch, [200])

    await _scrape_zonaprop_direct(_filters(), _noop)

    assert 'session-' in (proxies[0] or '')


class TestTransientProxyFailures:
    """Measured live: page 1 of 27 came back fine (`total_portal=798`) and page
    2 died with `590 UPSTREAM504` — an Apify PROXY error, not a WAF block. It
    was outside the 403/429 retry set, so 26 pages were abandoned over one
    hiccup. Anything transient earns the same second chance."""

    async def test_a_proxy_upstream_error_is_retried(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        proxies = _serve(monkeypatch, [590, 200])

        results = await _scrape_zonaprop_direct(_filters(), _noop)

        assert len(results) == 3
        assert len(proxies) == 2

    @pytest.mark.parametrize('code', [500, 502, 503, 504, 590])
    async def test_every_server_side_failure_gets_a_second_chance(
        self, monkeypatch: pytest.MonkeyPatch, code: int,
    ) -> None:
        _serve(monkeypatch, [code, 200])

        assert len(await _scrape_zonaprop_direct(_filters(), _noop)) == 3

    async def test_a_404_is_not_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unknown slug is an answer, not a hiccup — paying twice for it is
        waste."""
        proxies = _serve(monkeypatch, [404, 200])

        results = await _scrape_zonaprop_direct(_filters(), _noop)

        assert results == []
        assert len(proxies) == 1

    async def test_a_network_error_is_retried(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = {'n': 0}

        class _Client:
            def __init__(self, *a: Any, **k: Any) -> None: pass
            async def __aenter__(self) -> '_Client': return self
            async def __aexit__(self, *a: Any) -> None: return None

            async def get(self, url: str, *a: Any, **k: Any) -> httpx.Response:
                calls['n'] += 1
                if calls['n'] == 1:
                    raise httpx.ReadTimeout('timeout', request=httpx.Request('GET', url))
                return httpx.Response(200, text=_ok_page(),
                                      request=httpx.Request('GET', url))

        monkeypatch.setattr(httpx, 'AsyncClient', _Client)

        assert len(await _scrape_zonaprop_direct(_filters(), _noop)) == 3


class TestSessionStickiness:
    """Rotating on every request was self-inflicted: live, nearly every FIRST
    attempt drew a 403 and the retry then succeeded — the pattern of a fresh
    exit IP being challenged and a warmed one being let through. The Apify
    actor used one session per run (`zp_71397`).

    So: keep the session that works, rotate only when it stops working.
    """

    async def test_pages_reuse_the_session_that_worked(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        proxies = _serve(monkeypatch, [200, 200, 200])
        f = _filters()

        await _scrape_zonaprop_direct(f, _noop)

        # One page here, but the client must be built with a stable session.
        assert 'session-' in (proxies[0] or '')

    async def test_a_block_rotates_but_success_sticks(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """403 → new session → works → the NEXT page keeps that session."""
        pages: dict[str, str] = {}
        proxies: list[str | None] = []
        seq: list[int] = [403, 200, 200]
        calls = {'n': 0}

        class _Client:
            def __init__(self, *a: Any, **k: Any) -> None:
                proxies.append(k.get('proxy'))
            async def __aenter__(self) -> '_Client': return self
            async def __aexit__(self, *a: Any) -> None: return None

            async def get(self, url: str, *a: Any, **k: Any) -> httpx.Response:
                i = calls['n']
                calls['n'] += 1
                code = seq[i] if i < len(seq) else 200
                two = json.loads(_ok_page(3).split('= ', 1)[1].rsplit(';window', 1)[0])
                two['listStore']['paging']['totalPages'] = 2
                body = ('<html><script>window.__PRELOADED_STATE__ = '
                        + json.dumps(two) + ';window.x={};</script></html>')
                return httpx.Response(code, text=body if code == 200 else 'no',
                                      request=httpx.Request('GET', url))

        monkeypatch.setattr(httpx, 'AsyncClient', _Client)
        pages.clear()

        await _scrape_zonaprop_direct(_filters(), _noop)

        assert len(proxies) == 3          # blocked, retry, then page 2
        assert proxies[0] != proxies[1]   # the block rotated the session
        assert proxies[1] == proxies[2]   # the working one was kept
