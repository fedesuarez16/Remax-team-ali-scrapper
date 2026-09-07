"""How does one gated community figure on each portal? — asked, not guessed.

This is the "búsqueda por barrio" half of the catalogue: the operator loads
"Grand Bell, City Bell, La Plata" by hand, and this module goes and asks every
portal what IT calls that place, so the answer can be reviewed by a human once
and reused by the scraper forever.

WHY IT IS WORTH A ROUND TRIP. The portals disagree, and not cosmetically —
the difference decides whether a search returns the barrio or the country.
Measured live (2026-09-04):

    RE/MAX        privatecommunityId 2439            → `in::::::2439:`
    Argenprop     CodigoBarrio GRAND-BELL, under the SYNTHETIC localidad
                  LA-PLATA-COUNTRIES-BARRIOS-CERRADOS
    ZonaProp      /casas-venta-grand-bell.html → 301 → …-city-bell-la-plata, 42
    MercadoLibre  /casas/venta/grand-bell/     → 301 → bsas-gba-sur/…, 29
    InmoBusqueda  barrio_id 9 → NO page; the bare slug DROPS the zona
    Mudafy        no gated-community pages at all — every URL 404s

TWO STRATEGIES, AND THE ASYMMETRY BETWEEN THEM. `native` means the portal owns
the entity and will filter server-side. `localidad` means it does not, so the
search runs on the containing localidad and `barrio_cerrado.barrio_matches`
(plus the polygon, when there is one) narrows the results here.

Guessing `localidad` when `native` was available costs recall — more pages
walked, same answer. Guessing `native` when it is wrong serves a whole
partido as if it were the barrio: measured, `casas-venta-grand-bell-la-plata`
returns 5.951 listings against the real 40. So every ambiguous case resolves
to `localidad`, including a portal that simply errored.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

from app.services.apify import (
    PORTAL_SOURCES,
    _ARGENPROP_AUTOCOMPLETE_URL,
    _argenprop_resolve_zona_slug,
    _c21_resolve_location,
    _inmobusqueda_resolve_zona_slug,
    _remax_resolve_location,
    _slugify,
)
from app.services.barrio_cerrado import barrio_probe_forms, strip_kind_prefix

Strategy = Literal['native', 'localidad']

# Portals that build their location segment by slugifying the zona, with no
# autocomplete we can reach off the metered residential proxy. Both were
# measured to canonicalise a BARE barrio slug on their own (see module
# docstring), so the probe reports what the URL builder will produce and
# leaves `confirmed` false — a claim for a human, not a verified fact.
_SLUG_BUILT: tuple[str, ...] = ('zonaprop', 'mercadolibre')

# Portals with no gated-community pages at all. Not "we failed to resolve" —
# measured absent, so re-probing will not change the answer.
_NO_NATIVE_SUPPORT: tuple[str, ...] = ('mudafy',)

# RE/MAX writes its filter as `in:` + 7 colon slots; the slot says which id
# FIELD matched, and therefore how precise the match is. Slot 4 is
# `neighborhoodId`, slot 5 `privatecommunityId` — the two that name something
# SMALLER than a localidad. Slot 3 (`cityId`) resolving for a barrio query
# means the portal handed back the containing city: correct place, wrong
# altitude, and accepting it as the barrio's ref would call "all of Gonnet"
# a precise Grand Bell search.
_REMAX_BARRIO_SLOTS = frozenset({4, 5})


@dataclass(frozen=True)
class PortalRef:
    """One portal's answer about one barrio."""

    portal: str
    strategy: Strategy
    ref: str | None
    label: str | None
    result_count: int | None
    confirmed: bool
    note: str

    def as_row(self, barrio_id: str) -> dict:
        """The `barrio_cerrado_portal_refs` row this answer persists as."""
        return {
            'barrio_id': barrio_id,
            'portal': self.portal,
            'strategy': self.strategy,
            'ref': self.ref,
            'label': self.label,
            'result_count': self.result_count,
            'confirmed': self.confirmed,
            'note': self.note,
        }


def _localidad(strategy_note: str) -> dict:
    return {'strategy': 'localidad', 'ref': None, 'note': strategy_note}


def _remax_slot(locations: str) -> int | None:
    """Which slot RE/MAX populated in a `locations` filter string.

    `in::::1067:::` → 3, `in:::::5::` → 4, `in::::::2439:` → 5. The leading
    `in` occupies index 0 of the split, so slot = index - 1.
    """
    parts = locations.split(':')
    for idx, value in enumerate(parts):
        if idx and value:
            return idx - 1
    return None


