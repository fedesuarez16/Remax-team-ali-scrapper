"""Pure zona/address normalization helpers.

Extracted from `app.graphs.extraction.nodes` (moved verbatim) so both the
extraction graph and the geocoding services (`app.services.geocode`) can
share one source of truth without a circular import — `nodes.py` lazily
imports `geocode.py`, so `geocode.py` must never import `nodes.py`.

Stdlib-only. No other project imports.
"""
from __future__ import annotations

import re
import unicodedata


def normalize_address(direccion: str) -> str:
    s = unicodedata.normalize('NFKD', direccion).encode('ascii', 'ignore').decode()
    return ' '.join(s.lower().split())


# Street-type and honorific abbreviations, expanded so the same street matches
# across portals ("Av. Santa Fe" / "Avenida Santa Fe", "Gral. Paz" / "General
# Paz"). The street TYPE itself is deliberately kept in the fingerprint: in La
# Plata "Diagonal 74" and "Calle 74" are two different streets, so dropping the
# word would merge listings that are blocks apart.
_ADDRESS_ABBREVIATIONS = {
    'av': 'avenida', 'avda': 'avenida', 'avd': 'avenida', 'ave': 'avenida',
    'bv': 'boulevard', 'blvd': 'boulevard', 'bvard': 'boulevard', 'bulevar': 'boulevard',
    'diag': 'diagonal',
    'pje': 'pasaje',
    'gral': 'general', 'grl': 'general',
    'cnel': 'coronel',
    'tte': 'teniente',
    'pres': 'presidente',
    'ing': 'ingeniero',
    'dr': 'doctor', 'dra': 'doctora',
    'sto': 'santo', 'sta': 'santa',
}

# Filler that carries no identity: "Calle 7" is just "7", and "N° 1234" is the
# altura written long-hand.
_ADDRESS_FILLER = {'calle', 'nro', 'n', 'no', 'num', 'numero', 'altura'}

# Everything from here on describes the UNIT, not the building — one portal
# publishes "Santa Fe 1234 Piso 5", another just "Santa Fe 1234".
_ADDRESS_UNIT_MARKERS = {'piso', 'depto', 'dpto', 'depa', 'departamento', 'uf', 'unidad'}


def address_fingerprint(direccion: str) -> str | None:
    """Canonical ``street number`` for matching one property across portals.

    Returns ``None`` when the address has no street number to anchor on — a
    zona-only address like "Gonnet" describes a neighbourhood, not a building,
    and collapsing on it would merge unrelated listings. Callers fall back to
    comparing the full normalized address in that case.
    """
    # Only the head matters: what follows the first comma is the barrio/city,
    # which some portals append and others omit.
    head = normalize_address(direccion).split(',')[0]

    tokens: list[str] = []
    for token in re.split(r'[^0-9a-z]+', head):
        if not token:
            continue
        token = _ADDRESS_ABBREVIATIONS.get(token, token)
        if token in _ADDRESS_UNIT_MARKERS:
            break
        if token in _ADDRESS_FILLER:
            continue
        tokens.append(token)

    # The altura is the trailing number; the street is everything before it —
    # which keeps numeric street names ("Calle 7 1234" → "7 1234") intact.
    if len(tokens) < 2 or not tokens[-1].isdigit():
        return None
    return ' '.join(tokens)


_ZONA_SUFFIXES = (
    ', caba', ', capital federal', ', ciudad autonoma de buenos aires',
    ', provincia de buenos aires', ', buenos aires', ', argentina',
)


def normalize_zona(zona: str) -> str:
    """Cache key for a zona. Reuses address normalization (accent-strip, lower,
    whitespace-collapse) then drops trailing city/province/country qualifiers so
    'Palermo', 'Palermo, CABA' and 'palermo, Buenos Aires' share one key."""
    s = normalize_address(zona)
    changed = True
    while changed:
        changed = False
        for suf in _ZONA_SUFFIXES:
            if s.endswith(suf):
                s = s[: -len(suf)].strip()
                changed = True
    return s


# Terms that name a whole province/city rather than a locality. Degrading a
# barrio search down to one of these stops being a barrio search — "Palermo,
# CABA" falling back to "CABA" would hand back the entire city — so the
# candidate chain stops before them.
_WIDE_JURISDICTIONS = frozenset({
    'caba', 'capital federal', 'ciudad autonoma de buenos aires',
    'provincia de buenos aires', 'buenos aires', 'argentina',
})


def zona_candidates(zona: str) -> list[str]:
    """A zona → the phrases to try for it, most specific first.

    No two portals model the same barrio alike: for La Plata's casco urbano,
    InmoBusqueda has an exact localidad, RE/MAX's only literal match is a
    gated community, Argenprop's is a homonym in San Luis, and ZonaProp and
    Mudafy have no such concept — so there is no canonical phrase to rewrite
    a zona INTO. Callers instead walk this chain, falling back to the
    containing localidad when a portal's own autocomplete does not know (or
    misreads) the barrio.

    The chain doubles as the zona guard's phrase set: a listing that only
    names the localidad ("calle 47 e/ 12 y 13, La Plata") still survives, which
    is what a barrio-level guard was rejecting outright.
    """
    parts = [' '.join(p.split()) for p in zona.split(',')]
    parts = [p for p in parts if p]

    chain: list[str] = []
    while parts:
        phrase = ', '.join(parts)
        if phrase not in chain:
            chain.append(phrase)
        # Stop at a bare term, or when the next fallback would be a whole
        # province/city rather than a locality.
        if len(parts) < 2 or normalize_address(', '.join(parts[1:])) in _WIDE_JURISDICTIONS:
            break
        parts = parts[1:]
    return chain


# --- Zona filter catalogue (Gran La Plata) ------------------------------------
#
# `properties` has no locality column — the baseline migration only stores
# `direccion`/`direccion_norm` — so the locality named inside the address is the
# only signal a "Zona" filter can stand on. Each entry lists the phrases that
# identify its locality; portals spell City Bell both ways, hence two terms.
ZONA_TERMS: dict[str, tuple[str, ...]] = {
    'la_plata': ('la plata',),
    'city_bell': ('city bell', 'citybell'),
    'gonnet': ('gonnet',),
    'villa_elisa': ('villa elisa',),
    'hudson': ('hudson',),
}

# "La Plata" names BOTH a locality and the partido containing City Bell, Gonnet
# and Villa Elisa, which portals publish as "City Bell, La Plata". Left
# unqualified the La Plata option would swallow its own siblings, so it negates
# them — a picker implies its options are mutually exclusive. The reverse is not
# true: "City Bell, La Plata" IS a City Bell listing, so no other zona excludes.
ZONA_EXCLUDES: dict[str, tuple[str, ...]] = {
    'la_plata': tuple(
        term
        for slug, terms in ZONA_TERMS.items() if slug != 'la_plata'
        for term in terms
    ),
}


def zona_filter(slug: str) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """``(match_terms, exclude_terms)`` for a zona slug, or ``None`` if unknown.

    Callers turn `match` into an OR of `ilike` clauses and `exclude` into
    negated `ilike`s. Unknown slugs return ``None`` rather than an empty filter
    so a typo surfaces instead of silently widening the result set.
    """
    terms = ZONA_TERMS.get(slug)
    if terms is None:
        return None
    return terms, ZONA_EXCLUDES.get(slug, ())
