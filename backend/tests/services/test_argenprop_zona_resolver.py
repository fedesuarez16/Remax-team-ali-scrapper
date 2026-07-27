"""Argenprop zona → portal slug resolution via the portal's OWN location
autocomplete API (https://api.sosiva451.com/Ubicaciones/buscar?stringBusqueda=,
the endpoint argenprop.com's search box calls — public, no WAF, verified
live). This is what makes "Gonnet" actually work: Argenprop's slug is
`manuel-gonnet`, and an unknown slug 301s to the nationwide listing.

Selection heuristic — verified against real API responses: results are
ordered by `Importancia`, which can rank a fuzzy match first (real case:
"villa elisa" returns "Barrio Villa Felisa, San Lorenzo" ABOVE "Villa Elisa,
Partido de La Plata"), so the first result whose slugified `label` contains
EVERY comma-part of the query wins — never the raw first result. The slug is
the lowercased `CodigoBarrio` (when present) or `CodigoLocalidad`.
"""
import httpx
import pytest

from app.services import apify
from app.services.apify import _argenprop_resolve_zona_slug


def _entry(label: str, *, barrio: str | None = None, localidad: str = 'X') -> dict:
    value: dict = {'CodigoLocalidad': localidad}
    if barrio is not None:
        value['CodigoBarrio'] = barrio
    return {'label': label, 'value': value}


_RESPONSES: dict[str, list[dict]] = {
    'gonnet': [
        _entry('Manuel B Gonnet, Partido de La Plata', localidad='MANUEL-GONNET'),
        _entry('Countries y Barrios Cerrados en Gonnet, Partido de La Plata',
               localidad='GONNET-COUNTRIES-BARRIOS-CERRADOS'),
    ],
    'villa elisa': [
        _entry('Barrio Villa Felisa, San Lorenzo', barrio='BR-VILLA-FELISA',
               localidad='SAN-LORENZO-DEPARTAMENTO-DE-SAN-LORENZO'),
        _entry('Villa Elisa, Partido de La Plata', localidad='VILLA-ELISA'),
    ],
    'palermo': [
        _entry('Palermo, Capital Federal', barrio='PALERMO', localidad='CAPITAL-FEDERAL'),
        _entry('Barrio Parque, Palermo', barrio='PALERMO', localidad='CAPITAL-FEDERAL'),
    ],
}


@pytest.fixture(autouse=True)
def _mock_autocomplete(monkeypatch):
    captured: dict = {'calls': 0, 'queries': []}

    class _FakeResponse:
        def __init__(self, payload: list) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> list:
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
            q = (params or {}).get('stringBusqueda', '')
            captured['queries'].append(q)
            return _FakeResponse(_RESPONSES.get(q.lower(), []))

    monkeypatch.setattr(httpx, 'AsyncClient', _FakeAsyncClient)
    apify._ARGENPROP_SLUG_CACHE.clear()
    return captured


async def test_resolves_gonnet_to_portal_localidad_code() -> None:
    assert await _argenprop_resolve_zona_slug('Gonnet') == 'manuel-gonnet'


async def test_prefers_exact_label_match_over_higher_importance_fuzzy() -> None:
    # Real API behavior: "Villa Felisa" (fuzzy) outranks "Villa Elisa" by
    # Importancia — label matching must skip it.
    assert await _argenprop_resolve_zona_slug('Villa Elisa') == 'villa-elisa'


async def test_barrio_code_wins_over_localidad_code_when_present() -> None:
    assert await _argenprop_resolve_zona_slug('Palermo') == 'palermo'


async def test_composite_zona_requires_every_comma_part_in_label(_mock_autocomplete) -> None:
    # Query is sent with the first comma part only; ALL parts must appear in
    # the winning label ("Villa Elisa" exists in other provinces too).
    assert await _argenprop_resolve_zona_slug('Villa Elisa, La Plata') == 'villa-elisa'
    assert _mock_autocomplete['queries'] == ['Villa Elisa']


async def test_no_matching_label_returns_none() -> None:
    assert await _argenprop_resolve_zona_slug('Gonnet, Cordoba') is None


async def test_api_failure_returns_none(monkeypatch) -> None:
    class _Boom:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            raise httpx.ConnectError('down')

        async def __aexit__(self, *args) -> None:
            return None

    monkeypatch.setattr(httpx, 'AsyncClient', _Boom)
    apify._ARGENPROP_SLUG_CACHE.clear()
    assert await _argenprop_resolve_zona_slug('Gonnet') is None


async def test_result_is_cached_second_call_skips_api(_mock_autocomplete) -> None:
    await _argenprop_resolve_zona_slug('Gonnet')
    await _argenprop_resolve_zona_slug('Gonnet')
    assert _mock_autocomplete['calls'] == 1


async def test_empty_zona_returns_none_without_api_call(_mock_autocomplete) -> None:
    assert await _argenprop_resolve_zona_slug('') is None
    assert _mock_autocomplete['calls'] == 0
