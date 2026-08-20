from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from anthropic import AsyncAnthropic

from app.core.config import settings
from app.services.llm_costs import SCOPE_FICHA_ENRICH, SCOPE_FICHA_PROPIO, record_llm_usage

logger = logging.getLogger(__name__)

# Browser-like headers: ZonaProp serves the full listing HTML to these (its
# gallery lives in an embedded JSON blob) but 403s bare clients. MercadoLibre's
# listing page needs the same treatment now that its API is closed.
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


# Una foto del aviso: `D_NQ_NP[_2X]_<id>-MLA<n>_<fecha>` + sufijo de tamaño.
# El `NP_` es lo que la separa del chrome del sitio: la misma página trae
# `D_NQ_871042-MLA96631608403_102025-OO.webp`, que no es de la propiedad.
_ML_PIC_RE = re.compile(
    r'https://http2\.mlstatic\.com/(D_NQ_NP_(?:2X_)?[0-9A-Za-z]+-MLA\d+_\d+)-[0-9A-Za-z-]+\.(?:jpg|webp)'
)


def _parse_mercadolibre_pictures(html: str) -> list[str]:
    """Las fotos del aviso, normalizadas al original `-O.jpg`.

    Cada foto aparece varias veces con distinto sufijo de tamaño (`-F-null.webp`
    en la galería, `-E.webp` en el thumbnail del feed, `-V.webp` en el visor):
    todas cuelgan de la misma base, así que se deduplica por base y se pide
    `-O.jpg`, que es el original (200 image/jpeg, verificado en vivo) y el
    formato que ya usa el resto del repo.
    """
    out: list[str] = []
    seen: set[str] = set()
    for m in _ML_PIC_RE.finditer(html or ''):
        base = m.group(1)
        if base in seen:
            continue
        seen.add(base)
        out.append(f'https://http2.mlstatic.com/{base}-O.jpg')
    return out


async def _mercadolibre_gallery(url_origen: str) -> list[str]:
    """Full photo gallery from the listing page HTML.

    NOT the official API: `api.mercadolibre.com/items/{id}` answers **403** even
    with a real application token (client_credentials returns HTTP 200 and scope
    `read`, and the call still comes back
    `PA_UNAUTHORIZED_RESULT_FROM_POLICIES` / `blocked_by: PolicyAgent`) — ML
    closed its public catalogue to third-party apps and the DevCenter offers no
    catalogue permission to tick. Every MercadoLibre ficha was left with the
    single feed thumbnail while the listing carried the whole gallery.

    The listing page itself still serves 200 to browser-like headers, which is
    the same route `_zonaprop_gallery` already takes.
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
            return _parse_mercadolibre_pictures(resp.text)
    except Exception as exc:
        logger.warning('ML gallery fetch failed for %s: %s', url_origen, exc)
        return []


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
    - mercadolibre → parse the listing page HTML. Its API is closed (403 to
      everything, even with a real app token), so this is the only route left.
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


def _gallery_looks_incomplete(prop: dict[str, Any]) -> bool:
    """True when a ficha never captured its real gallery.

    The search feed stores only the thumbnail (0-1 photos) while a portal listing
    holds 15-34. A ficha stuck on <=1 image got its gallery fetch swallowed by a
    transient portal failure (WAF challenge, timeout) at scrape time — worth one
    re-attempt. A healthy gallery reports False, so repeat ficha opens cost nothing.
    """
    return len(prop.get('imagenes') or []) <= 1


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
    # Gallery recovery is deliberately decoupled from the text-enrichment gate:
    # `ficha_enriched` is set by the text pass, so a ficha whose gallery came back
    # empty at scrape time would otherwise stay locked on the lone feed thumbnail
    # forever. Re-attempt whenever the stored gallery is still incomplete — a
    # healthy gallery skips the fetch, so repeat opens cost nothing.
    if not prop.get('ficha_enriched') or _gallery_looks_incomplete(prop):
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

    # El scope decide si este gasto entra en el contador de Ficha Propio. El mismo
    # enrich corre para fichas de portal, así que scopearlo por `fuente` es lo que
    # evita que el contador infle con gasto que no es de Ficha Propio.
    await record_llm_usage(
        sb,
        scope=SCOPE_FICHA_PROPIO if prop.get('fuente') == 'manual' else SCOPE_FICHA_ENRICH,
        model=MODEL,
        usage=getattr(msg, 'usage', None),
        property_id=prop.get('id'),
        url=prop.get('url_origen'),
    )

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
