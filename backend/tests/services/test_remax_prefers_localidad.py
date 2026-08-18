"""RE/MAX location resolution must land on the localidad, not the partido.

Asking for "La Plata" means the city — its casco urbano — not the partido that
also contains City Bell, Villa Elisa, Gonnet, Tolosa and Los Hornos. RE/MAX
models both and its autocomplete ranks the partido FIRST, so taking the first
label that contains the query resolved `in:::68::::` (level 2, `countyId`).
Measured live against that filter: of 200 listings only 123 were the casco —
the rest were sibling localities the user explicitly did not ask for.

Matches are ranked, not filtered: an exact head — the label's first component
EQUALLING the query head — is the strong signal, and among those the deepest
level wins, which is what picks "La Plata, La Plata" (level 3, `cityId`) over
"La Plata, Buenos Aires" (level 2, `countyId`).

Exact head deliberately is NOT a hard requirement. RE/MAX often spells a zona
more formally than the user does ("Gonnet" → "Manuel B Gonnet"), and demanding
an exact head would drop those entirely; without one the portal's own relevance
order decides. The cost is that a lone near-match still wins — "Casco Urbano"
resolves to the gated community "Los Eucaliptus Casco Urbano", which serves
zero listings. Recovering from that is the zona candidate chain's job, not this
resolver's; see `test_zona_candidate_fallback`.
"""
import httpx
import pytest

from app.services import apify
from app.services.apify import _remax_resolve_location


def _entry(label: str, level: int, loc_id: int) -> dict:
    field = {2: 'countyId', 3: 'cityId', 4: 'neighborhoodId',
             5: 'privatecommunityId'}[level]
    return {'label': label, 'level': level, field: loc_id}


# Shapes copied from real `search/findAll/{q}?level=1` responses.
_RESPONSES: dict[str, list[dict]] = {
    'la plata': [
        _entry('<b>La</b> <b>Plata</b>, Buenos Aires', 2, 68),
        _entry('Abasto, <b>La</b> <b>Plata</b>, Buenos Aires', 3, 900),
        _entry('City Bell, <b>La</b> <b>Plata</b>, Buenos Aires', 3, 901),
        _entry('<b>La</b> <b>Plata</b>, <b>La</b> <b>Plata</b>, Buenos Aires', 3, 1066),
        _entry('Campo la <b>Plata</b>, Balcarce, Buenos Aires', 3, 902),
    ],
    'casco urbano': [
        _entry('Los Eucaliptus <b>Casco</b> <b>Urbano</b>, La Plata, Buenos Aires', 3, 1066),
    ],
    'gonnet': [
        _entry('Manuel B <b>Gonnet</b>, La Plata, Buenos Aires', 3, 1067),
        _entry('Barrio Angosto, Manuel B <b>Gonnet</b>, La Plata, Buenos Aires', 4, 847447),
    ],
    'city bell': [
        _entry('City Bell, La Plata, Buenos Aires', 3, 901),
        _entry('Grand Bell, City Bell, La Plata, Buenos Aires', 5, 7001),
        _entry('La Chiquita, City Bell, La Plata, Buenos Aires', 5, 7002),
    ],
}


@pytest.fixture(autouse=True)
def _mock_autocomplete(monkeypatch):
    class _FakeResponse:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class _FakeAsyncClient:
        def __init__(self, *a, **kw) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, params=None, **kw):
            q = str(url).rsplit('/', 1)[-1]
            from urllib.parse import unquote
            key = unquote(q).lower()
            return _FakeResponse({'data': {'geoSearch': _RESPONSES.get(key, [])}})

    monkeypatch.setattr(httpx, 'AsyncClient', _FakeAsyncClient)
    apify._REMAX_LOCATION_CACHE.clear()


def _slot(location: str) -> tuple[int, str]:
    """(level, id) encoded in an `in:` filter."""
    slots = location.removeprefix('in:').split(':')
    return next((i, v) for i, v in enumerate(slots) if v)


class TestPrefersTheLocalidadOverThePartido:
    async def test_la_plata_resolves_to_the_city_not_the_county(self):
        location = await _remax_resolve_location('La Plata')
        assert _slot(location) == (3, '1066')

    async def test_does_not_resolve_to_the_partido_id(self):
        """`in:::68::::` is the regression: it drags in every sibling."""
        assert await _remax_resolve_location('La Plata') != 'in:::68::::'


class TestExactHeadIsARankingSignalNotAHardFilter:
    async def test_portal_spelling_the_zona_more_fully_still_resolves(self):
        """"Gonnet" is "Manuel B Gonnet" on RE/MAX — no exact head, yet the
        same place. Requiring an exact head outright would break every zona
        the portal names more formally than the user does."""
        assert _slot(await _remax_resolve_location('Gonnet')) == (3, '1067')

    async def test_deeper_level_does_not_win_without_an_exact_head(self):
        """Barrio Angosto (level 4) sits inside the Gonnet result set; the
        portal's own ranking must keep the localidad on top."""
        assert _slot(await _remax_resolve_location('Gonnet')) != (4, '847447')

    async def test_gated_community_wins_only_because_nothing_better_exists(self):
        """"Los Eucaliptus Casco Urbano" is the sole match, so it resolves —
        and serves zero listings. The zona candidate chain is what recovers
        from that; see test_zona_candidate_fallback."""
        assert _slot(await _remax_resolve_location('Casco Urbano, La Plata')) == (3, '1066')

    async def test_unknown_zona_stays_none(self):
        assert await _remax_resolve_location('Zona Inexistente') is None


class TestOrdinaryLocalidadesStillResolve:
    async def test_city_bell_picks_the_locality_not_its_gated_communities(self):
        location = await _remax_resolve_location('City Bell, La Plata')
        assert _slot(location) == (3, '901')

    async def test_sibling_locality_is_not_confused_with_the_casco(self):
        casco = await _remax_resolve_location('La Plata')
        bell = await _remax_resolve_location('City Bell, La Plata')
        assert casco != bell
