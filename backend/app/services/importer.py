"""Ficha Propio — import a single property from a portal ficha URL.

Given the link to a property listing on any portal (Zonaprop, Argenprop,
MercadoLibre, a RE/MAX office site, etc.), fetch the page, extract the
property with an LLM, harvest its photo gallery and persist it to the
`properties` catalog with ``fuente='manual'`` so the app can serve a branded,
shareable ficha at ``/p/{id}`` replacing the portal link.

Idempotent per URL: a property already imported (matched by ``url_origen``)
is returned as-is without re-fetching or re-extracting.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from anthropic import AsyncAnthropic

from app.core.config import settings
from app.models.property import NormalizedProperty
from app.services.apify import (
    _extract_images_from_html,
    fetch_page_html_via_actor,
    harvest_page_images,
    render_page_html,
)
from app.services.ficha import portal_gallery_from_url
from app.services.llm_costs import SCOPE_FICHA_PROPIO, record_llm_usage
from app.services.zona import normalize_address

MODEL = 'claude-haiku-4-5-20251001'
_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

_FICHA_EXTRACT_TOOL = {
    'name': 'extract_property_ficha',
    'description': (
        'Extrae los datos de LA propiedad principal publicada en la ficha de un '
        'portal inmobiliario argentino.'
    ),
    'input_schema': {
        'type': 'object',
        'properties': {
            'encontrada': {
                'type': 'boolean',
                'description': 'false si la página no es la ficha de una propiedad',
            },
            'titulo': {'type': ['string', 'null']},
            'precio': {'type': ['number', 'null']},
            'moneda': {'type': ['string', 'null'], 'enum': ['USD', 'ARS', None]},
            'tipo_operacion': {'type': ['string', 'null'], 'enum': ['venta', 'alquiler', None]},
            'tipo_propiedad': {
                'type': ['string', 'null'],
                'enum': ['departamento', 'casa', 'ph', 'local', 'oficina', 'terreno', 'otro', None],
            },
            'ambientes': {'type': ['integer', 'null']},
            'banos': {'type': ['integer', 'null']},
            'cocheras': {'type': ['integer', 'null']},
            'piso': {'type': ['integer', 'null']},
            'expensas': {'type': ['number', 'null']},
            'amenities': {'type': ['array', 'null'], 'items': {'type': 'string'}},
            'm2': {'type': ['number', 'null']},
            'antiguedad': {'type': ['integer', 'null']},
            'direccion': {'type': ['string', 'null']},
            'descripcion': {'type': ['string', 'null']},
        },
        'required': ['encontrada'],
    },
}

_FICHA_SYSTEM_PROMPT = (
    'Sos un extractor de propiedades inmobiliarias argentinas. '
    'Recibís el texto de la ficha de UNA propiedad publicada en un portal '
    '(Zonaprop, Argenprop, MercadoLibre, Remax, la web de una inmobiliaria, etc.). '
    'Extraé los datos de esa propiedad: precio, moneda, operación, tipo, ambientes, '
    'baños, m², dirección y una descripción completa y atractiva basada en el texto. '
    'Si la página no corresponde a una propiedad puntual (es un listado, una home o '
    'una página de error), devolvé encontrada=false.'
)

# Under this many httpx-parsed images the gallery is likely JS-rendered and a
# headless pass is worth it (same threshold as apify._GALLERY_MIN_IMGS).
_MIN_GALLERY = 4

# Tope de fotos que se guardan por ficha. Era 20 y recortaba avisos reales: los
# de MercadoLibre llegan a 28 y los de RE/MAX a 37. El tope existe para que un
# portal con una galería absurda no infle la fila, no para podar avisos normales.
_MAX_IMAGENES = 40


# ── Fetch ladder: httpx → Playwright local → actor de Apify ───────────────────
#
# Argenprop (y cualquier portal detrás de AWS WAF Bot Control) devuelve 403 a un
# GET pelado de httpx. El search path ya lo sabía y corre el actor con
# `crawlerType: 'playwright:chrome'`; este path se comía el 403 y abortaba el
# import entero.
#
# El orden es puramente económico: httpx es gratis e instantáneo y alcanza para
# la mayoría de los portales (tokko, xintel, webs de inmobiliarias); Playwright
# cuesta segundos; el actor cuesta plata. Cada escalón sólo se paga cuando el
# anterior no alcanzó.
#
# La distinción que importa es bloqueo ≠ ausencia: un 403 es "el portal no nos
# deja" y se reintenta con más artillería; un 404 es "el aviso no existe" y
# escalar no lo va a revivir — sólo quema tiempo y créditos de Apify.

_BROWSER_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'es-AR,es;q=0.9,en;q=0.8',
    'Upgrade-Insecure-Requests': '1',
}

# Códigos donde el portal nos rechaza a NOSOTROS, no a la URL.
_BLOCKED_STATUS = frozenset({401, 403, 405, 429, 503})

# Un challenge de WAF vuelve con 200 y HTML válido, así que hay que mirar el
# cuerpo. Lo que NO sirve es buscar el nombre del vendor: verificado en vivo,
# argenprop.com inyecta `captcha-sdk.awswaf.com/challenge.js` en TODAS sus
# páginas, ficha real incluida. Ese detector daba falso positivo sobre páginas
# perfectamente buenas y mandaba cada import a pagar un run de Apify al pedo.
#
# La señal honesta es el TEXTO VISIBLE: un challenge pesa kilobytes de script y
# no dice nada; una ficha real trae miles de caracteres de descripción. El
# umbral es holgado a propósito — de más, escalamos y gastamos sin necesidad.
_MIN_VISIBLE_TEXT = 500


class PortalBlocked(RuntimeError):
    """El portal nos bloqueó. No dice NADA sobre si la propiedad existe."""


def _is_blocked_status(status: int) -> bool:
    return status in _BLOCKED_STATUS


def _visible_text(html: str) -> str:
    """El texto que un humano ve — sin scripts, estilos ni chrome de navegación."""
    from bs4 import BeautifulSoup  # type: ignore[import-untyped]

    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
        tag.decompose()
    return re.sub(r'\n{3,}', '\n\n', soup.get_text(separator='\n')).strip()


def _looks_blocked(html: str) -> bool:
    """True si el HTML es un challenge/interstitial en vez de la ficha."""
    return len(_visible_text(html)) < _MIN_VISIBLE_TEXT


async def _fetch_html_httpx(url: str) -> str:
    """Tier 1. Levanta ``PortalBlocked`` si el portal nos rechaza a nosotros;
    cualquier otro error HTTP (404, 500) propaga tal cual."""
    async with httpx.AsyncClient(
        headers=_BROWSER_HEADERS, timeout=20, follow_redirects=True,
    ) as client:
        resp = await client.get(url)
    if _is_blocked_status(resp.status_code):
        raise PortalBlocked(f'El portal bloqueó el pedido ({resp.status_code})')
    resp.raise_for_status()
    html = resp.text
    if _looks_blocked(html):
        raise PortalBlocked('El portal devolvió un challenge en vez de la ficha')
    return html


async def _fetch_html(url: str) -> str:
    """La ficha en HTML, escalando sólo lo necesario. Ver el bloque de arriba."""
    try:
        return await _fetch_html_httpx(url)
    except PortalBlocked:
        pass

    for fetch in (render_page_html, fetch_page_html_via_actor):
        html = await fetch(url)
        if html and not _looks_blocked(html):
            return html

    raise PortalBlocked(
        'El portal bloqueó todos los intentos (httpx, browser y Apify). '
        'La propiedad puede existir igual — reintentá más tarde.'
    )


async def _fetch_page(url: str) -> tuple[str, list[str]]:
    """Fetch the ficha and return (visible text, server-HTML gallery)."""
    html = await _fetch_html(url)
    # anchor_to_og: la ficha es UNA propiedad, así que las fotos que no comparten
    # el directorio del og:image son de otra (bloques de "similares", widgets).
    return _visible_text(html)[:8000], _extract_images_from_html(html, url, anchor_to_og=True)


async def _extract_llm(url: str, text: str) -> tuple[dict[str, Any] | None, Any]:
    """LLM-extract the single property described by ``text``.

    Returns ``(data, usage)`` — data is None when the page holds no property, but
    the usage comes back either way: Anthropic bills the call whether or not we
    found something, so the caller books it regardless.
    """
    msg = await _client.messages.create(  # type: ignore[call-overload]
        model=MODEL,
        max_tokens=1024,
        system=_FICHA_SYSTEM_PROMPT,
        tools=[_FICHA_EXTRACT_TOOL],  # type: ignore[list-item]
        tool_choice={'type': 'tool', 'name': 'extract_property_ficha'},
        messages=[{'role': 'user', 'content': f'Ficha: {url}\n\n{text}'}],
    )
    usage = getattr(msg, 'usage', None)
    tool_use = next((b for b in msg.content if b.type == 'tool_use'), None)
    if not tool_use or not tool_use.input.get('encontrada'):
        return None, usage
    return dict(tool_use.input), usage


def _normalize_tipo_propiedad(valor: str | None) -> str:
    permitidos = {'departamento', 'casa', 'ph', 'local', 'oficina', 'terreno', 'otro'}
    v = (valor or '').lower()
    return v if v in permitidos else 'otro'


async def import_property_from_url(sb: Any, url: str) -> dict[str, Any]:
    """Import one ficha URL into `properties`. Returns {'property', 'created'}.

    Raises on invalid URLs, unreachable pages or pages without a property —
    the caller reports the failure per URL without aborting the batch.
    """
    url = url.strip()
    if not re.match(r'^https?://', url):
        raise ValueError('URL inválida — tiene que empezar con http(s)://')

    existing = await (
        sb.table('properties').select('*').eq('url_origen', url).limit(1).execute()
    )
    if existing.data:
        return {'property': existing.data[0], 'created': False}

    text, images = await _fetch_page(url)
    if len(text) < 100:
        raise RuntimeError('La página no tiene contenido legible')

    data, usage = await _extract_llm(url, text)
    # Se bookea SIEMPRE, encuentre o no la propiedad: Anthropic cobra la llamada
    # igual, y un contador que sólo suma los aciertos subestima el gasto real.
    await record_llm_usage(sb, scope=SCOPE_FICHA_PROPIO, model=MODEL, usage=usage, url=url)
    if not data:
        raise RuntimeError('No se encontró una propiedad en esa página')

    # El parser propio del portal primero: sabe dónde vive la galería COMPLETA,
    # el harvest genérico sólo junta los `<img>` que haya en el HTML. RE/MAX es
    # una SPA de Angular y su HTML trae una sola foto (la del og:image), así que
    # sin esto la ficha nace con 1 de 37; MercadoLibre server-rendea 5 de 28 al
    # UA de escritorio y su parser pide el markup mobile, que las trae todas.
    # Se despacha por HOST y no por `fuente` porque acá `fuente` todavía no
    # existe — y cuando exista va a ser 'manual', que no dice de qué portal es.
    portal_gallery = await portal_gallery_from_url(url)
    if len(portal_gallery) > len(images):
        images = portal_gallery

    if len(images) < _MIN_GALLERY:
        try:
            galleries = await harvest_page_images([url])
            gallery = galleries.get(url, [])
            if len(gallery) > len(images):
                images = gallery
        except Exception:
            pass

    filled = sum(
        1 for f in ('precio', 'tipo_operacion', 'tipo_propiedad', 'ambientes', 'm2', 'direccion')
        if data.get(f) is not None
    )
    prop = NormalizedProperty(
        titulo=data.get('titulo'),
        descripcion=data.get('descripcion'),
        direccion=data.get('direccion') or '',
        direccion_norm=normalize_address(data.get('direccion') or ''),
        precio=data.get('precio'),
        moneda=data.get('moneda') or 'USD',  # type: ignore[arg-type]
        tipo_operacion=data.get('tipo_operacion') or 'venta',  # type: ignore[arg-type]
        tipo_propiedad=_normalize_tipo_propiedad(data.get('tipo_propiedad')),  # type: ignore[arg-type]
        ambientes=data.get('ambientes'),
        banos=data.get('banos'),
        cocheras=data.get('cocheras'),
        piso=data.get('piso'),
        expensas=data.get('expensas'),
        m2_total=data.get('m2'),
        antiguedad=data.get('antiguedad'),
        amenities=data.get('amenities') or [],
        imagenes=images[:_MAX_IMAGENES],
        fuente='manual',
        url_origen=url,
        confianza_extraccion=min(1.0, filled / 6),
    )
    res = await sb.table('properties').insert(prop.model_dump()).execute()
    if not res.data:
        raise RuntimeError('No se pudo guardar la propiedad')
    return {'property': res.data[0], 'created': True}
