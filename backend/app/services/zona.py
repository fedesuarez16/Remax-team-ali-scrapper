"""Pure zona/address normalization helpers.

Extracted from `app.graphs.extraction.nodes` (moved verbatim) so both the
extraction graph and the geocoding services (`app.services.geocode`) can
share one source of truth without a circular import — `nodes.py` lazily
imports `geocode.py`, so `geocode.py` must never import `nodes.py`.

Stdlib-only. No other project imports.
"""
from __future__ import annotations

import unicodedata


def normalize_address(direccion: str) -> str:
    s = unicodedata.normalize('NFKD', direccion).encode('ascii', 'ignore').decode()
    return ' '.join(s.lower().split())


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
