"""Test-first for `app.services.barrio_probe` — "how does this barrio figure on
each portal?", answered by asking the portals, not by guessing.

WHAT THE PROBE IS FOR. A gated community resolves differently on every portal,
and the differences are not cosmetic — they decide whether a search returns 42
listings or 172.141. Measured live (2026-09-04):

    RE/MAX        privatecommunityId 2439 → locations `in::::::2439:`
    Argenprop     CodigoBarrio GRAND-BELL, under the SYNTHETIC localidad
                  LA-PLATA-COUNTRIES-BARRIOS-CERRADOS
    ZonaProp      /casas-venta-grand-bell.html → 301 → -grand-bell-city-bell-la-plata, 42
    MercadoLibre  /casas/venta/grand-bell/ → 301 → bsas-gba-sur/la-plata/grand-bell/, 29
    InmoBusqueda  barrio_id 9 → NO page; /propiedades-grand-bell.html drops the zona
    Mudafy        no country pages at all — every URL 404s

So the probe's job is to record, per portal, WHICH of two strategies applies:
`native` (the portal owns the entity; filter server-side) or `localidad`
(degrade to the containing localidad and filter by alias/polygon). Getting
that wrong in the `localidad` direction costs recall; getting it wrong in the
`native` direction serves the whole country as if it were the answer.

The probe never guesses a `native` ref. If the portal's own resolver declines,
the answer is `localidad` — which is always CORRECT, just wider.
"""
from __future__ import annotations

import pytest

from app.services import apify, barrio_probe
from app.services.barrio_probe import PortalRef, probe_barrio


@pytest.fixture(autouse=True)
def _sin_caches():
    apify._REMAX_LOCATION_CACHE.clear()
    apify._ARGENPROP_SLUG_CACHE.clear()
    apify._INMOBUSQUEDA_SLUG_CACHE.clear()
    yield
    apify._REMAX_LOCATION_CACHE.clear()
    apify._ARGENPROP_SLUG_CACHE.clear()
    apify._INMOBUSQUEDA_SLUG_CACHE.clear()


def _stub(monkeypatch, *, remax=None, argenprop=None, inmobusqueda=None,
          century21=None) -> None:
    """Stand in for the four portal resolvers the probe calls.

    Every one of them is already covered by its own test module; the probe's
    contract is what it MAKES of their answers, so stubbing them keeps this
    file about that and off the network (`conftest.no_real_network`).
    """
    async def _r(_zona): return remax
    async def _a(_zona): return argenprop
    async def _i(_zona): return inmobusqueda
    async def _c(_zona): return century21

    monkeypatch.setattr(barrio_probe, '_remax_resolve_location', _r)
    monkeypatch.setattr(barrio_probe, '_argenprop_resolve_zona_slug', _a)
    monkeypatch.setattr(barrio_probe, '_inmobusqueda_resolve_zona_slug', _i)
    monkeypatch.setattr(barrio_probe, '_c21_resolve_location', _c)


def _by_portal(refs: list[PortalRef]) -> dict[str, PortalRef]:
    return {r.portal: r for r in refs}


class TestEveryPortalGetsARow:
    async def test_all_portal_sources_are_reported(self, monkeypatch):
        """A missing row is indistinguishable from "not probed yet" in the UI.
        A portal that cannot resolve gets an explicit `localidad` row."""
        _stub(monkeypatch)
        refs = _by_portal(await probe_barrio('Grand Bell', 'City Bell, La Plata'))
        assert set(refs) == set(apify.PORTAL_SOURCES)

    async def test_a_portal_that_declines_falls_back_to_localidad(self, monkeypatch):
        _stub(monkeypatch)
        refs = _by_portal(await probe_barrio('Grand Bell', 'City Bell, La Plata'))
        assert refs['remax'].strategy == 'localidad'
        assert refs['remax'].ref is None


