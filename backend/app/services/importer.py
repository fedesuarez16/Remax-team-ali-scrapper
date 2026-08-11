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
from app.services.apify import _extract_images_from_html, harvest_page_images
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


async def _fetch_page(url: str) -> tuple[str, list[str]]:
    """Fetch the ficha and return (visible text, server-HTML gallery)."""
    from bs4 import BeautifulSoup  # type: ignore[import-untyped]

    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; PropSearchBot/1.0)',
        'Accept-Language': 'es-AR,es;q=0.9',
    }
    async with httpx.AsyncClient(headers=headers, timeout=20, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        html = resp.text

    imgs = _extract_images_from_html(html, url)
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
        tag.decompose()
    text = re.sub(r'\n{3,}', '\n\n', soup.get_text(separator='\n')).strip()
    return text[:8000], imgs


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
        imagenes=images[:20],
        fuente='manual',
        url_origen=url,
        confianza_extraccion=min(1.0, filled / 6),
    )
    res = await sb.table('properties').insert(prop.model_dump()).execute()
    if not res.data:
        raise RuntimeError('No se pudo guardar la propiedad')
    return {'property': res.data[0], 'created': True}
