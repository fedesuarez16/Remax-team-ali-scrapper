"""The direct ZonaProp scrape loop, and the switch back to Apify.

Pagination is no longer inferred: `listStore.paging.totalPages` says how many
pages exist, so the loop stops because the portal said so rather than because a
page looked short. And membership is checked against `appliedFilters` by zone
ID instead of matching listing text, which is what used to discard Grand Bell
and Lomas de City Bell from a City Bell search.

`ZONAPROP_USE_APIFY=true` puts the actor back in charge, untouched.
"""
from typing import Any

import httpx
import pytest

from app.models.property import ScrapingFilters
from app.services.apify import ApifyService, _scrape_zonaprop_direct

_BASE = 'https://www.zonaprop.com.ar/casas-venta-city-bell-450000-500000-dolar'


def _posting(i: int, *, zone: str = '1001379', barrio: str = 'City Bell') -> dict:
    return {
        'postingId': str(i),
        'url': f'/propiedades/clasificado/casa-{i}.html',
        'title': f'Casa {i}',
        'realEstateType': {'name': 'Casas'},
        'priceOperationTypes': [
            {'prices': [{'amount': 450_000, 'currency': 'USD'}]}
        ],
        'postingLocation': {
            'address': {'name': f'Calle {i}'},
            'location': {
                'locationId': f'V1-D-{zone}', 'name': barrio,
                'parent': {'locationId': 'V1-C-1001361', 'name': 'La Plata'},
            },
        },
        'mainFeatures': {},
        'visiblePictures': {'pictures': []},
    }



def _gonnet(i: int) -> dict:
    """A posting from ANOTHER zone of the same partido: its parent chain holds
    the La Plata city id, which is exactly how it slipped through."""
    p = _posting(i, zone='1001380', barrio='Manuel B Gonnet')
    p['postingLocation']['location']['parent'] = {
        'locationId': 'V1-C-1001361', 'name': 'La Plata',
    }
    return p

def _page(state_postings: list[dict], *, total_pages: int, current: int,
          applied: tuple[str, str] = ('City Bell', '1001379')) -> str:
    import json
    state = {'listStore': {
        'paging': {'total': 42, 'totalPages': total_pages, 'currentPage': current},
        'appliedFilters': [
            {'type': 'location',
             'options': [{'label': applied[0], 'min': applied[1]}]}
        ],
        'listPostings': state_postings,
    }}
    return (
        '<html><script>window.__PRELOADED_STATE__ = '
        + json.dumps(state)
        + ';window.after = {"x": 1};</script></html>'
    )


def _filters(zona: str = 'City Bell') -> ScrapingFilters:
    return ScrapingFilters(zona=zona, zona_pedida=zona,
                           tipo_operacion='venta', tipos_propiedad=['casa'],
                           precio_min=450_000, precio_max=500_000)


async def _noop(source: str, status: str, count: int) -> None:
    return None


def _serve(monkeypatch: pytest.MonkeyPatch, pages: dict[str, str]) -> list[str]:
    asked: list[str] = []

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None: pass
        async def __aenter__(self) -> '_Client': return self
        async def __aexit__(self, *a: Any) -> None: return None

        async def get(self, url: str, *a: Any, **k: Any) -> Any:
            asked.append(url)
            body = pages.get(url, '<html>nada</html>')
            return httpx.Response(200, text=body, request=httpx.Request('GET', url))

    monkeypatch.setattr(httpx, 'AsyncClient', _Client)
    return asked


