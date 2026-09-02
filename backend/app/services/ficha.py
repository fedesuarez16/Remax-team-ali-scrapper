from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

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

_MOBILE_UA = (
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 '
    '(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
)
# MercadoLibre sirve DOS markups distintos para la misma URL y sólo el de
# celular trae la galería entera. Relevado en vivo sobre 7 avisos de inmuebles:
# con UA de escritorio siempre 5 fotos (y el alt diciendo "Imagen 1 de 28"),
# con UA de iPhone las 28. El VIP nuevo de inmuebles arma un mosaico de 5 y pide
# el resto por JS al abrir el visor; el carrusel mobile ya viene entero en el
# HTML servido. Verificado con Playwright que ni el click en "28 fotos", ni el
# scroll, ni las flechas disparan el XHR que falta — headless no hidrata esa
# parte. O sea: el arreglo es un header, no más browser ni más Apify.
_MOBILE_HEADERS = {**_BROWSER_HEADERS, 'User-Agent': _MOBILE_UA}
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


async def _mercadolibre_gallery(url_origen: str, allow_escalation: bool = True) -> list[str]:
    """Full photo gallery from the listing page HTML.

    NOT the official API: `api.mercadolibre.com/items/{id}` answers **403** even
    with a real application token (client_credentials returns HTTP 200 and scope
    `read`, and the call still comes back
    `PA_UNAUTHORIZED_RESULT_FROM_POLICIES` / `blocked_by: PolicyAgent`) — ML
    closed its public catalogue to third-party apps and the DevCenter offers no
    catalogue permission to tick. Every MercadoLibre ficha was left with the
    single feed thumbnail while the listing carried the whole gallery.

    The listing page itself still serves 200 to browser-like headers, which is
    the same route (and same escalation ladder) `_zonaprop_gallery` takes.

    Se pide con `_MOBILE_HEADERS`: la misma URL con UA de escritorio devuelve
    sólo las 5 fotos del mosaico, con UA de celular la galería completa. Ver el
    comentario de `_MOBILE_HEADERS`.
    """
    return await _gallery_via_ladder(
        url_origen, _parse_mercadolibre_pictures,
        headers=_MOBILE_HEADERS, allow_escalation=allow_escalation,
    )


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


async def _fetch_listing_html(
    url_origen: str, headers: dict[str, str] | None = None
) -> tuple[bool, str | None]:
    """Browser-headed GET of a listing page — the cheap first rung of the ladder.

    Returns ``(gone, html)``:
    - ``gone=True`` when the listing is 404/410 (taken down). The caller must NOT
      escalate: no rung resurrects a dead listing, and a headless render / paid
      Apify run per baja would be pure waste.
    - ``html`` is the page text on 200; None on a block/error (403/429/transport)
      so the caller escalates. ZonaProp/MercadoLibre 403 bare clients but serve
      the full gallery HTML to these headers.
    """
    if not url_origen:
        return False, None
    # Egress por `SCRAPER_PROXY_URL` cuando está seteado: los portales sólo
    # abren el HTML del aviso a IPs RESIDENCIALES. Mismo diagnóstico ya medido
    # para el scraper de MercadoLibre (1.98 MB desde una conexión hogareña vs
    # 39 KB de verificación desde datacenter, misma URL y mismos headers).
    # Railway es datacenter, así que sin proxy producción come el muro — y como
    # el muro llega con 200, se lee como "el aviso no tiene fotos" en vez de
    # como "nos bloquearon". Sin proxy configurado, `proxy=None` sale directo.
    from app.core.config import settings
    try:
        async with httpx.AsyncClient(
            timeout=20, headers=headers or _BROWSER_HEADERS, follow_redirects=True,
            proxy=settings.SCRAPER_PROXY_URL or None,
        ) as client:
            resp = await client.get(url_origen)
            if resp.status_code in (404, 410):
                return True, None
            return False, resp.text if resp.status_code == 200 else None
    except Exception as exc:
        logger.warning('listing fetch failed for %s: %s', url_origen, exc)
        return False, None