async def _probe_remax(zona: str, _identity: str, _localidad_slug: str) -> dict:
    locations = await _remax_resolve_location(zona)
    if not locations:
        return _localidad('RE/MAX no resolvió la ubicación; se busca por localidad.')
    slot = _remax_slot(locations)
    if slot not in _REMAX_BARRIO_SLOTS:
        return _localidad(
            f'RE/MAX resolvió a slot {slot} (localidad o partido, no el barrio); '
            'se busca por localidad.',
        )
    kind = 'barrio privado' if slot == 5 else 'barrio'
    return {
        'strategy': 'native', 'ref': locations,
        'note': f'RE/MAX lo tiene como {kind} (slot {slot}).',
    }


async def _probe_argenprop(zona: str, _identity: str, localidad_slug: str) -> dict:
    slug = await _argenprop_resolve_zona_slug(zona)
    if not slug:
        return _localidad('Argenprop no resolvió el barrio; se busca por localidad.')
    if slug == localidad_slug:
        return _localidad(
            f'Argenprop devolvió la localidad ({slug}), no el barrio; '
            'se busca por localidad.',
        )
    return {
        'strategy': 'native', 'ref': slug,
        'note': f'Argenprop lo tiene como barrio propio ({slug}).',
    }


async def _probe_inmobusqueda(zona: str, _identity: str, localidad_slug: str) -> dict:
    slug = await _inmobusqueda_resolve_zona_slug(zona)
    if not slug:
        return _localidad(
            'InmoBusqueda no le da página propia (barrio anidado en su '
            'localidad); se busca por localidad.',
        )
    if slug == localidad_slug:
        return _localidad(
            f'InmoBusqueda devolvió la localidad ({slug}); se busca por localidad.')
    return {
        'strategy': 'native', 'ref': slug,
        'note': f'InmoBusqueda lo tiene como localidad propia ({slug}).',
    }


async def _probe_century21(zona: str, identity: str, _localidad_slug: str) -> dict:
    location = await _c21_resolve_location(zona)
    if not location:
        return _localidad('Century21 no resolvió la ubicación; se busca por localidad.')
    # C21 writes one path segment per level and gives a gated community its own
    # (`en-division_lomas-de-city-bell`), so the barrio's own slug appearing in
    # the path IS the portal saying it got down to the barrio.
    if _slugify(identity) not in _slugify(location):
        return _localidad(
            f'Century21 resolvió a una ubicación más ancha ({location}); '
            'se busca por localidad.',
        )
    return {
        'strategy': 'native', 'ref': location,
        'note': f'Century21 lo tiene con tramo propio ({location}).',
    }


_PROBES = {
    'remax': _probe_remax,
    'argenprop': _probe_argenprop,
    'inmobusqueda': _probe_inmobusqueda,
    'century21': _probe_century21,
}


async def _probe_first_hit(
    probe: Any, formas: list[str], identity: str, localidad_slug: str,
) -> dict:
    """Run a portal's probe over the barrio's query forms, keeping the first
    `native` answer.

    The cascade is not politeness, it is a measured requirement: the portals
    disagree about which ADMINISTRATIVE LEVEL a gated community hangs off.
    Argenprop files Grand Bell under the partido ("Grand Bell, Partido de La
    Plata"), InmoBusqueda under the localidad ("Grand Bell, City Bell, Pdo. de
    La Plata"). Both resolvers require every comma part of the query to appear
    in the label, so one single query shape is guaranteed to miss one of them
    — and it did: the three-part composite made Argenprop report `localidad`
    for a barrio it has as `CodigoBarrio=GRAND-BELL`.

    A `localidad` answer is never returned early; the last form's answer is,
    so the note the operator reads describes the narrowest attempt.
    """
    answer = _localidad('sin intentos')
    for forma in formas:
        answer = await probe(forma, identity, localidad_slug)
        if answer['strategy'] == 'native':
            return answer
    return answer


async def _probe_one(
    portal: str, formas: list[str], identity: str, localidad_slug: str,
) -> PortalRef:
    if portal in _NO_NATIVE_SUPPORT:
        answer = _localidad(
            f'{portal} no publica páginas de barrio cerrado (medido: 404 en '
            'todas las formas de URL); se busca por localidad + filtro por nombre.',
        )
    elif portal in _SLUG_BUILT:
        slug = _slugify(identity)
        answer = {
            'strategy': 'native', 'ref': slug,
            'note': (
                f'{portal} canonicaliza el slug pelado ({slug}) por redirect. '
                'Sin verificar — confirmalo corriendo una búsqueda.'
            ),
        }
    else:
        probe = _PROBES[portal]
        try:
            answer = await _probe_first_hit(probe, formas, identity, localidad_slug)
        except Exception as e:  # noqa: BLE001 — a portal outage is not a verdict
            # An error is NOT "this barrio does not exist here". Writing that
            # down would freeze a transient failure into the catalogue, so the
            # row degrades to the always-correct strategy and says why, which
            # makes a re-probe the obvious next step.
            answer = _localidad(f'Error consultando {portal}: {e}. Se busca por localidad.')

    return PortalRef(
        portal=portal,
        strategy=answer['strategy'],  # type: ignore[arg-type]
        ref=answer.get('ref'),
        label=answer.get('label'),
        result_count=answer.get('result_count'),
        # Nothing the probe decides on its own is confirmed. Confirmation is a
        # human looking at the label and saying "yes, that is my barrio" —
        # which matters because homonyms are the norm, not the exception:
        # Argenprop serves a "Los Ceibos" in Tigre, La Plata, Córdoba,
        # Corrientes and González Catán.
        confirmed=False,
        note=answer['note'],
    )


