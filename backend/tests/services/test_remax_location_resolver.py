"""RE/MAX zona → server-side `locations` filter, resolved via the portal's
own autocomplete API (GET /api/search/findAll/{query}?level=1 on
api-ar.redremax.com — public, verified live).

Why this exists: without a location filter, `_scrape_remax_api` paged the
NEWEST ~100 listings nationwide and text-guarded them — for any specific
zona the newest-100 sample almost never contains it, so searches returned 0.

`locations` format (reverse-engineered from the Angular frontend's own
request, then verified live against the API): `in:` + 7 colon-separated
slots, with the location id placed in the slot matching its autocomplete
`level` — level 3 (city/localidad) uses `cityId` in slot 4
(`in::::1067:::` → 128 real Gonnet listings), level 4 (neighborhood) uses
`neighborhoodId` in slot 5 (`in:::::5::` → 103 Las Cañitas listings). The
`@label` suffix the frontend appends is ignored by the server (verified),
so only the id is sent.
"""
import httpx
import pytest

from app.services import apify
from app.services.apify import _remax_resolve_location


def _geo(label: str, *, level: int, city_id: int = 0, nbh_id: int = 0,
         county_id: int = 0, pc_id: int = 0) -> dict:
    return {
        'label': label, 'level': level, 'cityId': city_id,
        'neighborhoodId': nbh_id, 'countyId': county_id,
        'privatecommunityId': pc_id,
    }


_RESPONSES: dict[str, list[dict]] = {
    'gonnet': [
        _geo('Manuel B <b>Gonnet</b>, La Plata, Buenos Aires', level=3, city_id=1067),
        _geo('Barrio Angosto, Manuel B <b>Gonnet</b>, La Plata, Buenos Aires',
             level=4, city_id=1067, nbh_id=847447),
    ],
    'las cañitas': [
        _geo('Las <b>Cañitas</b>, Palermo, Capital Federal', level=4, city_id=25024, nbh_id=5),
    ],
    'villa elisa': [
        _geo('Villa Felisa, San Lorenzo, Santa Fe', level=4, city_id=900, nbh_id=901),
        _geo('Villa <b>Elisa</b>, La Plata, Buenos Aires', level=3, city_id=1068),
    ],
}


@pytest.fixture(autouse=True)
def _mock_autocomplete(monkeypatch):
    captured: dict = {'calls': 0, 'urls': []}

    class _FakeResponse:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> '_FakeAsyncClient':
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def get(self, url, params=None, **kwargs) -> _FakeResponse:
            captured['calls'] += 1
            captured['urls'].append(url)
            query = url.rsplit('/', 1)[-1].split('?')[0]
            from urllib.parse import unquote
            geo = _RESPONSES.get(unquote(query).lower(), [])
            return _FakeResponse({'data': {'geoSearch': geo}})

    monkeypatch.setattr(httpx, 'AsyncClient', _FakeAsyncClient)
    apify._REMAX_LOCATION_CACHE.clear()
    return captured


async def test_city_level_resolves_to_slot_4() -> None:
    assert await _remax_resolve_location('Gonnet') == 'in::::1067:::'


async def test_neighborhood_level_resolves_to_slot_5() -> None:
    assert await _remax_resolve_location('Las Cañitas') == 'in:::::5::'


async def test_label_matching_strips_bold_tags_and_skips_fuzzy_hits() -> None:
    # "Villa Felisa" (fuzzy) comes first — matching must skip it and pick the
    # exact "Villa Elisa", comparing against labels with <b></b> stripped.
    assert await _remax_resolve_location('Villa Elisa') == 'in::::1068:::'


async def test_composite_zona_requires_every_comma_part_in_label() -> None:
    assert await _remax_resolve_location('Villa Elisa, La Plata') == 'in::::1068:::'
    assert await _remax_resolve_location('Villa Elisa, Cordoba') is None


async def test_api_failure_returns_none(monkeypatch) -> None:
    class _Boom:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            raise httpx.ConnectError('down')

        async def __aexit__(self, *args) -> None:
            return None

    monkeypatch.setattr(httpx, 'AsyncClient', _Boom)
    apify._REMAX_LOCATION_CACHE.clear()
    assert await _remax_resolve_location('Gonnet') is None


async def test_result_is_cached_second_call_skips_api(_mock_autocomplete) -> None:
    await _remax_resolve_location('Gonnet')
    await _remax_resolve_location('Gonnet')
    assert _mock_autocomplete['calls'] == 1


async def test_empty_zona_returns_none_without_api_call(_mock_autocomplete) -> None:
    assert await _remax_resolve_location('') is None
    assert _mock_autocomplete['calls'] == 0
