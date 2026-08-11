from __future__ import annotations

import re
import unicodedata
from typing import Any

from anthropic import AsyncAnthropic

from app.core.config import settings
from app.models.property import MatchCriteria, ScoreBreakdown
from app.services.llm_costs import SCOPE_MATCH_PARSE, record_llm_usage

MODEL = 'claude-haiku-4-5-20251001'
_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

_EXTRACT_MATCH_CRITERIA_TOOL = {
    'name': 'extract_match_criteria',
    'description': (
        'Extrae criterios estructurados de coincidencia inmobiliaria a partir de una búsqueda '
        'en lenguaje natural para el mercado argentino. Identificá zonas (pueden ser varias), '
        'tipos de propiedad aceptados, presupuesto y amenities distinguiendo obligatorios de deseables.'
    ),
    'input_schema': {
        'type': 'object',
        'properties': {
            'zonas': {
                'type': 'array',
                'items': {'type': 'string'},
                'description': 'Lista de barrios/zonas mencionadas, ej: ["Palermo", "Villa Crespo"]',
            },
            'tipos_propiedad': {
                'type': 'array',
                'items': {
                    'type': 'string',
                    'enum': ['departamento', 'casa', 'ph', 'local', 'oficina', 'terreno', 'otro'],
                },
            },
            'precio_min': {'type': ['number', 'null'], 'description': 'Precio mínimo en USD'},
            'precio_max': {'type': ['number', 'null'], 'description': 'Precio máximo en USD'},
            'moneda': {'type': 'string', 'enum': ['USD', 'ARS'], 'default': 'USD'},
            'ambientes_min': {
                'type': ['integer', 'null'],
                'description': 'Mínimo de ambientes o dormitorios',
            },
            'amenities_required': {
                'type': 'array',
                'items': {'type': 'string'},
                'description': 'Amenities obligatorios (el usuario los pide como necesarios)',
            },
            'amenities_desired': {
                'type': 'array',
                'items': {'type': 'string'},
                'description': 'Amenities deseables pero no obligatorios ("en lo posible", "idealmente", "si tiene")',
            },
        },
        'required': ['zonas'],
    },
}

_SYSTEM_PROMPT = (
    'Sos un parser de criterios de búsqueda inmobiliaria para el mercado argentino. '
    'Extraés zonas (pueden ser varias), tipos de propiedad, presupuesto y amenities. '
    'Los amenities marcados como "en lo posible", "idealmente", "si se puede" van en amenities_desired. '
    'Los mencionados sin condición van en amenities_required. '
    'Siempre llamá a extract_match_criteria.'
)

# Keyword expansions for amenity matching (normalized, no accents)
_AMENITY_KEYWORDS: dict[str, list[str]] = {
    'parque': ['parque', 'jardin', 'verde', 'espacio verde', 'parque privado', 'jardin privado', 'patio'],
    'cochera': ['cochera', 'garage', 'garaje'],
    'pileta': ['pileta', 'piscina', 'pool'],
    'balcon': ['balcon', 'terraza'],
    'quincho': ['quincho', 'barbacoa', 'parrilla'],
    'seguridad': ['seguridad', 'vigilancia', 'portero'],
}


# Property-type keyword variants for the deterministic fallback parser
_TIPO_KEYWORDS: dict[str, list[str]] = {
    'departamento': ['departamento', 'departamentos', 'depto', 'deptos', 'dpto', 'monoambiente'],
    'casa': ['casa', 'casas', 'chalet'],
    'ph': ['ph'],
    'local': ['local', 'locales', 'fondo de comercio', 'fabrica', 'galpon'],
    'oficina': ['oficina', 'oficinas'],
    'terreno': ['terreno', 'terrenos', 'lote', 'lotes'],
}

# Tokens that are never part of a zone name
_STOPWORDS: set[str] = {
    'en', 'de', 'la', 'el', 'los', 'las', 'con', 'sin', 'para', 'un', 'una', 'unos', 'unas',
    'hasta', 'desde', 'menos', 'mas', 'por', 'y', 'o', 'a', 'al', 'del', 'que', 'busco',
    'quiero', 'cerca', 'zona', 'barrio', 'ambiente', 'ambientes', 'amb', 'dormitorio',
    'dormitorios', 'usd', 'ars', 'dolares', 'pesos', 'millones', 'millon', 'mil', 'maximo',
    'minimo', 'venta', 'alquiler', 'comprar', 'alquilar',
}


