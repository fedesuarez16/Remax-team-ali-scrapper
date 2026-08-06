"""InmoBusqueda zona → portal slug resolution via the portal's OWN location
autocomplete API (https://www.inmobusqueda.com.ar/configubicacion/autocomplete.json.php
?partido=1&valor=, the endpoint inmobusqueda.com.ar's search box calls —
public, unauthenticated, no WAF, verified live).

Why a resolver instead of slugifying the query: an unknown slug does NOT 404,
it renders the listing with the zona silently dropped (the `<title>` comes back
as a bare "Propiedades  - InmoBusqueda"), so guessing returns nationwide
results that look perfectly valid. Verified live: `propiedades-gonnet.html`
is exactly that trap — the real slug is `manuel-b-gonnet`.

Selection heuristic — every case below is a real API response:
1. Candidates are entries whose slugified full `name` contains EVERY comma-part
   of the query.
2. The winner is the first candidate whose FIRST name-component matches the
   query head exactly. This is what stops "city bell" resolving to "Lomas de
   City Bell" (which the API ranks first, and whose page comes back with an
   empty zona), and what makes "palermo" pick CABA's Palermo instead of the
   homonym in Partido de Anta, Salta.
3. With no exact head match, the first candidate wins — that is what resolves
   "gonnet" to "Manuel B Gonnet".

The slug itself is the slugified first component: "La Plata (Casco Urbano),
Pdo. de La Plata, Buenos Aires" → `la-plata-casco-urbano`.
"""
import httpx
import pytest

from app.services import apify
from app.services.apify import _inmobusqueda_resolve_zona_slug


def _entry(name: str, localidad_id: str | int = '23244') -> dict:
    return {'name': name, 'provincia_id': '1', 'partido_id': '68',
            'localidad_id': localidad_id, 'barrio_id': 0}


# Captured verbatim from the live autocomplete endpoint.
_RESPONSES: dict[str, list[dict]] = {
    'gonnet': [
        _entry('Manuel B Gonnet, Pdo. de La Plata, Buenos Aires'),
    ],
    'city bell': [
        _entry('Lomas de City Bell, City Bell, Pdo. de La Plata, Buenos Aires'),
        _entry('City Bell, Pdo. de La Plata, Buenos Aires'),
    ],
    'palermo': [
        _entry('Palermo Bajo, Córdoba, dpto. de Cordoba Capital, Córdoba'),
        _entry('Alto Palermo, Córdoba, dpto. de Cordoba Capital, Córdoba'),
        _entry('Palermo, Capital Federal, Capital Federal, Capital Federal'),
        _entry('Palermo Chico, Capital Federal, Capital Federal, Capital Federal'),
    ],
    'la plata': [
        # The API ranks this form-only option first; `localidad_id: 0` marks it.
        _entry('Todo el Partido de La Plata, Buenos Aires', localidad_id=0),
        _entry('La Plata (Casco Urbano), Pdo. de La Plata, Buenos Aires'),
        _entry('City Bell, Pdo. de La Plata, Buenos Aires', localidad_id='23250'),
    ],
    'villa elisa': [
        _entry('Villa Elisa, Pdo. de La Plata, Buenos Aires'),
        _entry('Villa Elisa, dpto. de Colon, Entre Ríos'),
    ],
}


@pytest.fixture(autouse=True)
def _stub_autocomplete(monkeypatch):
    apify._INMOBUSQUEDA_SLUG_CACHE.clear()

    class _Resp:
        def __init__(self, payload): self._payload = payload
        def raise_for_status(self): pass
        def json(self): return self._payload

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, params=None):
            valor = (params or {}).get('valor', '')
            return _Resp(_RESPONSES.get(valor.strip().lower(), []))

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: _Client())
    yield
    apify._INMOBUSQUEDA_SLUG_CACHE.clear()


@pytest.mark.asyncio
async def test_gonnet_resolves_to_the_full_official_name():
    """The whole reason the resolver exists: `propiedades-gonnet.html` renders
    a zona-less nationwide page, `propiedades-manuel-b-gonnet.html` does not."""
    assert await _inmobusqueda_resolve_zona_slug('Gonnet') == 'manuel-b-gonnet'


@pytest.mark.asyncio
async def test_exact_head_match_beats_the_api_ordering():
    """The API ranks "Lomas de City Bell" above "City Bell"; the slug for the
    former comes back with an empty zona, so the exact head match must win."""
    assert await _inmobusqueda_resolve_zona_slug('City Bell') == 'city-bell'


@pytest.mark.asyncio
async def test_homonym_in_another_province_is_not_picked():
    """"Palermo" matches a Córdoba barrio and a Salta partido before CABA's."""
    assert await _inmobusqueda_resolve_zona_slug('Palermo') == 'palermo'


@pytest.mark.asyncio
async def test_parenthesised_name_slugifies():
    """Also covers skipping the "Todo el Partido de …" option the API ranks
    first: it carries `localidad_id: 0` because the search box submits it by id
    and it has no listing page — its slug renders the nationwide trap page."""
    assert await _inmobusqueda_resolve_zona_slug('La Plata') == 'la-plata-casco-urbano'


@pytest.mark.asyncio
async def test_comma_parts_narrow_the_candidates():
    """A caller that knows the partido can disambiguate two homonyms."""
    assert await _inmobusqueda_resolve_zona_slug('Villa Elisa, Entre Ríos') == 'villa-elisa'
    apify._INMOBUSQUEDA_SLUG_CACHE.clear()
    assert await _inmobusqueda_resolve_zona_slug('Villa Elisa, La Plata') == 'villa-elisa'


@pytest.mark.asyncio
async def test_unknown_zona_returns_none():
    assert await _inmobusqueda_resolve_zona_slug('Ciudad Inexistente') is None


@pytest.mark.asyncio
async def test_blank_zona_returns_none():
    assert await _inmobusqueda_resolve_zona_slug('   ') is None


@pytest.mark.asyncio
async def test_result_is_cached_so_a_fan_out_hits_the_api_once():
    calls: list[str] = []

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return _RESPONSES['gonnet']

    class _Counting:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, params=None):
            calls.append((params or {}).get('valor', ''))
            return _Resp()

    import app.services.apify as mod
    mod.httpx.AsyncClient = lambda **kw: _Counting()  # type: ignore[assignment]
    assert await _inmobusqueda_resolve_zona_slug('Gonnet') == 'manuel-b-gonnet'
    assert await _inmobusqueda_resolve_zona_slug('Gonnet') == 'manuel-b-gonnet'
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_api_failure_is_not_cached(monkeypatch):
    """A transient outage must not pin `None` for the rest of the process."""
    class _Boom:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **kw): raise httpx.ConnectError('down')

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: _Boom())
    assert await _inmobusqueda_resolve_zona_slug('Gonnet') is None
    assert 'gonnet' not in apify._INMOBUSQUEDA_SLUG_CACHE