async def _gallery_via_ladder(
    url_origen: str,
    parser: Callable[[str], list[str]],
    headers: dict[str, str] | None = None,
    allow_escalation: bool = True,
) -> list[str]:
    """Fetch a listing's HTML and parse its gallery, escalating only on a block.

    Rung 1 — browser-headed httpx (free): the fast path that answers most opens.
    Rung 2 — headless Chromium render (free): past a transient UA/JS/WAF wall.
    Rung 3 — Apify website actor (paid, last resort): only when a genuine WAF
             block defeats both cheaper rungs. Returns None off (no token/mock),
             so cost is never incurred unless everything else came back empty.

    A 404/410 (listing gone) short-circuits to [] with NO escalation. Each rung
    reuses the SAME `parser`, so the escalation is source-agnostic.

    `headers` viaja por TODA la escalera, no sólo por el primer escalón: si el
    portal sirve markup distinto según el User-Agent (MercadoLibre), un rung 2
    que renderiza con otro UA devolvería una galería recortada y el llamador no
    tendría cómo notar la diferencia.
    """
    if not url_origen:
        return []

    gone, html = await _fetch_listing_html(url_origen, headers)
    if gone:
        return []
    if html and (imgs := parser(html)):
        return imgs

    # `allow_escalation=False` corta acá: los escalones 2 y 3 tardan segundos y
    # minutos respectivamente, así que no pueden correr dentro de un request que
    # tiene que contestar ya (la ficha pública). Ver `_recuperar_galeria`.
    if not allow_escalation:
        return []

    from app.services.apify import fetch_page_html_via_actor, render_page_html

    rendered = await render_page_html(url_origen, user_agent=(headers or {}).get('User-Agent'))
    if rendered and (imgs := parser(rendered)):
        return imgs

    via_actor = await fetch_page_html_via_actor(url_origen)
    return parser(via_actor) if via_actor else []


async def _zonaprop_gallery(url_origen: str, allow_escalation: bool = True) -> list[str]:
    """Full ZonaProp gallery from the detail page, escalating past a WAF block.

    ZonaProp serves complete HTML (gallery embedded as JSON) to browser-like
    headers, but a transient DataDome challenge can blank the cheap fetch — the
    ladder then falls through to a headless render and, last, an Apify actor.
    A stale listing simply parses to [].
    """
    return await _gallery_via_ladder(
        url_origen, _parse_zonaprop_pictures, allow_escalation=allow_escalation
    )


# Portales con parser propio, por host. La clave es un fragmento del dominio
# porque MercadoLibre reparte los avisos entre subdominios por tipo de
# propiedad (`casa.`, `departamento.`, `ph.`, `terreno.`, `articulo.`).
_PORTAL_HOSTS = ('mercadolibre', 'zonaprop', 'remax', 'century21')


async def portal_gallery_from_url(url: str, allow_escalation: bool = True) -> list[str]:
    """La galería completa de un aviso, deducida por el HOST de la URL.

    Existe porque el despacho por `fuente` no alcanza: Ficha Propio guarda todo
    con `fuente='manual'`, así que una ficha importada de MercadoLibre nunca
    llegaba a su parser y se quedaba con lo que hubiera en el HTML genérico.
    La URL, en cambio, siempre dice de qué portal salió.

    Devuelve [] para un portal sin parser propio — ahí el harvest genérico del
    llamador sigue siendo la mejor opción disponible.
    """
    host = urlparse(url or '').netloc.lower()
    if not host:
        return []
    if 'mercadolibre' in host:
        return await _mercadolibre_gallery(url, allow_escalation)
    if 'zonaprop' in host:
        return await _zonaprop_gallery(url, allow_escalation)
    if 'remax' in host:
        from app.services.apify import remax_gallery_from_url
        return await remax_gallery_from_url(url)
    if 'century21' in host:
        # El listado sirve 10 miniaturas aunque el aviso tenga 22 fotos; la
        # ficha las trae todas por la misma API.
        from app.services.apify import century21_gallery_from_url
        return await century21_gallery_from_url(url)
    return []