def _norm(text: str) -> str:
    nfkd = unicodedata.normalize('NFKD', text.lower())
    return ''.join(c for c in nfkd if not unicodedata.combining(c))


def _zone_matches(zone: str, direccion_norm: str | None) -> bool:
    if not direccion_norm:
        return False
    zone_n = _norm(zone)
    dir_n = _norm(direccion_norm)
    # Full zone string first, then individual tokens (>=4 chars to avoid false positives)
    tokens = [zone_n] + [t for t in zone_n.split() if len(t) >= 4]
    return any(t in dir_n for t in tokens)


def _amenity_matches(wanted: str, amenities: list[str]) -> bool:
    wanted_n = _norm(wanted)
    amenities_blob = ' '.join(_norm(a) for a in amenities)

    if wanted_n in amenities_blob:
        return True

    for key, expansions in _AMENITY_KEYWORDS.items():
        if wanted_n in [_norm(k) for k in [key] + expansions]:
            return any(_norm(exp) in amenities_blob for exp in expansions)

    return False


def _score_property(prop: dict, criteria: MatchCriteria) -> tuple[int, ScoreBreakdown, list[str]]:
    reasons: list[str] = []
    bd = ScoreBreakdown()

    # Zone (30 pts)
    if criteria.zonas:
        addr = prop.get('direccion_norm') or prop.get('direccion', '')
        for zone in criteria.zonas:
            if _zone_matches(zone, addr):
                bd.zone = 30
                reasons.append(f'Zona: {zone}')
                break

    # Tipo propiedad (25 pts)
    if criteria.tipos_propiedad:
        if prop.get('tipo_propiedad') in criteria.tipos_propiedad:
            bd.tipo_propiedad = 25
            reasons.append(prop['tipo_propiedad'].capitalize())

    # Ambientes (20 pts)
    if criteria.ambientes_min is not None:
        amb = prop.get('ambientes')
        if amb and amb >= criteria.ambientes_min:
            bd.ambientes = 20
            reasons.append(f'{amb} ambiente{"s" if amb > 1 else ""}')

    # Precio en rango (15 pts)
    precio = prop.get('precio')
    if precio and prop.get('moneda') == criteria.moneda:
        pmin, pmax = criteria.precio_min, criteria.precio_max
        in_range = (
            (pmin is None or precio >= pmin)
            and (pmax is None or precio <= pmax)
        )
        if in_range and (pmin is not None or pmax is not None):
            bd.precio = 15
            reasons.append(f'USD {int(precio):,} (en rango)')

    # Amenities required (15 pts total, split)
    amenities = prop.get('amenities') or []
    if criteria.amenities_required:
        pts_each = 15 / len(criteria.amenities_required)
        for amenity in criteria.amenities_required:
            if _amenity_matches(amenity, amenities):
                bd.amenities_required += round(pts_each)
                reasons.append(f'Tiene {amenity}')

    # Amenities desired (5 pts total, split)
    if criteria.amenities_desired:
        pts_each = 5 / len(criteria.amenities_desired)
        for amenity in criteria.amenities_desired:
            if _amenity_matches(amenity, amenities):
                bd.amenities_desired += round(pts_each)
                reasons.append(f'Tiene {amenity}')

    total = (
        bd.zone + bd.tipo_propiedad + bd.ambientes
        + bd.precio + bd.amenities_required + bd.amenities_desired
    )
    return min(100, total), bd, reasons


def _parse_criteria_heuristic(query: str) -> MatchCriteria:
    """LLM-free criteria parser. Covers the common shape of AR real-estate queries
    (tipo + zona + presupuesto + ambientes + amenities) so ranking still works when
    the Anthropic API is unavailable. Less nuanced than the LLM, but deterministic."""
    q = _norm(query)
    tokens = re.findall(r'[a-z0-9]+', q)

    tipos = [tipo for tipo, kws in _TIPO_KEYWORDS.items() if any(_norm(k) in q for k in kws)]

    amb_min = None
    m = re.search(r'(\d+)\s*(?:amb|ambiente|dormitorio)', q)
    if m:
        amb_min = int(m.group(1))

    precio_max = None
    pm = re.search(r'(?:hasta|menos de|max(?:imo)?)\s*(?:usd\s*)?([\d.]+)\s*(k|mil|millones?|m)?', q)
    if pm:
        val = float(pm.group(1).replace('.', ''))
        unit = pm.group(2)
        if unit in ('k', 'mil'):
            val *= 1_000
        elif unit in ('m', 'millon', 'millones'):
            val *= 1_000_000
        precio_max = val

    amenities = [
        key for key, expansions in _AMENITY_KEYWORDS.items()
        if any(_norm(exp) in q for exp in [key] + expansions)
    ]

    tipo_words = {_norm(k) for kws in _TIPO_KEYWORDS.values() for k in kws}
    zone_tokens = [t for t in tokens if t not in _STOPWORDS and t not in tipo_words and not t.isdigit()]
    zonas = [' '.join(zone_tokens)] if zone_tokens else []

    return MatchCriteria(
        zonas=zonas,
        tipos_propiedad=tipos,
        precio_max=precio_max,
        ambientes_min=amb_min,
        amenities_required=amenities,
    )


