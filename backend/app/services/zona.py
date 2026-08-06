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
