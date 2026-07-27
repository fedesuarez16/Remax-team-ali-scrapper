from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from anthropic import AsyncAnthropic

from app.core.config import settings

logger = logging.getLogger(__name__)

_ML_API_BASE = 'https://api.mercadolibre.com'
# permalink → item id: .../MLA-1234567890-titulo... → MLA1234567890
_ML_ID_RE = re.compile(r'(ML[A-Z])-?(\d+)')

# Browser-like headers: ZonaProp serves the full listing HTML to these (its
# gallery lives in an embedded JSON blob) but 403s bare clients.
_BROWSER_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/120 Safari/537.36'
    ),
    'Accept-Language': 'es-AR,es;q=0.9',
}
# ZonaProp embeds its gallery as `'pictures': [ {..} ]` (single OR double quoted
# key). Each picture object carries several resolutions; we take the largest.
_ZP_PICTURES_KEY = re.compile(r"""["']pictures["']\s*:\s*\[""")
_ZP_RES_KEYS = (
    'resizeUrl1200x1200', 'url1200x1200',
    'resizeUrl720x532', 'url730x532', 'url360x266',
)

MODEL = 'claude-haiku-4-5-20251001'
_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

# Structured specs already shown as boxes on the ficha — the LLM must NOT
# re-emit these as destacados, they come from dedicated columns.
_KNOWN_SPECS = (
    'precio', 'ambientes', 'm2', 'metros', 'superficie', 'baños', 'banos',
    'cocheras', 'piso', 'antigüedad', 'antiguedad', 'expensas',
)

_ENRICH_TOOL = {
    'name': 'extraer_ficha',
    'description': (
        'Extrae comodidades y características destacadas del texto libre de la '
        'descripción de una propiedad inmobiliaria argentina, para armar una ficha.'
    ),
    'input_schema': {
        'type': 'object',
        'properties': {
            'amenities': {
                'type': 'array',
                'items': {'type': 'string'},
                'description': (
                    'Comodidades y servicios del edificio o la unidad '
                    '(ej: Pileta, Parrilla, SUM, Gimnasio, Seguridad 24hs, Laundry, '
                    'Baulera, Solárium). Nombres cortos y capitalizados, sin duplicados.'
                ),
            },
            'destacados': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'label': {
                            'type': 'string',
                            'description': 'Etiqueta corta (ej: Orientación, Estado, Disposición, Luminosidad, Apto crédito).',
                        },
                        'value': {
                            'type': 'string',
                            'description': 'Valor corto (ej: Norte, A estrenar, Frente, Muy luminoso, Sí).',
                        },
                    },
                    'required': ['label', 'value'],
                },
                'description': (
                    'Características puntuales con etiqueta+valor que NO sean amenities '
                    'ni datos numéricos ya conocidos (precio, ambientes, m2, baños, '
                    'cocheras, piso, antigüedad, expensas).'
                ),
            },
        },
        'required': ['amenities', 'destacados'],
    },
}

_SYSTEM_PROMPT = (
    'Sos un analista inmobiliario. Recibís la descripción en texto libre de una '
    'propiedad y extraés información estructurada para armar una ficha.\n'
    '- amenities: comodidades/servicios (pileta, parrilla, SUM, gimnasio, seguridad, '
    'laundry, baulera, etc.). Nombres cortos, capitalizados, sin duplicados.\n'
    '- destacados: características puntuales con etiqueta+valor (orientación, estado, '
    'disposición frente/contrafrente, luminosidad, apto crédito, apto profesional, etc.).\n'
    'NO inventes datos que no estén en el texto. NO repitas en destacados datos '
    'numéricos que ya vienen aparte (precio, ambientes, m², baños, cocheras, piso, '
    'antigüedad, expensas). Si no hay información, devolvé listas vacías.'
)


def _merge_amenities(existing: list[str], extracted: list[str]) -> list[str]:
    """Append extracted amenities that aren't already present (case-insensitive)."""
    seen = {a.strip().lower() for a in existing}
    merged = list(existing)
    for a in extracted:
        a = a.strip()
        if a and a.lower() not in seen:
            seen.add(a.lower())
            merged.append(a)
    return merged