async def _fetch_full_gallery(
    prop: dict[str, Any], allow_escalation: bool = True
) -> list[str]:
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
        return await _zonaprop_gallery(url_origen, allow_escalation)
    if fuente == 'mercadolibre':
        return await _mercadolibre_gallery(url_origen, allow_escalation)
    if fuente in ('googlemaps', 'argenprop', 'remax') and url_origen.startswith('http'):
        from app.services.apify import harvest_page_images
        galleries = await harvest_page_images([url_origen])
        return galleries.get(url_origen, [])
    # `fuente` no identifica al portal (típicamente 'manual', que es como Ficha
    # Propio guarda TODO lo que importa). La URL sí: se despacha por host.
    if fuente not in ('instagram',) and url_origen.startswith('http'):
        return await portal_gallery_from_url(url_origen, allow_escalation)
    return []


# Debajo de esto, una ficha de un portal CON parser propio está casi seguro
# incompleta. Los dos números salen de relevar avisos reales: el mosaico de
# escritorio de MercadoLibre trae siempre 5 fotos (el aviso tenía 28) y el
# harvest genérico de ZonaProp saca 6 donde el parser propio saca 27.
_GALERIA_SOSPECHOSA = 6


def _gallery_looks_incomplete(prop: dict[str, Any]) -> bool:
    """True when a ficha never captured its real gallery.

    The search feed stores only the thumbnail (0-1 photos) while a portal listing
    holds 15-34. A ficha stuck on <=1 image got its gallery fetch swallowed by a
    transient portal failure (WAF challenge, timeout) at scrape time — worth one
    re-attempt. A healthy gallery reports False, so repeat ficha opens cost nothing.

    Caso aparte, los portales con parser propio (`_PORTAL_HOSTS`): ahí hay una
    fuente de verdad barata, así que el umbral se sube a `_GALERIA_SOSPECHOSA`.
    Existe porque las fichas guardadas antes de que el import consultara al
    parser del portal quedaron con la galería parcial Y marcadas como
    enriquecidas — el import es idempotente por `url_origen`, así que repegar el
    link devuelve la fila vieja y sin esta regla quedaban clavadas para siempre.

    El reintento se auto-limita por partida doble: la escalera sólo sube de
    escalón cuando el parser vuelve VACÍO, así que un aviso que de verdad tiene
    5 fotos se resuelve en el primer GET (gratis) y no toca el rung pago; y si
    lo que vuelve es lo mismo que ya había, `_enrich_gallery` corta antes de
    escribir en la base.

    Un portal SIN parser propio (Argenprop, la web de una inmobiliaria) se
    queda con la regla vieja: no hay contra qué comparar, y reintentar sería
    pagar harvest headless sin saber siquiera si falta algo.
    """
    imagenes = prop.get('imagenes') or []
    if len(imagenes) <= 1:
        return True
    host = urlparse(prop.get('url_origen') or '').netloc.lower()
    if not any(portal in host for portal in _PORTAL_HOSTS):
        return False
    return len(imagenes) <= _GALERIA_SOSPECHOSA


async def _enrich_gallery(prop: dict[str, Any], sb: Any) -> None:
    """Merge the full original-listing gallery into ``imagenes`` and persist.

    Runs once per property at ficha-prep time (few selected properties, not the
    whole search feed). Mutates ``prop['imagenes']`` in place.
    """
    existing = prop.get('imagenes') or []
    full = await _fetch_full_gallery(prop)
    if not full:
        return
    # A ficha stuck on the low-res feed thumbnail: the recovered gallery is the
    # authoritative full-res set and already contains that photo at a better
    # resolution, so REPLACE rather than merge — otherwise the blurry 360x266
    # thumbnail stays pinned as imagenes[0], i.e. the ficha's cover. A healthy
    # gallery still merges, so a manually-added photo is never dropped.
    merged = full if _gallery_looks_incomplete(prop) else _merge_images(existing, full)
    merged = _merge_images(merged, [])
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
