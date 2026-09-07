"""Gated communities (barrio cerrado / club de campo / country) as first-class
search targets.

WHY A CATALOGUE AND NOT JUST A STRING. `zona_candidates` builds its fallback
chain out of the comma string the user typed: "City Bell, La Plata" degrades
to "La Plata" when a portal cannot resolve the barrio. A gated community never
arrives with that string — the user says "Grand Bell", full stop — so its
chain is one link long and a portal that misses it returns nothing at all.
The catalogue stores the one fact nobody can derive: which localidad contains
the barrio. `barrio_zona` splices it back in, and the whole existing pipeline
(candidate chain, zona guard, ZonaProp union collapse) works unchanged.

THE SECOND HALF is the guard. Once a search DOES degrade to the localidad,
something has to keep the rest of City Bell out of a Grand Bell search, and
the only signal left is the listing's free text — where no two portals spell a
barrio the same way. Measured live (2026-09-04):

    Argenprop      "Los Ceibos, Partido de La Plata"           → LOS-CEIBOS-LP
    Argenprop      "Barrio Los Ceibos  - Km.32, González Catan"
    InmoBusqueda   "Club de campo Los Ceibos, City Bell, ..."   → barrio_id 129
    InmoBusqueda   "Countries y Barrios Cerrados Nordelta, ..."
    ZonaProp       h1 "42 Casas en venta en Grand Bell, City Bell"

`strip_kind_prefix` reads a portal's label back to the identity, and
`barrio_aliases` expands the identity into every form a portal might publish.

Stdlib-only apart from `app.services.zona`, which is itself stdlib-only — so
both the scrapers and the extraction graph can import this without a cycle.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from app.services.zona import normalize_address


def _norm(value: str) -> str:
    """Accent-folded, lowercased, whitespace-collapsed — the same normal form
    the address/zona helpers use, so a barrio and a zona compare alike."""
    return normalize_address(value)


# The KIND a portal prepends to the name. Longest first: "country club" has to
# win over "country", and "countries y barrios cerrados" (InmoBusqueda's own
# grouping label) over "barrios cerrados", or the leftover fragment becomes
# part of the identity.
#
# Note what is NOT here: "chacras". "Chacras de la Reserva" and "Chacras del
# Country" are NAMES — the word is the identity, not a label bolted onto it.
# Stripping it left "de la Reserva", which matches nothing.
#
# Abbreviations are listed in their written form ("B°", "Bo.") and normalized
# through the same accent/punctuation fold as the label, so they all collapse
# onto the tokens a comparison actually sees ("b", "bo").
_KIND_PREFIXES: tuple[str, ...] = tuple(sorted(
    {
        _p for _p in (
            _norm(_raw) for _raw in (
                'countries y barrios cerrados', 'country y barrio cerrado',
                'barrios cerrados', 'barrio cerrado', 'barrio privado',
                'club de campo', 'country club', 'club nautico',
                'complejo residencial', 'complejo',
                'condominio',
                'country', 'barrio', 'bo.', 'bo', 'b°', 'b.',
            )
        ) if _p
    },
    key=len,
    reverse=True,
))

# Kinds we GENERATE aliases for. Narrower than `_KIND_PREFIXES` on purpose:
# stripping has to recognise everything a portal might send, but emitting
# every variant would put a dozen near-useless phrases in front of the guard's
# substring scan on every single listing.
_ALIAS_KINDS: tuple[str, ...] = (
    'barrio cerrado', 'barrio privado', 'club de campo', 'country', 'barrio',
)


def strip_kind_prefix(label: str) -> str:
    """A portal's label → the barrio's identity, with the leading kind removed.

    Only a LEADING kind is a kind: "Chacras del Country" is a name, and
    stripping mid-string would turn it into "del". A label that is *nothing
    but* a kind is returned untouched — an empty identity matches every
    listing, which is worse than a redundant one.
    """
    cleaned = ' '.join(label.split())
    if not cleaned:
        return ''

    lowered = _norm(cleaned)
    for prefix in _KIND_PREFIXES:
        # The trailing space is load-bearing: without it the one-letter "b"
        # prefix eats the head of "Bella Vista".
        if not lowered.startswith(f'{prefix} '):
            continue
        # The normalized string and the original run in lockstep only in word
        # COUNT, not in characters (accents, double spaces) — so drop words,
        # never a character slice.
        rest = ' '.join(cleaned.split()[len(prefix.split()):])
        if rest:
            return rest
    return cleaned


def barrio_aliases(nombre: str, extra: Iterable[str] = ()) -> tuple[str, ...]:
    """Every phrase a portal might publish this barrio under, normalized.

    Order is deterministic (identity, then kind-prefixed forms, then the
    operator's own) because the tuple is persisted on the catalogue row and
    diffed when a barrio is edited.

    A blank name yields an EMPTY tuple rather than a set containing '': the
    guard reads an empty phrase as "matches everything", and that would hand a
    country search the whole localidad — the exact failure this module exists
    to prevent.
    """
    identity = strip_kind_prefix(nombre)
    if not _norm(identity):
        return ()

    aliases: list[str] = []

    def add(value: str) -> None:
        norm = _norm(value)
        if norm and norm not in aliases:
            aliases.append(norm)

    add(identity)
    # The name AS STORED, when the operator already wrote the kind in.
    add(nombre)

    # Only expand kinds onto a bare identity. "Club de Campo Los Ceibos"
    # already carries one; prefixing another spells a phrase nobody writes.
    if _norm(nombre) == _norm(identity):
        for kind in _ALIAS_KINDS:
            add(f'{kind} {identity}')

    for value in extra:
        add(value)
    return tuple(aliases)


def barrio_matches(texto: str, aliases: Iterable[str]) -> bool:
    """Does this listing's free text name the barrio?

    Matched on WORD BOUNDARIES, not raw substring: Argenprop serves both "El
    Rodeo" (Córdoba) and "Rodeo de la Cruz" (Mendoza), and a bare `in` would
    collapse them. Two places 1000 km apart in one result set is not a near
    miss, it is a wrong answer.

    An empty alias set matches NOTHING — deliberately the inverse of the zona
    guard's "empty keeps everything". An empty set here means we failed to
    build a barrio filter, and the honest answer to that is zero results, not
    every house in City Bell.
    """
    phrases = [a for a in (_norm(a) for a in aliases) if a]
    if not phrases:
        return False
    haystack = _norm(texto)
    return any(
        re.search(rf'(?<![0-9a-z]){re.escape(p)}(?![0-9a-z])', haystack)
        for p in phrases
    )


def barrio_zona(nombre: str, localidad: str) -> str:
    """`"Grand Bell" + "City Bell, La Plata"` → `"Grand Bell, City Bell, La Plata"`.

    The composite the rest of the pipeline already understands: feed it to
    `zona_candidates` and the chain degrades barrio → localidad → partido, so
    a portal that cannot resolve the barrio still answers from its localidad
    (with `barrio_matches` keeping the neighbours out) instead of returning
    nothing.

    The KIND is stripped here, always. This string becomes a URL slug on every
    portal, and none of them carry the kind in one: measured 2026-09-04,
    `/casas-venta-grand-bell.html` serves the barrio's 42 listings while
    `/casas-venta-barrio-grand-bell.html` falls through to the NATIONWIDE
    listing — 172.141 results wearing the shape of an answer. So a barrio the
    operator stored as "Club de Campo Los Ceibos" still searches as
    "Los Ceibos, City Bell, La Plata"; `barrio_aliases` keeps the kind-prefixed
    spellings for the text guard, where they belong.
    """
    parts = [' '.join(p.split()) for p in (strip_kind_prefix(nombre), *localidad.split(','))]
    return ', '.join(p for p in parts if p)