def _clean_destacados(raw: list[dict]) -> list[dict[str, str]]:
    """Keep only well-formed label/value pairs that aren't a known structured spec."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for d in raw:
        label = str(d.get('label') or '').strip()
        value = str(d.get('value') or '').strip()
        if not label or not value:
            continue
        key = label.lower()
        if key in seen or any(spec in key for spec in _KNOWN_SPECS):
            continue
        seen.add(key)
        out.append({'label': label, 'value': value})
    return out


def _merge_images(existing: list[str], extra: list[str]) -> list[str]:
    """Union preserving order, existing first, deduped (case-insensitive)."""
    seen: set[str] = set()
    out: list[str] = []
    for img in [*existing, *extra]:
        if not isinstance(img, str) or not img.strip():
            continue
        key = img.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(img.strip())
    return out


async def _mercadolibre_gallery(url_origen: str) -> list[str]:
    """Full photo gallery from MercadoLibre's official item API (no scraping).

    The search feed only carries the thumbnail; the complete ``pictures[]`` array
    lives at ``/items/{id}``. The item id is parsed from the permalink.
    """
    m = _ML_ID_RE.search(url_origen or '')
    if not m:
        return []
    item_id = f'{m.group(1)}{m.group(2)}'
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f'{_ML_API_BASE}/items/{item_id}',
                params={'attributes': 'pictures'},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning('ML gallery fetch failed for %s: %s', item_id, exc)
        return []
    return [
        p.get('secure_url') or p.get('url', '')
        for p in (data.get('pictures') or [])
        if isinstance(p, dict) and (p.get('secure_url') or p.get('url'))
    ]


def _parse_zonaprop_pictures(html: str) -> list[str]:
    """Extract the full gallery from ZonaProp's embedded `pictures` JSON array.

    The search feed (Apify) returns only a handful of photos; the detail page
    HTML carries the complete gallery in a JS object literal. The `pictures` key
    may be single- or double-quoted, so we locate it, balance-scan the array,
    JSON-parse it, and pick the largest resolution per picture (deduped).
    """
    m = _ZP_PICTURES_KEY.search(html)
    if not m:
        return []
    start = html.index('[', m.start())
    depth = 0
    end = -1
    for j in range(start, len(html)):
        ch = html[j]
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                end = j
                break
    if end < 0:
        return []
    try:
        pics = json.loads(html[start:end + 1])
    except Exception:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for p in pics:
        if not isinstance(p, dict):
            continue
        url = next((p[k] for k in _ZP_RES_KEYS if p.get(k)), None)
        if not url:
            continue
        base = url.split('?')[0]
        if base not in seen:
            seen.add(base)
            out.append(base)
    return out


async def _zonaprop_gallery(url_origen: str) -> list[str]:
    """Full ZonaProp gallery from the detail-page HTML (no browser, no Apify).

    ZonaProp 403s bare clients but serves complete HTML to browser-like headers;
    the gallery is embedded as JSON. A stale listing may 410 — returns [] then.
    """
    if not url_origen:
        return []
    try:
        async with httpx.AsyncClient(
            timeout=20, headers=_BROWSER_HEADERS, follow_redirects=True
        ) as client:
            resp = await client.get(url_origen)
            if resp.status_code != 200:
                return []
            return _parse_zonaprop_pictures(resp.text)
    except Exception as exc:
        logger.warning('ZonaProp gallery fetch failed for %s: %s', url_origen, exc)
        return []


async def _fetch_full_gallery(prop: dict[str, Any]) -> list[str]:
    """Recover the complete photo gallery from the original listing.

    Source-aware and verified against real data:
    - zonaprop → parse the detail page's embedded gallery JSON (biggest gap:
      the feed stores ~2 photos while listings carry 15-34).
    - mercadolibre → official item API (best-effort; ML now gates its API).
    - googlemaps → url_origen is the agency-site ficha of a single property,
      so its harvested images belong to it unambiguously.
    - argenprop/remax → generic Playwright harvest of the listing's own ficha
      page (same reasoning as googlemaps: one property per URL).
    Instagram keeps the images captured at scrape time: re-harvesting pulls
    unrelated posts. Returns [] on any failure so the caller keeps the
    search-time images.
    """
    fuente = (prop.get('fuente') or '').lower()
    url_origen = prop.get('url_origen') or ''
    if fuente == 'zonaprop':
        return await _zonaprop_gallery(url_origen)
    if fuente == 'mercadolibre':
        return await _mercadolibre_gallery(url_origen)
    if fuente in ('googlemaps', 'argenprop', 'remax') and url_origen.startswith('http'):
        from app.services.apify import harvest_page_images
        galleries = await harvest_page_images([url_origen])
        return galleries.get(url_origen, [])
    return []


async def _enrich_gallery(prop: dict[str, Any], sb: Any) -> None:
    """Merge the full original-listing gallery into ``imagenes`` and persist.

    Runs once per property at ficha-prep time (few selected properties, not the
    whole search feed). Mutates ``prop['imagenes']`` in place.
    """
    existing = prop.get('imagenes') or []
    full = await _fetch_full_gallery(prop)
    merged = _merge_images(existing, full)
    if merged == existing:
        return
    prop['imagenes'] = merged
    if sb is not None and prop.get('id'):
        try:
            await sb.table('properties').update(
                {'imagenes': merged}
            ).eq('id', prop['id']).execute()
        except Exception as exc:
            logger.warning('gallery persist failed for %s: %s', prop.get('id'), exc)


async def enrich_ficha(prop: dict[str, Any], sb: Any) -> dict[str, Any]:
    """Parse a property's free-text description into amenities + destacados via LLM.

    Idempotent: if already enriched, returns the property unchanged. Otherwise runs
    the LLM, merges amenities, stores destacados, persists, and marks it enriched.
    A too-short description is marked enriched with empty destacados so it isn't retried.

    Also recovers the full original-listing gallery (search feeds only carry a
    partial set — MercadoLibre only the thumbnail) before the text enrichment gate.
    """
    if not prop.get('ficha_enriched'):
        await _enrich_gallery(prop, sb)

    if prop.get('ficha_enriched'):
        return prop

    descripcion = (prop.get('descripcion') or '').strip()
    if len(descripcion) < 40:
        amenities = prop.get('amenities') or []
        await _persist(sb, prop.get('id'), amenities, [])
        prop['destacados'] = []
        prop['ficha_enriched'] = True
        return prop

    titulo = prop.get('titulo') or ''
    try:
        msg = await _client.messages.create(  # type: ignore[call-overload]
            model=MODEL,
            max_tokens=768,
            system=_SYSTEM_PROMPT,
            tools=[_ENRICH_TOOL],  # type: ignore[list-item]
            tool_choice={'type': 'tool', 'name': 'extraer_ficha'},
            messages=[{'role': 'user', 'content': f'{titulo}\n\n{descripcion}'[:6000]}],
        )
    except Exception as exc:
        logger.warning('enrich_ficha LLM call failed for %s: %s', prop.get('id'), exc)
        return prop  # leave un-enriched; the ficha still renders the raw description

    tool_use = next((b for b in msg.content if b.type == 'tool_use'), None)
    if not tool_use:
        return prop

    data: dict[str, Any] = tool_use.input  # type: ignore[assignment]
    amenities = _merge_amenities(prop.get('amenities') or [], data.get('amenities') or [])
    destacados = _clean_destacados(data.get('destacados') or [])

    await _persist(sb, prop.get('id'), amenities, destacados)
    prop['amenities'] = amenities
    prop['destacados'] = destacados
    prop['ficha_enriched'] = True
    return prop


async def _persist(sb: Any, property_id: str | None, amenities: list[str], destacados: list[dict]) -> None:
    if sb is None or not property_id:
        return
    try:
        await sb.table('properties').update({
            'amenities': amenities,
            'destacados': destacados,
            'ficha_enriched': True,
        }).eq('id', property_id).execute()
    except Exception as exc:
        logger.warning('enrich_ficha persist failed for %s: %s', property_id, exc)
