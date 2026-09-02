"""A gated community (barrio cerrado / country) must resolve on RE/MAX.

The autocomplete's `level` is NOT the `locations` slot. Verified live
(2026-09-02, GET /search/findAll/Grand Bell?level=1):

    {"level": 4, "label": "Grand Bell, City Bell, La Plata, Buenos Aires",
     "countyId": 68, "cityId": 1058, "neighborhoodId": 0,
     "privatecommunityId": 2439}

A country comes back as level 4 — the same level as a barrio — but with
`neighborhoodId` 0 and its id in `privatecommunityId`. Mapping level → id
field read the 0, dropped the only exact-head candidate, and the resolver
returned None: the search fell back to nationwide paging and served ~0
listings for every gated community, silently.

Which slot filters was also measured against `listings/findAllWithEntrepreneurships`:

    in::::::2439:   (slot 5, private community)  → Grand Bell listings
    in:::::2439::   (slot 4, neighborhood)        → 0

So the id field decides the slot, not the level: the DEEPEST populated id
is the entry's own identity — the shallower ones are its parent chain.
"""
import httpx
import pytest

from app.services import apify
from app.services.apify import _remax_resolve_location


def _geo(label: str, *, level: int, county_id: int = 0, city_id: int = 0,
         nbh_id: int = 0, pc_id: int = 0) -> dict:
    """Full shape of a real `geoSearch` entry: every id field present, the
    ones that do not apply set to 0 (the portal never omits them)."""
    return {
        'label': label, 'level': level, 'stateId': 'BA', 'subregionId': None,
        'countyId': county_id, 'cityId': city_id,
        'neighborhoodId': nbh_id, 'privatecommunityId': pc_id,
    }


# Copied from live responses.
_RESPONSES: dict[str, list[dict]] = {
    'grand bell': [
        # Fuzzy decoy the portal ranks FIRST: "Belleville, Monte Grande".
        _geo('<b>Bell</b>eville, Monte <b>Grand</b>e, Esteban Echeverria, Buenos Aires',
             level=4, county_id=40, city_id=704, pc_id=202706),
        _geo('<b>Grand</b> <b>Bell</b>, City <b>Bell</b>, La Plata, Buenos Aires',
             level=4, county_id=68, city_id=1058, pc_id=2439),
    ],
    'las cañitas': [
        _geo('Las <b>Cañitas</b>, Palermo, Capital Federal',
             level=4, county_id=1, city_id=25024, nbh_id=5),
    ],
    'gonnet': [
        _geo('Manuel B <b>Gonnet</b>, La Plata, Buenos Aires',
             level=3, county_id=68, city_id=1067),
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
            from urllib.parse import unquote
            key = unquote(str(url).rsplit('/', 1)[-1]).lower()
            return _FakeResponse({'data': {'geoSearch': _RESPONSES.get(key, [])}})

    monkeypatch.setattr(httpx, 'AsyncClient', _FakeAsyncClient)
    apify._REMAX_LOCATION_CACHE.clear()


class TestGatedCommunityResolvesToThePrivateCommunitySlot:
    async def test_country_with_partido(self):
        """The parser's shape: "Barrio, Partido"."""
        assert await _remax_resolve_location('Grand Bell, La Plata') == 'in::::::2439:'

    async def test_country_alone(self):
        assert await _remax_resolve_location('Grand Bell') == 'in::::::2439:'

    async def test_neighborhood_slot_is_never_used_for_a_country(self):
        """`in:::::2439::` is the regression: it serves nothing."""
        assert await _remax_resolve_location('Grand Bell, La Plata') != 'in:::::2439::'

    async def test_fuzzy_decoy_ranked_first_does_not_win(self):
        assert '202706' not in (await _remax_resolve_location('Grand Bell, La Plata') or '')


class TestOtherLevelsKeepTheirSlot:
    async def test_real_neighborhood_still_uses_the_neighborhood_slot(self):
        assert await _remax_resolve_location('Las Cañitas') == 'in:::::5::'

    async def test_city_still_uses_the_city_slot(self):
        assert await _remax_resolve_location('Gonnet') == 'in::::1067:::'