async def _parse_criteria(
    query: str, *, sb: Any = None, job_id: str | None = None,
) -> MatchCriteria:
    """Parse a natural-language query into structured criteria, falling back to the
    heuristic parser on any LLM trouble.

    `sb`/`job_id` are the ledger hooks. They are optional because the heuristic
    fallback means this function must keep working with no infrastructure at all —
    but when a caller has them, the call gets booked. The billed call is recorded
    even when tool-use parsing fails and the heuristic takes over.
    """
    try:
        msg = await _client.messages.create(
            model=MODEL,
            max_tokens=512,
            system=_SYSTEM_PROMPT,
            tools=[_EXTRACT_MATCH_CRITERIA_TOOL],  # type: ignore[list-item]
            tool_choice={'type': 'tool', 'name': 'extract_match_criteria'},
            messages=[{'role': 'user', 'content': query}],
        )
        await record_llm_usage(
            sb,
            scope=SCOPE_MATCH_PARSE,
            model=MODEL,
            usage=getattr(msg, 'usage', None),
            job_id=job_id,
        )
        tool_use = next((b for b in msg.content if b.type == 'tool_use'), None)
        if tool_use is not None:
            return MatchCriteria(**tool_use.input)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning('LLM criteria parse failed (%s) — using heuristic parser', exc)

    return _parse_criteria_heuristic(query)


async def rank_properties(
    query: str, properties: list[dict], *, sb: Any = None, job_id: str | None = None,
) -> list[dict]:
    """Score and sort an already-fetched set of properties against a query.

    Unlike ``match_properties``, this does not touch the DB or filter anything —
    it ranks the exact list passed in. Each property dict is mutated in place with
    ``match_score`` (0-100) and ``match_reasons``, then sorted by score descending.

    ``sb``/``job_id`` only feed the token ledger: pass them when the ranking runs
    as part of a search, so the criteria-parse call is attributed to that job.
    """
    criteria = await _parse_criteria(query, sb=sb, job_id=job_id)
    for prop in properties:
        score, _breakdown, reasons = _score_property(prop, criteria)
        prop['match_score'] = score
        prop['match_reasons'] = reasons
    properties.sort(key=lambda p: p.get('match_score', 0), reverse=True)
    return properties


async def match_properties(query: str, supabase: Any) -> dict[str, Any]:
    # No job_id: POST /properties/match searches the whole CRM, it is not part of
    # any scraping job.
    criteria = await _parse_criteria(query, sb=supabase)

    q = supabase.table('properties').select('*').eq('tipo_operacion', 'venta').limit(500)

    if criteria.moneda:
        q = q.eq('moneda', criteria.moneda)

    # Pre-filter price with 25% tolerance to reduce data transfer
    if criteria.precio_min is not None and criteria.precio_max is not None:
        q = q.gte('precio', criteria.precio_min * 0.75).lte('precio', criteria.precio_max * 1.25)
    elif criteria.precio_max is not None:
        q = q.lte('precio', criteria.precio_max * 1.25)
    elif criteria.precio_min is not None:
        q = q.gte('precio', criteria.precio_min * 0.75)

    result = await q.execute()
    props: list[dict] = result.data or []

    scored = []
    for prop in props:
        score, breakdown, reasons = _score_property(prop, criteria)
        if score > 0:
            scored.append({
                'property': prop,
                'score': score,
                'breakdown': breakdown.model_dump(),
                'match_reasons': reasons,
            })

    scored.sort(key=lambda x: x['score'], reverse=True)

    return {
        'criteria': criteria.model_dump(),
        'results': scored,
        'total': len(scored),
    }