class TestPagingIsDeclaredNotGuessed:
    async def test_it_walks_exactly_the_pages_the_portal_declares(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        asked = _serve(monkeypatch, {
            f'{_BASE}.html': _page([_posting(i) for i in range(30)], total_pages=2, current=1),
            f'{_BASE}-pagina-2.html': _page(
                [_posting(i) for i in range(100, 112)], total_pages=2, current=2),
        })

        results = await _scrape_zonaprop_direct(_filters(), _noop)

        assert len(results) == 42
        assert asked == [f'{_BASE}.html', f'{_BASE}-pagina-2.html']

    async def test_a_single_page_costs_a_single_request(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The old loop had to fetch one page beyond the end to confirm it."""
        asked = _serve(monkeypatch, {
            f'{_BASE}.html': _page([_posting(i) for i in range(20)], total_pages=1, current=1),
        })

        results = await _scrape_zonaprop_direct(_filters(), _noop)

        assert len(results) == 20
        assert len(asked) == 1


class TestMembershipIsCheckedByZoneId:
    async def test_a_sub_barrio_of_the_requested_zone_is_kept(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Grand Bell is inside City Bell. Text matching threw it out; the zone
        chain keeps it."""
        _serve(monkeypatch, {
            f'{_BASE}.html': _page([
                _posting(1),
                _posting(2, barrio='Grand Bell'),
                _posting(3, barrio='Lomas de City Bell'),
            ], total_pages=1, current=1),
        })

        results = await _scrape_zonaprop_direct(_filters(), _noop)

        assert len(results) == 3

    async def test_a_posting_from_another_zone_is_dropped(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The redirect guard, now keyed on what the portal declared."""
        _serve(monkeypatch, {
            f'{_BASE}.html': _page([
                _posting(1),
                _posting(2, zone='9999999', barrio='Nueva Córdoba'),
            ], total_pages=1, current=1),
        })

        results = await _scrape_zonaprop_direct(_filters(), _noop)

        assert len(results) == 1


class TestWhenThePageIsNotAListing:
    async def test_a_wall_yields_nothing_and_does_not_crash(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _serve(monkeypatch, {f'{_BASE}.html': '<html>Acceso denegado</html>'})

        assert await _scrape_zonaprop_direct(_filters(), _noop) == []


class TestTheApifyEscapeHatch:
    async def test_the_actor_is_used_when_the_flag_is_on(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core.config import settings
        monkeypatch.setattr(settings, 'ZONAPROP_USE_APIFY', True)
        called: list[str] = []

        async def fake(self_: Any, actor_id: str, filters: ScrapingFilters) -> Any:
            from app.services.apify import ZonaPropFunnel
            called.append(actor_id)
            return [], ZonaPropFunnel(search_url='https://z/x')

        monkeypatch.setattr(ApifyService, '_scrape_zonaprop_paginated', fake)
        service = ApifyService(api_token='dummy')

        await service._scrape_source_once('zonaprop', _filters(), _noop)

        assert called, 'the actor path must still be reachable'

    async def test_the_direct_path_is_the_default(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core.config import settings
        assert settings.ZONAPROP_USE_APIFY is False

        _serve(monkeypatch, {
            f'{_BASE}.html': _page([_posting(1)], total_pages=1, current=1),
        })

        async def boom(*a: Any, **k: Any) -> Any:
            raise AssertionError('the actor must not be called by default')

        monkeypatch.setattr(ApifyService, '_scrape_zonaprop_paginated', boom)
        service = ApifyService(api_token='dummy')

        results = await service._scrape_source_once('zonaprop', _filters(), _noop)

        assert len(results) == 1


class TestTheCompositeSlugRedirect:
    """`City Bell, La Plata` slugifies to `city-bell-la-plata`, which the portal
    does not know — it answers with the containing PARTIDO instead. The actor
    path retried with the bare localidad on a guess ("did everything get
    rejected?"). Here the portal states which zone it applied, so the redirect
    is DETECTED rather than inferred."""

    async def test_it_falls_back_to_the_bare_localidad(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        composite = ('https://www.zonaprop.com.ar/'
                     'casas-venta-city-bell-la-plata-450000-500000-dolar')
        asked = _serve(monkeypatch, {
            # The portal answers the unknown slug with the partido.
            f'{composite}.html': _page(
                [_posting(i, zone='1001374', barrio='La Plata') for i in range(5)],
                total_pages=1, current=1, applied=('La Plata', '1001374')),
            f'{_BASE}.html': _page(
                [_posting(i) for i in range(20)], total_pages=1, current=1),
        })

        results = await _scrape_zonaprop_direct(_filters('City Bell, La Plata'), _noop)

        assert asked == [f'{composite}.html', f'{_BASE}.html']
        assert len(results) == 20

    async def test_a_slug_the_portal_honours_is_not_refetched(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`La Plata, La Plata` IS a real ZonaProp slug — do not pay twice."""
        url = ('https://www.zonaprop.com.ar/'
               'casas-venta-la-plata-la-plata-450000-500000-dolar')
        asked = _serve(monkeypatch, {
            f'{url}.html': _page(
                [_posting(i, zone='1001374', barrio='La Plata') for i in range(8)],
                total_pages=1, current=1, applied=('La Plata', '1001374')),
        })

        results = await _scrape_zonaprop_direct(_filters('La Plata, La Plata'), _noop)

        assert asked == [f'{url}.html']
        assert len(results) == 8


class TestABroadUrlIsNarrowedToTheRequestedZone:
    """Measured live: `casas-venta-city-bell-la-plata-...` applies TWO filters —
    `La Plata` (type **city**, 1001361) AND `City Bell` (type zone, 1001379) —
    and answers 5.967 listings, Gonnet and Villa Elisa among them, against 848
    on `casas-venta-city-bell-...`.

    Two defences, and the ORDER matters. The union is first collapsed onto the
    narrow URL, so the crawl pays for 848 listings instead of paging through
    5.967. The zone-id filter then still runs on whatever comes back — a
    narrowed page is not a trusted page, and only the applied zone that
    matches what the user ASKED for may be used; the containing city is a
    widening, not the request.
    """

    async def test_a_union_is_collapsed_and_then_still_filtered(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        union = ('https://www.zonaprop.com.ar/'
                 'casas-venta-city-bell-la-plata-450000-500000-dolar')
        import json
        state = {'listStore': {
            'paging': {'total': 5, 'totalPages': 1, 'currentPage': 1},
            'appliedFilters': [{'type': 'location', 'options': [
                {'label': 'La Plata', 'type': 'city', 'min': '1001361'},
                {'label': 'City Bell', 'type': 'zone', 'min': '1001379'},
            ]}],
            'listPostings': [_posting(1), _gonnet(2)],
        }}
        asked = _serve(monkeypatch, {
            f'{union}.html': '<html><script>window.__PRELOADED_STATE__ = '
                             + json.dumps(state) + ';window.x={};</script></html>',
            # The narrow URL the portal's own label builds. Gonnet rides along
            # here too (same partido in its parent chain) and must still go.
            f'{_BASE}.html': _page([
                _posting(1),                        # City Bell
                _posting(2, barrio='Grand Bell'),   # sub-barrio, se queda
                _gonnet(3), _gonnet(4), _gonnet(5),  # otra zona del partido
            ], total_pages=1, current=1),
        })

        results = await _scrape_zonaprop_direct(_filters('City Bell, La Plata'), _noop)

        assert asked == [f'{union}.html', f'{_BASE}.html']
        assert len(results) == 2
        assert all('Calle' in p.direccion for p in results)

    async def test_a_city_level_request_keeps_the_city(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Asking for "La Plata, La Plata" SHOULD match the city filter — the
        rule is "the zone you asked for", not "never a city"."""
        url = ('https://www.zonaprop.com.ar/'
               'casas-venta-la-plata-la-plata-450000-500000-dolar')
        import json
        state = {'listStore': {
            'paging': {'total': 2, 'totalPages': 1, 'currentPage': 1},
            'appliedFilters': [{'type': 'location', 'options': [
                {'label': 'La Plata', 'type': 'city', 'min': '1001361'},
            ]}],
            'listPostings': [_gonnet(1), _gonnet(2)],
        }}
        _serve(monkeypatch, {
            f'{url}.html': '<html><script>window.__PRELOADED_STATE__ = '
                           + json.dumps(state) + ';window.x={};</script></html>',
        })

        results = await _scrape_zonaprop_direct(_filters('La Plata, La Plata'), _noop)

        assert len(results) == 2


class TestAZoneTheUserNeverAskedForIsNotKept:
    """`_zonaprop_requested_zone_ids` returns `None` with a precise meaning:
    the portal DECLARED zones and not one of them is the request — i.e. it
    served somewhere else. Collapsing that into an empty set (`... or set()`)
    turned the sentinel inside out: empty means "no filter" downstream, so the
    one signal that says "this is not your zona" made the loop keep the whole
    partido.

    Live: a Gonnet search came back with ~300 La Plata houses — City Bell,
    Tolosa, Villa Elisa — because the bare `gonnet` slug redirects too, so the
    composite retry (which fires once, on page 1) had already been spent.
    """

    def _partido_page(self, postings: list[dict], *, total_pages: int = 1) -> str:
        return _page(postings, total_pages=total_pages, current=1,
                     applied=('La Plata', '1001361'))

    async def test_a_plain_slug_the_portal_redirects_keeps_nothing(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No comma in the request, so the composite retry never applies —
        this is line 782 on its own."""
        url = 'https://www.zonaprop.com.ar/casas-venta-gonnet-450000-500000-dolar'
        _serve(monkeypatch, {
            f'{url}.html': self._partido_page([_gonnet(1), _posting(2), _posting(3)]),
        })

        results = await _scrape_zonaprop_direct(_filters('Gonnet'), _noop)

        assert results == []

    async def test_a_composite_whose_retry_also_redirects_keeps_nothing(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The retry is spent on page 1; the second pass must still refuse the
        partido instead of falling through to "keep everything"."""
        composite = ('https://www.zonaprop.com.ar/'
                     'casas-venta-gonnet-la-plata-450000-500000-dolar')
        plain = 'https://www.zonaprop.com.ar/casas-venta-gonnet-450000-500000-dolar'
        asked = _serve(monkeypatch, {
            f'{composite}.html': self._partido_page([_posting(i) for i in range(3)]),
            f'{plain}.html': self._partido_page([_posting(i) for i in range(10, 20)]),
        })

        results = await _scrape_zonaprop_direct(_filters('Gonnet, La Plata'), _noop)

        assert asked == [f'{composite}.html', f'{plain}.html']
        assert results == []

    async def test_it_does_not_page_through_a_listing_it_will_reject_whole(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A redirect is settled on page 1. Paying for 40 more pages of a
        partido dump we reject in full is spend for nothing."""
        url = 'https://www.zonaprop.com.ar/casas-venta-gonnet-450000-500000-dolar'
        asked = _serve(monkeypatch, {
            f'{url}.html': self._partido_page([_posting(1)], total_pages=40),
        })

        await _scrape_zonaprop_direct(_filters('Gonnet'), _noop)

        assert asked == [f'{url}.html']


# ── A ZonaProp slug names ONE location. Joining barrio and partido does not
# narrow: when BOTH halves resolve, the portal reads the slug as a UNION and
# throws the partido in. Verified live 2026-08-31 (appliedFilters / result
# counts):
#
#   manuel-b-gonnet-la-plata → [La Plata (city), Manuel B Gonnet (zone)] 4.572
#   manuel-b-gonnet          → [Manuel B Gonnet (zone)]                    397
#   city-bell-la-plata       → [La Plata (city), City Bell (zone)]       5.967
#   city-bell                → [City Bell (zone)]                          848
#   villa-elisa-la-plata     → [Villa Elisa (zone)]                        432
#   la-plata-la-plata        → [La Plata (zone)]                         1.844
#   gonnet-la-plata          → []  → redirects to /casas-venta.html    171.067
#
# So "composite" is NOT the tell — `villa-elisa-la-plata` and `la-plata-la-plata`
# are canonical single locations. The tell is what the portal DECLARED. ───────

def _multi_page(postings: list[dict], options: list[dict], *, total_pages: int = 1) -> str:
    import json
    return ('<html><script>window.__PRELOADED_STATE__ = ' + json.dumps({'listStore': {
        'paging': {'total': len(postings), 'totalPages': total_pages, 'currentPage': 1},
        'appliedFilters': [{'type': 'location', 'options': options}],
        'listPostings': postings,
    }}) + ';window.x={};</script></html>')


_LA_PLATA_CITY = {'label': 'La Plata', 'type': 'city', 'min': '1001361'}
_GONNET_ZONE = {'label': 'Manuel B Gonnet', 'type': 'zone', 'min': '1001377'}


def _gonnet_posting(i: int) -> dict:
    return _posting(i, zone='1001377', barrio='Manuel B Gonnet')


class TestAUnionSlugIsNarrowedToThePortalsOwnName:
    """The retry no longer guesses at the user's text — it reads back the
    portal's OWN label for the zone that was asked for, which is what its
    canonical URLs slugify. That is how `manuel-b-gonnet` is recovered from a
    request spelled "Manuel B Gonnet, La Plata", with no hardcoded vocabulary.
    """

    async def test_a_union_is_refetched_on_the_matched_zones_own_slug(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        union = ('https://www.zonaprop.com.ar/'
                 'casas-venta-manuel-b-gonnet-la-plata-450000-500000-dolar')
        narrow = ('https://www.zonaprop.com.ar/'
                  'casas-venta-manuel-b-gonnet-450000-500000-dolar')
        asked = _serve(monkeypatch, {
            # The union: the partido rides along, so most of the page is not Gonnet.
            f'{union}.html': _multi_page(
                [_gonnet_posting(1)] + [_posting(i) for i in range(2, 30)],
                [_LA_PLATA_CITY, _GONNET_ZONE], total_pages=46),
            f'{narrow}.html': _multi_page(
                [_gonnet_posting(i) for i in range(100, 108)], [_GONNET_ZONE]),
        })

        results = await _scrape_zonaprop_direct(
            _filters('Manuel B Gonnet, La Plata'), _noop)

        assert asked == [f'{union}.html', f'{narrow}.html']
        assert len(results) == 8

    async def test_a_canonical_composite_is_left_alone(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`villa-elisa-la-plata` disambiguates the Entre Ríos homonym and
        applies exactly ONE option — narrowing it would break the search."""
        url = ('https://www.zonaprop.com.ar/'
               'casas-venta-villa-elisa-la-plata-450000-500000-dolar')
        asked = _serve(monkeypatch, {
            f'{url}.html': _multi_page(
                [_posting(i, zone='1001369', barrio='Villa Elisa') for i in range(6)],
                [{'label': 'Villa Elisa', 'type': 'zone', 'min': '1001369'}]),
        })

        results = await _scrape_zonaprop_direct(_filters('Villa Elisa, La Plata'), _noop)

        assert asked == [f'{url}.html']
        assert len(results) == 6


class TestASlugThePortalCannotResolveAtAll:
    """`gonnet-la-plata` resolves to NOTHING: the portal drops the location
    filter and serves 171.067 houses across Argentina. An empty
    `appliedFilters` is therefore not "no evidence, keep everything" — with a
    zona requested it is proof the slug was never understood."""

    async def test_a_nationwide_dump_keeps_nothing(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        composite = 'https://www.zonaprop.com.ar/casas-venta-gonnet-la-plata-450000-500000-dolar'
        plain = 'https://www.zonaprop.com.ar/casas-venta-gonnet-450000-500000-dolar'
        _serve(monkeypatch, {
            f'{composite}.html': _multi_page(
                [_posting(i, zone='9999999', barrio='Nueva Córdoba') for i in range(30)], []),
            f'{plain}.html': _multi_page(
                [_posting(i, zone='9999999', barrio='Rosario') for i in range(30)], []),
        })

        results = await _scrape_zonaprop_direct(_filters('Gonnet, La Plata'), _noop)

        assert results == []