class TestNativeResolution:
    async def test_remax_private_community_is_native(self, monkeypatch):
        """Slot 5 populated → the portal owns the entity."""
        _stub(monkeypatch, remax='in::::::2439:')
        refs = _by_portal(await probe_barrio('Grand Bell', 'City Bell, La Plata'))
        assert refs['remax'].strategy == 'native'
        assert refs['remax'].ref == 'in::::::2439:'

    async def test_remax_city_slot_is_NOT_a_barrio_match(self, monkeypatch):
        """`in::::1067:::` is Manuel B Gonnet the LOCALIDAD. Accepting it as
        the barrio's ref would filter a Grand Bell search down to... all of
        Gonnet, and call it precise. The slot is the tell: only slot 5
        (`privatecommunityId`) and slot 4 (`neighborhoodId`) name a place
        smaller than a localidad."""
        _stub(monkeypatch, remax='in::::1067:::')
        refs = _by_portal(await probe_barrio('Grand Bell', 'City Bell, La Plata'))
        assert refs['remax'].strategy == 'localidad'

    async def test_argenprop_barrio_code_is_native(self, monkeypatch):
        _stub(monkeypatch, argenprop='los-ceibos-lp')
        refs = _by_portal(await probe_barrio('Los Ceibos', 'City Bell, La Plata'))
        assert refs['argenprop'].strategy == 'native'
        assert refs['argenprop'].ref == 'los-ceibos-lp'

    async def test_inmobusqueda_barrio_shaped_country_is_localidad(self, monkeypatch):
        """Grand Bell has `barrio_id 9` and no page — the resolver returns
        None, and that is the CORRECT answer, not a failure."""
        _stub(monkeypatch, inmobusqueda=None)
        refs = _by_portal(await probe_barrio('Grand Bell', 'City Bell, La Plata'))
        assert refs['inmobusqueda'].strategy == 'localidad'

    async def test_inmobusqueda_localidad_shaped_country_is_native(self, monkeypatch):
        """Haras del Sur has `barrio_id 0` and a real page."""
        _stub(monkeypatch, inmobusqueda='haras-del-sur')
        refs = _by_portal(await probe_barrio('Haras del Sur', 'La Plata'))
        assert refs['inmobusqueda'].strategy == 'native'
        assert refs['inmobusqueda'].ref == 'haras-del-sur'


class TestSlugBuiltPortals:
    """ZonaProp and MercadoLibre have no autocomplete we can reach without the
    metered residential proxy, but they DO canonicalise a bare slug — measured:
    `/casas-venta-grand-bell.html` 301s to `-grand-bell-city-bell-la-plata`
    (42 listings), `/casas/venta/grand-bell/` to `bsas-gba-sur/la-plata/
    grand-bell/` (29). So the probe reports the slug the URL builder WILL use,
    marked native but UNCONFIRMED — a claim for a human to check, never a
    verified fact."""

    async def test_zonaprop_reports_the_bare_slug(self, monkeypatch):
        _stub(monkeypatch)
        refs = _by_portal(await probe_barrio('Grand Bell', 'City Bell, La Plata'))
        assert refs['zonaprop'].strategy == 'native'
        assert refs['zonaprop'].ref == 'grand-bell'

    async def test_mercadolibre_reports_the_bare_slug(self, monkeypatch):
        _stub(monkeypatch)
        refs = _by_portal(await probe_barrio('Grand Bell', 'City Bell, La Plata'))
        assert refs['mercadolibre'].ref == 'grand-bell'

    async def test_the_slug_drops_the_kind_prefix(self, monkeypatch):
        """"Club de Campo Los Ceibos" must slug to `los-ceibos`. The kind is
        not part of the URL on any portal — `/casas-venta-barrio-grand-bell.html`
        falls through to the NATIONWIDE listing (172.141 results, measured)."""
        _stub(monkeypatch)
        refs = _by_portal(await probe_barrio('Club de Campo Los Ceibos', 'City Bell, La Plata'))
        assert refs['zonaprop'].ref == 'los-ceibos'

    async def test_slug_built_refs_are_never_confirmed(self, monkeypatch):
        _stub(monkeypatch)
        refs = _by_portal(await probe_barrio('Grand Bell', 'City Bell, La Plata'))
        assert refs['zonaprop'].confirmed is False