async def probe_barrio(nombre: str, localidad: str) -> list[PortalRef]:
    """Ask every portal how it names this barrio. One row per portal, always.

    A portal with no row is indistinguishable from "not probed yet" in the
    review UI, so a portal that cannot resolve gets an explicit `localidad`
    row rather than being omitted.

    The zona sent to each resolver is the COMPOSITE ("Grand Bell, City Bell,
    La Plata"), never the bare name: the comma parts are what separate the
    homonyms, and Argenprop's first hit for a bare "Los Ceibos" is in Tigre.
    """
    identity = strip_kind_prefix(nombre)
    formas = barrio_probe_forms(nombre, localidad)
    localidad_slug = _slugify(localidad.split(',')[0])

    return list(await asyncio.gather(*(
        _probe_one(portal, formas, identity, localidad_slug)
        for portal in PORTAL_SOURCES
    )))


# ── Bulk discovery: enumerating a partido's gated communities ─────────────────
#
# Argenprop is the only one of the seven portals that files gated communities
# under a SYNTHETIC pseudo-locality — `LA-PLATA-COUNTRIES-BARRIOS-CERRADOS`,
# `TIGRE-COUNTRIES-BARRIOS-CERRADOS`. Every country in the partido hangs off
# that one code, which turns the fuzzy autocomplete into an enumeration: filter
# on the locality code and what is left IS the catalogue.
#
# The code is NOT derivable, which is why this reads it off the response
# instead of building it. Measured 2026-09-04, the format is not even stable:
#   GRANADERO-BAIGORRIA-COUNTRIES-BARRIOS-CERRADOS
#   COUNTRIES-YBRS-CERRADOS-EN-FUNES

# What a pseudo-locality code looks like, in slug form. Matched loosely (both
# observed spellings contain these two tokens) because the exact format varies
# per partido and a strict pattern would silently drop a whole partido.
_PSEUDO_LOCALITY_MARKERS = ('countries', 'cerrados')

# The autocomplete's own result ceiling, measured live: 30 rows, no error, no
# marker. A partido with more gated communities than this comes back truncated
# and looking complete — the single most dangerous thing about this endpoint,
# so the count is compared against it and reported.
ARGENPROP_AUTOCOMPLETE_CAP = 30

# The autocomplete sits behind CloudFront, which answers a header-less request
# with a bare `403 Request blocked` — no JSON, no hint. Measured: the same
# query with a browser UA and an argenprop.com Referer returns 200. The
# resolver already in production does not send these and works from the
# deployment's IP, so this is belt-and-braces for a call the operator triggers
# by hand and must not see fail for a reason the response does not explain.
_ARGENPROP_DISCOVERY_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
    ),
    'Referer': 'https://www.argenprop.com/',
    'Accept': 'application/json',
}


@dataclass(frozen=True)
class BarrioCandidate:
    """One gated community found by the bulk discovery, ready for review."""

    nombre: str
    localidad: str
    label: str
    argenprop_ref: str
    kind: str = 'barrio_cerrado'

    def as_dict(self) -> dict:
        return {
            'nombre': self.nombre,
            'localidad': self.localidad,
            'label': self.label,
            'argenprop_ref': self.argenprop_ref,
            'kind': self.kind,
        }


@dataclass(frozen=True)
class BarrioDiscovery:
    """What one partido's enumeration found, and how much to trust it."""

    barrios: list[BarrioCandidate]
    total_api: int
    truncated: bool
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            'barrios': [b.as_dict() for b in self.barrios],
            'total_api': self.total_api,
            'truncated': self.truncated,
            'error': self.error,
        }


def _is_pseudo_locality(code: str) -> bool:
    slug = _slugify(code)
    return all(marker in slug for marker in _PSEUDO_LOCALITY_MARKERS)


