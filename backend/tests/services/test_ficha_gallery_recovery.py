"""Gallery recovery is decoupled from the text-enrichment gate.

A ficha whose gallery fetch was swallowed by a transient portal failure (WAF
challenge, timeout) at scrape time gets locked on the lone search-feed thumbnail:
`ficha_enriched` is set by the *text* pass, so the old `if not ficha_enriched`
guard meant the gallery was never re-fetched. `enrich_ficha` now re-attempts the
gallery whenever the stored one is still incomplete (<=1 image), independently of
that text gate — and skips the fetch entirely for a healthy gallery, so repeat
ficha opens cost nothing. This pins that behaviour without hitting the network.
"""
from __future__ import annotations

import app.services.apify as apify
import app.services.ficha as ficha
from app.services.ficha import _gallery_looks_incomplete, _gallery_via_ladder, enrich_ficha

# Parser stub: comma-separated HTML → image list. Empty/None HTML → no images.
def _parse(html):
    return [p for p in (html or '').split(',') if p]


def test_gallery_looks_incomplete_only_for_zero_or_one_image() -> None:
    assert _gallery_looks_incomplete({'imagenes': []}) is True
    assert _gallery_looks_incomplete({'imagenes': ['a']}) is True
    assert _gallery_looks_incomplete({}) is True
    assert _gallery_looks_incomplete({'imagenes': ['a', 'b']}) is False


async def test_enriched_ficha_stuck_on_thumbnail_recovers_gallery(monkeypatch) -> None:
    full = [f'https://cdn/{i}.jpg' for i in range(19)]

    async def fake_fetch(prop):
        return full

    monkeypatch.setattr(ficha, '_fetch_full_gallery', fake_fetch)

    # Already text-enriched, but carrying only the feed thumbnail.
    prop = {
        'id': None,  # sb is None below, so nothing persists — pure in-memory check
        'fuente': 'zonaprop',
        'ficha_enriched': True,
        'imagenes': ['https://cdn/thumb.jpg?isFirstImage=true'],
    }

    out = await enrich_ficha(prop, sb=None)

    # The recovered gallery is authoritative and full-res: the low-res feed
    # thumbnail is dropped so it never stays pinned as the cover image.
    assert out['imagenes'] == full
    assert not any('thumb' in u for u in out['imagenes'])
    assert len(out['imagenes']) == 19


async def test_healthy_gallery_is_never_refetched(monkeypatch) -> None:
    calls = {'n': 0}

    async def fake_fetch(prop):
        calls['n'] += 1
        return ['x']

    monkeypatch.setattr(ficha, '_fetch_full_gallery', fake_fetch)

    prop = {
        'id': None,
        'fuente': 'zonaprop',
        'ficha_enriched': True,
        'imagenes': ['a', 'b', 'c'],
    }

    await enrich_ficha(prop, sb=None)

    assert calls['n'] == 0


# ── Escalation ladder: httpx → headless render → Apify actor, only on empty ──

async def test_ladder_stops_at_httpx_when_it_yields_images(monkeypatch) -> None:
    calls = {'render': 0, 'actor': 0}

    async def fetch_html(url, headers=None):
        return False, 'a,b,c'  # (gone, html)

    async def render(url, user_agent=None):
        calls['render'] += 1
        return 'x'

    async def actor(url):
        calls['actor'] += 1
        return 'y'

    monkeypatch.setattr(ficha, '_fetch_listing_html', fetch_html)
    monkeypatch.setattr(apify, 'render_page_html', render)
    monkeypatch.setattr(apify, 'fetch_page_html_via_actor', actor)

    assert await _gallery_via_ladder('http://x', _parse) == ['a', 'b', 'c']
    # Cheap rung answered — no headless render, and crucially no paid Apify run.
    assert calls == {'render': 0, 'actor': 0}


async def test_ladder_escalates_to_render_when_httpx_blocked(monkeypatch) -> None:
    calls = {'actor': 0}

    async def fetch_html(url, headers=None):
        return False, None  # blocked (403/429), not gone → escalate

    async def render(url, user_agent=None):
        return 'r1,r2'

    async def actor(url):
        calls['actor'] += 1
        return 'y'

    monkeypatch.setattr(ficha, '_fetch_listing_html', fetch_html)
    monkeypatch.setattr(apify, 'render_page_html', render)
    monkeypatch.setattr(apify, 'fetch_page_html_via_actor', actor)

    assert await _gallery_via_ladder('http://x', _parse) == ['r1', 'r2']
    assert calls['actor'] == 0  # render succeeded → still no paid run


async def test_ladder_falls_through_to_apify_only_as_last_resort(monkeypatch) -> None:
    async def fetch_html(url, headers=None):
        return False, None

    async def render(url, user_agent=None):
        return None  # even headless Chromium got challenged

    async def actor(url):
        return 'p1,p2,p3'

    monkeypatch.setattr(ficha, '_fetch_listing_html', fetch_html)
    monkeypatch.setattr(apify, 'render_page_html', render)
    monkeypatch.setattr(apify, 'fetch_page_html_via_actor', actor)

    assert await _gallery_via_ladder('http://x', _parse) == ['p1', 'p2', 'p3']


async def test_ladder_does_not_escalate_on_gone_listing(monkeypatch) -> None:
    calls = {'render': 0, 'actor': 0}

    async def fetch_html(url, headers=None):
        return True, None  # 404/410 — the listing was taken down

    async def render(url, user_agent=None):
        calls['render'] += 1
        return 'r'

    async def actor(url):
        calls['actor'] += 1
        return 'p'

    monkeypatch.setattr(ficha, '_fetch_listing_html', fetch_html)
    monkeypatch.setattr(apify, 'render_page_html', render)
    monkeypatch.setattr(apify, 'fetch_page_html_via_actor', actor)

    # A dead listing must NOT burn a headless render or a paid Apify run.
    assert await _gallery_via_ladder('http://x', _parse) == []
    assert calls == {'render': 0, 'actor': 0}