class TestMudafyHasNoCountries:
    async def test_mudafy_is_always_localidad(self, monkeypatch):
        """Every gated-community URL 404s (measured 2026-09-04). Reporting a
        `native` slug for it would send the scraper at a dead page every
        search."""
        _stub(monkeypatch)
        refs = _by_portal(await probe_barrio('Grand Bell', 'City Bell, La Plata'))
        assert refs['mudafy'].strategy == 'localidad'
        assert refs['mudafy'].ref is None


class TestTheProbeIsHonestAboutFailure:
    async def test_a_resolver_that_raises_becomes_localidad(self, monkeypatch):
        """A transient portal outage must not be written down as "this barrio
        does not exist there" — `localidad` is the safe answer, and the note
        says why so a re-probe is an obvious next step."""
        async def _boom(_zona):
            raise RuntimeError('portal caído')

        _stub(monkeypatch)
        monkeypatch.setattr(barrio_probe, '_argenprop_resolve_zona_slug', _boom)
        refs = _by_portal(await probe_barrio('Grand Bell', 'City Bell, La Plata'))
        assert refs['argenprop'].strategy == 'localidad'
        assert 'error' in (refs['argenprop'].note or '').lower()

    async def test_every_row_carries_a_human_readable_note(self, monkeypatch):
        """The review screen shows the note next to the label. A row with no
        explanation is a row nobody can confirm."""
        _stub(monkeypatch, remax='in::::::2439:')
        for ref in await probe_barrio('Grand Bell', 'City Bell, La Plata'):
            assert ref.note

    async def test_the_probe_walks_the_forms_most_qualified_first(self, monkeypatch):
        """The composite goes first and the bare name last, always.

        Both ends matter. Leading with the bare name walks straight into the
        homonym problem — Argenprop's first "Los Ceibos" is in TIGRE. But
        stopping AT the composite was the bug this cascade fixes: the portals
        disagree about which level a gated community hangs off (Argenprop files
        it under the partido, InmoBusqueda under the localidad), and both
        resolvers demand every comma part appear in the label, so one query
        shape can never satisfy both.
        """
        visto: list[str] = []

        async def _spy(zona):
            visto.append(zona)
            return None

        _stub(monkeypatch)
        monkeypatch.setattr(barrio_probe, '_argenprop_resolve_zona_slug', _spy)
        await probe_barrio('Los Ceibos', 'City Bell, La Plata')
        assert visto == [
            'Los Ceibos, City Bell, La Plata',
            'Los Ceibos, La Plata',
            'Los Ceibos',
        ]

    async def test_a_native_hit_stops_the_walk(self, monkeypatch):
        """Each form is a live request. Once a portal has answered with the
        barrio, asking it a vaguer question can only find something worse."""
        visto: list[str] = []

        async def _spy(zona):
            visto.append(zona)
            return 'los-ceibos-lp'

        _stub(monkeypatch)
        monkeypatch.setattr(barrio_probe, '_argenprop_resolve_zona_slug', _spy)
        await probe_barrio('Los Ceibos', 'City Bell, La Plata')
        assert visto == ['Los Ceibos, City Bell, La Plata']

    async def test_a_later_form_can_rescue_a_native_ref(self, monkeypatch):
        """The measured Argenprop case: the three-part composite matches
        nothing because the portal's label reads "Grand Bell, Partido de La
        Plata" — no City Bell in it — while "Grand Bell, La Plata" resolves to
        `CodigoBarrio=GRAND-BELL`. Before the cascade this portal was recorded
        as `localidad` for a barrio it owns."""
        async def _spy(zona):
            return 'grand-bell' if zona == 'Grand Bell, La Plata' else None

        _stub(monkeypatch)
        monkeypatch.setattr(barrio_probe, '_argenprop_resolve_zona_slug', _spy)
        refs = _by_portal(await probe_barrio('Grand Bell', 'City Bell, La Plata'))
        assert refs['argenprop'].strategy == 'native'
        assert refs['argenprop'].ref == 'grand-bell'