# Seeds for the `deep` sweep. The base query returns only the ALPHABETICAL HEAD
# of a big partido — measured live, "countries pilar" stops at "El Zorzal" and
# everything from F to Z is simply absent. A single extra letter changes
# nothing (the fuzzy match ignores it), but a 3+ character seed does move the
# window: "countries pilar san" returned 21 rows the base query never reaches
# (San Francisco, Santa Silvina, Santa Rosa).
#
# These are the head-words Argentine gated communities are actually named after,
# not the alphabet. ~20 requests, paid only when the caller asks for them, and
# still no guarantee of completeness — no capped fuzzy endpoint can give one,
# which is why `truncated` keeps travelling in the result either way.
_DEEP_SEEDS: tuple[str, ...] = (
    'san', 'santa', 'los', 'las', 'club', 'altos', 'lomas', 'campo', 'chacras',
    'haras', 'villa', 'estancia', 'parque', 'puerto', 'rincon', 'colinas',
    'solar', 'jardin', 'golf', 'country', 'barrio', 'del', 'valle', 'cerro',
)


async def discover_argenprop_barrios(
    partido: str, localidad: str | None = None, *, deep: bool = False,
) -> BarrioDiscovery:
    """Enumerate a partido's gated communities from Argenprop.

    `localidad` defaults to the PARTIDO, and that is a deliberate downgrade,
    not a shortcut. Argenprop labels Grand Bell "Partido de La Plata" while the
    barrio actually sits in City Bell, so the containing localidad is simply
    not in the data. "Grand Bell, La Plata" degrades one rung less finely than
    "Grand Bell, City Bell, La Plata" — but it is CORRECT, and guessing City
    Bell would not be. The operator refines it afterwards, per barrio or by
    passing `localidad` for a lot they know shares one.

    Two entry kinds are dropped, both by reading the locality code rather than
    the label: the pseudo-locality's own heading row (no `CodigoBarrio` — it is
    the whole partido's listing, not a place), and anything hanging off a
    DIFFERENT locality, which is either another partido the fuzzy search
    dragged in or an ordinary non-gated barrio.
    """
    import httpx

    base_query = f'countries {partido}'.strip()
    queries = [base_query, *(f'{base_query} {seed}' for seed in _DEEP_SEEDS)] if deep \
        else [base_query]

    async def _ask(client: Any, query: str) -> list[dict]:
        resp = await client.get(
            _ARGENPROP_AUTOCOMPLETE_URL,
            params={'stringBusqueda': query},
            headers=_ARGENPROP_DISCOVERY_HEADERS,
        )
        resp.raise_for_status()
        return list(resp.json() or [])

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            base_results = await _ask(client, base_query)
            sweeps: list[list[dict]] = []
            if deep:
                # One flaky request out of twenty must not cost the other
                # nineteen — a partial sweep is still far more than the base
                # query alone.
                sweeps = [
                    r for r in await asyncio.gather(
                        *(_ask(client, q) for q in queries[1:]),
                        return_exceptions=True,
                    )
                    if isinstance(r, list)
                ]
    except Exception as e:  # noqa: BLE001
        # An import that 500s is worse than one that finds nothing: the
        # operator can always fall back to loading barrios by hand.
        return BarrioDiscovery([], 0, False, error=f'Error consultando Argenprop: {e}')

    # The pseudo-locality this partido's countries hang off. Read from the
    # response — the code is not derivable from the partido name.
    wanted_code: str | None = None
    for entry in base_results:
        code = str((entry.get('value') or {}).get('CodigoLocalidad') or '')
        if _is_pseudo_locality(code) and _slugify(partido) in _slugify(code):
            wanted_code = code
            break

    por_ref: dict[str, BarrioCandidate] = {}
    if wanted_code:
        for entry in (e for batch in (base_results, *sweeps) for e in batch):
            value = entry.get('value') or {}
            if str(value.get('CodigoLocalidad') or '') != wanted_code:
                continue
            ref = str(value.get('CodigoBarrio') or '')
            if not ref or ref in por_ref:
                continue  # no ref = the group's heading row, not a place
            label = ' '.join(str(entry.get('label') or '').split())
            nombre = label.split(',')[0].strip()
            if not nombre:
                continue
            por_ref[ref] = BarrioCandidate(
                nombre=nombre,
                localidad=(localidad or partido).strip(),
                label=label,
                argenprop_ref=ref,
            )

    # Merging N responses destroys the alphabetical order each one arrived in,
    # and this list is reviewed by eye.
    barrios = sorted(por_ref.values(), key=lambda c: _slugify(c.nombre))

    return BarrioDiscovery(
        barrios=barrios,
        total_api=len(base_results),
        # Read off the BASE query only: the sweep's job is to widen coverage,
        # not to prove it is complete, and a sweep response hitting the cap
        # says nothing about the partido as a whole.
        truncated=len(base_results) >= ARGENPROP_AUTOCOMPLETE_CAP,
    )