async def test_ladder_returns_empty_when_every_rung_fails(monkeypatch) -> None:
    async def fetch_html(url, headers=None):
        return False, None

    async def none(url, user_agent=None):
        return None

    monkeypatch.setattr(ficha, '_fetch_listing_html', fetch_html)
    monkeypatch.setattr(apify, 'render_page_html', none)
    monkeypatch.setattr(apify, 'fetch_page_html_via_actor', none)

    assert await _gallery_via_ladder('http://x', _parse) == []


# ── Fichas viejas clavadas en una galería parcial ────────────────────────────
#
# Relevado en vivo sobre una ficha real de ZonaProp (55714384, "Casa Hotel La
# Cumbre"): el parser propio saca 27 fotos, el harvest genérico 6. Una ficha
# importada antes de que el import consultara al parser del portal quedó con
# esas 6 y NO se cura sola: el import es idempotente por `url_origen` (repegar
# el link devuelve la fila guardada) y el gate de recuperación sólo miraba
# `<= 1 imagen`.
#
# El gate se abre para los portales que TIENEN parser propio, porque sólo ahí
# hay una fuente de verdad barata contra la cual comparar. La escalera no
# escala salvo que el parser vuelva vacío, así que un aviso que de verdad tiene
# 5 fotos cuesta un GET gratis y ninguna escritura.

_ZP_URL = 'https://www.zonaprop.com.ar/propiedades/clasificado/x-55714384.html'


def test_zonaprop_stuck_on_the_generic_harvest_is_retried() -> None:
    prop = {'url_origen': _ZP_URL, 'imagenes': [f'z{i}.jpg' for i in range(6)]}
    assert _gallery_looks_incomplete(prop) is True


def test_a_healthy_zonaprop_gallery_is_left_alone() -> None:
    prop = {'url_origen': _ZP_URL, 'imagenes': [f'z{i}.jpg' for i in range(27)]}
    assert _gallery_looks_incomplete(prop) is False


def test_remax_stuck_on_the_og_image_is_retried() -> None:
    """RE/MAX es una SPA: sin su API la ficha nace con la sola foto del og."""
    prop = {'url_origen': 'https://www.remax.com.ar/listings/casa-1', 'imagenes': ['og.jpg']}
    assert _gallery_looks_incomplete(prop) is True


def test_a_portal_without_its_own_parser_is_not_retried() -> None:
    """Argenprop o la web de una inmobiliaria no tienen fuente de verdad barata:
    reintentar sería pagar harvest headless sin saber si falta algo."""
    prop = {
        'url_origen': 'https://www.argenprop.com/ph--9044111',
        'imagenes': [f'a{i}.jpg' for i in range(5)],
    }
    assert _gallery_looks_incomplete(prop) is False


# ── Egress: el escalón barato también necesita salir por proxy ───────────────
#
# REGRESIÓN MEDIDA CONTRA PRODUCCIÓN: la misma ficha de ZonaProp que en local
# se completa a 20 fotos en 0.34s, en Railway devolvía 5 y no se curaba nunca.
# Railway es un DATACENTER y los portales le sirven un muro (mismo diagnóstico
# ya documentado en `apify._scrape_mercadolibre`: 1.98 MB desde una conexión
# hogareña, 39 KB de verificación desde datacenter, con la MISMA URL y los
# MISMOS headers).
#
# El síntoma anterior — la ficha colgada >90s — era el mismo problema: rung 1
# volvía vacío por el muro y la escalera subía a Chromium y al actor de Apify.
#
# `SCRAPER_PROXY_URL` ya existe y ya lo usa el scraper de MercadoLibre. Faltaba
# usarlo acá.

def test_the_cheap_rung_egresses_through_the_residential_proxy(monkeypatch) -> None:
    import asyncio

    import httpx as _httpx

    from app.core import config as _config

    visto: dict = {}

    class _Resp:
        status_code = 200
        text = '<html></html>'

    class _Client:
        def __init__(self, *a, **kw) -> None:
            visto['proxy'] = kw.get('proxy')
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def get(self, url, *a, **kw): return _Resp()

    monkeypatch.setattr(_httpx, 'AsyncClient', _Client)
    monkeypatch.setattr(_config.settings, 'SCRAPER_PROXY_URL', 'http://user:pw@proxy.apify.com:8000')

    asyncio.get_event_loop_policy()
    asyncio.run(ficha._fetch_listing_html('https://www.zonaprop.com.ar/x.html'))
    assert visto['proxy'] == 'http://user:pw@proxy.apify.com:8000'


def test_without_a_configured_proxy_it_goes_out_direct(monkeypatch) -> None:
    """En local no hay proxy y no debe hacer falta: `proxy=None` es salida directa."""
    import asyncio

    import httpx as _httpx

    from app.core import config as _config

    visto: dict = {}

    class _Resp:
        status_code = 200
        text = '<html></html>'

    class _Client:
        def __init__(self, *a, **kw) -> None:
            visto['proxy'] = kw.get('proxy')
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def get(self, url, *a, **kw): return _Resp()

    monkeypatch.setattr(_httpx, 'AsyncClient', _Client)
    monkeypatch.setattr(_config.settings, 'SCRAPER_PROXY_URL', '')

    asyncio.run(ficha._fetch_listing_html('https://www.zonaprop.com.ar/x.html'))
    assert visto['proxy'] is None
