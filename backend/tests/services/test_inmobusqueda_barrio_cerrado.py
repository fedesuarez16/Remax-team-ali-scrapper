"""A gated community must never resolve to an InmoBusqueda `propiedades-{slug}`
page it does not have.

THE BUG, MEASURED LIVE (2026-09-04). The autocomplete returns gated
communities in TWO shapes, and only one of them has a listing page:

    valor=Haras del Sur → {"name": "Haras del Sur, Pdo. de La Plata, ...",
                           "localidad_id": "26226", "barrio_id": "0"}
    valor=Grand Bell    → {"name": "Grand Bell, City Bell, Pdo. de La Plata, ...",
                           "localidad_id": "23250", "barrio_id": "9"}

`barrio_id: 0` means the entry IS a localidad — `propiedades-haras-del-sur.html`
is a real page. A non-zero `barrio_id` means it is a barrio nested inside
`localidad_id`, and the portal serves no page for it. Confirmed by the site's
own anti-bot interstitial, which echoes the URL it was going to render:

    /propiedades-haras-del-sur.html         → ref=/propiedades-haras-del-sur.html
    /propiedades-grand-bell.html            → ref=/propiedades.html
    /propiedades-club-de-campo-los-ceibos   → ref=/propiedades.html

`ref=/propiedades.html` is the zona being SILENTLY DROPPED — the exact trap
the resolver's own docstring documents for `propiedades-gonnet.html`. It does
not 404; it serves the whole country dressed as a valid result.

The resolver read neither field: it built the slug from `name.split(',')[0]`,
so Grand Bell resolved to `grand-bell` and every gated-community search on
this portal quietly returned nationwide listings.

FIX: an entry with a non-zero `barrio_id` is not a slug. Rejecting it lets the
zona candidate chain degrade to the containing localidad, where the barrio
guard (`app.services.barrio_cerrado.barrio_matches`) keeps the neighbours out
— a narrow, honest answer instead of a wide, wrong one.
"""
import httpx
import pytest

from app.services import apify
from app.services.apify import _inmobusqueda_resolve_zona_slug


def _entry(name: str, *, localidad_id: str | int, barrio_id: str | int = 0) -> dict:
    return {'name': name, 'provincia_id': '1', 'partido_id': '68',
            'localidad_id': localidad_id, 'barrio_id': barrio_id}


# Captured verbatim from the live autocomplete endpoint (2026-09-04).
_RESPONSES: dict[str, list[dict]] = {
    'grand bell': [
        _entry('Grand Bell, City Bell, Pdo. de La Plata, Buenos Aires',
               localidad_id='23250', barrio_id=9),
    ],
    'haras del sur': [
        _entry('Haras del Sur, Pdo. de La Plata, Buenos Aires',
               localidad_id='26226'),
        _entry('Haras del Sur 2, Pdo. de Coronel Brandsen, Buenos Aires',
               localidad_id='26227'),
    ],
    'los ceibos': [
        _entry('Los Ceibos, Córdoba, dpto. de Cordoba Capital, Córdoba',
               localidad_id='4248', barrio_id=336),
        _entry('Club de campo Los Ceibos, City Bell, Pdo. de La Plata, Buenos Aires',
               localidad_id='23250', barrio_id=129),
    ],
    'city bell': [
        _entry('City Bell, Pdo. de La Plata, Buenos Aires', localidad_id='23250'),
    ],
}


@pytest.fixture(autouse=True)
def _stub_autocomplete(monkeypatch):
    apify._INMOBUSQUEDA_SLUG_CACHE.clear()

    class _Resp:
        def __init__(self, payload: list[dict]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict]:
            return self._payload

    class _Client:
        def __init__(self, *a, **kw) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, params=None, **kw):
            valor = str((params or {}).get('valor', '')).lower()
            return _Resp(_RESPONSES.get(valor, []))

    monkeypatch.setattr(httpx, 'AsyncClient', _Client)
    yield
    apify._INMOBUSQUEDA_SLUG_CACHE.clear()


class TestABarrioIsNotASlug:
    async def test_grand_bell_does_not_resolve(self):
        """The regression: `grand-bell` served the whole country."""
        assert await _inmobusqueda_resolve_zona_slug('Grand Bell') is None

    async def test_grand_bell_with_its_localidad_does_not_resolve_either(self):
        """The composite form the catalogue builds. Same barrio, same answer —
        the containment does not conjure a page into existence."""
        assert await _inmobusqueda_resolve_zona_slug(
            'Grand Bell, City Bell, La Plata') is None

    async def test_a_kind_prefixed_barrio_does_not_resolve(self):
        """"Club de campo Los Ceibos" was the worst case: the slug carried the
        KIND too (`club-de-campo-los-ceibos`), so it could never have matched
        anything even if barrios did have pages."""
        assert await _inmobusqueda_resolve_zona_slug('Los Ceibos, La Plata') is None


class TestALocalidadShapedCountryStillResolves:
    async def test_haras_del_sur_is_a_localidad_and_resolves(self):
        """`barrio_id: 0` → a real `propiedades-haras-del-sur.html`. Rejecting
        every gated community outright would have thrown this one away."""
        assert await _inmobusqueda_resolve_zona_slug('Haras del Sur') == 'haras-del-sur'

    async def test_the_partido_still_picks_the_right_haras(self):
        """Two Haras del Sur in two partidos — the existing exact-head rule
        plus the comma part keep them apart."""
        assert await _inmobusqueda_resolve_zona_slug(
            'Haras del Sur, La Plata') == 'haras-del-sur'


class TestOrdinaryZonasAreUntouched:
    async def test_a_plain_localidad_still_resolves(self):
        assert await _inmobusqueda_resolve_zona_slug('City Bell') == 'city-bell'
