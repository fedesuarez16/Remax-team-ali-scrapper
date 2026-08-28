"""`APIFY_DISABLED` means "skip the Apify actors", not "skip ZonaProp".

The flag's whole point is to keep searching with the sources that talk to the
portals directly when Apify is down, out of credits, or rate-limiting — which
is exactly the situation that motivated moving ZonaProp off the actor. Leaving
it out of that list would drop the portal precisely when the escape hatch is
being used for.

It only belongs there while it IS direct: with `ZONAPROP_USE_APIFY=true` it
runs through the actor again and `APIFY_DISABLED` must still exclude it.
"""
import pytest

from app.graphs.extraction.nodes import _env_allowed_sources


@pytest.fixture()
def apify_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, 'APIFY_DISABLED', True)
    monkeypatch.setattr(settings, 'SCRAPE_GOOGLEMAPS_ONLY', False)
    monkeypatch.setattr(settings, 'SCRAPE_ZONAPROP_ONLY', False)


def test_zonaprop_survives_apify_being_disabled(
    apify_off: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, 'ZONAPROP_USE_APIFY', False)

    assert 'zonaprop' in _env_allowed_sources()


def test_the_actual_actors_stay_out(apify_off: None) -> None:
    sources = _env_allowed_sources()
    assert 'googlemaps' not in sources
    assert 'instagram' not in sources


def test_the_other_direct_portals_are_untouched(apify_off: None) -> None:
    sources = _env_allowed_sources()
    for direct in ('mercadolibre', 'inmobusqueda', 'mudafy'):
        assert direct in sources


def test_zonaprop_is_excluded_again_when_it_goes_back_to_the_actor(
    apify_off: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, 'ZONAPROP_USE_APIFY', True)

    assert 'zonaprop' not in _env_allowed_sources()
