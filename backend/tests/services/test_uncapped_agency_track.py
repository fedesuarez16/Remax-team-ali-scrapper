"""The inmobiliarias track pages to exhaustion too — `0` means "no cap".

The portal scrapers already shipped uncapped (see test_uncapped_pagination.py),
but the agency track kept three self-imposed ceilings that had the same shape as
the old `REMAX_MAX_PAGES=5` bug — a default nobody chose, silently truncating
the sweep:

- `MAX_WEBSITE_URLS=10`  → only 10 agency/curated websites scraped per search,
  the rest dropped with no error and no event.
- `GOOGLEMAPS_MAX_PLACES=20` → discovery stopped at 20 places per zona.
- `INSTAGRAM_RESULTS_LIMIT=10` → 10 posts per profile.

`0` now means uncapped on all three, matching the convention the paging block
already documents. For the two Apify actors, uncapped means OMITTING the input
key entirely rather than sending a literal `0`: the actors read `0` as "crawl
zero items", so sending it would silence the source instead of freeing it.
"""
from typing import Any

import pytest

from app.core.config import Settings, settings
from app.graphs.extraction.nodes import route_after_review
from app.models.property import Agency, ScrapingFilters
from app.services.apify import ApifyService


async def _noop_progress(_src: str, _status: str, _count: int) -> None:
    return None


# ── shipped defaults ──────────────────────────────────────────────────────────

def test_shipped_defaults_impose_no_agency_ceiling() -> None:
    """Lo que SE ENVÍA, no lo que esta máquina tiene configurado — mismo criterio
    que `test_shipped_defaults_impose_no_page_ceiling`: capear en un despliegue
    concreto es legítimo y no debe poner en rojo un test sobre el contrato."""
    defaults = {n: f.default for n, f in Settings.model_fields.items()}
    assert defaults['MAX_WEBSITE_URLS'] == 0
    assert defaults['GOOGLEMAPS_MAX_PLACES'] == 0
    assert defaults['INSTAGRAM_RESULTS_LIMIT'] == 0


# ── MAX_WEBSITE_URLS: the website fan-out ─────────────────────────────────────

def _agency(agency_id: str) -> Agency:
    return Agency(id=agency_id, nombre=f'Agencia {agency_id}', sitio_web=f'https://{agency_id}.com')


def _manual(source_id: str) -> dict:
    return {'id': source_id, 'nombre': f'Curada {source_id}', 'url': f'https://{source_id}.com'}


def _state(n_agencies: int, n_manual: int) -> dict:
    agencies = [_agency(f'a{i}') for i in range(n_agencies)]
    return {
        'job_id': 'job-1',
        'agencies': agencies,
        'selected_agency_ids': [a.id for a in agencies],
        'manual_sources': [_manual(f'm{i}') for i in range(n_manual)],
    }


def _website_urls(sends: Any) -> list[str]:
    return [s.arg['url'] for s in sends if s.node == 'run_website_scraper']


def test_zero_scrapes_every_selected_website(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'MAX_WEBSITE_URLS', 0)
    monkeypatch.setattr(settings, 'SCRAPE_GOOGLEMAPS_ONLY', False)
    urls = _website_urls(route_after_review(_state(40, 25)))
    assert len(urls) == 65


def test_zero_keeps_curated_sources_first(monkeypatch) -> None:
    """Uncapped removes the starvation, but the priority still has to hold —
    a cap can come back via env at any time."""
    monkeypatch.setattr(settings, 'MAX_WEBSITE_URLS', 0)
    urls = _website_urls(route_after_review(_state(3, 2)))
    assert urls[:2] == ['https://m0.com', 'https://m1.com']


def test_a_positive_cap_is_still_honoured(monkeypatch) -> None:
    """The knob stays usable: capping a deployment for cost is a real need."""
    monkeypatch.setattr(settings, 'MAX_WEBSITE_URLS', 3)
    assert len(_website_urls(route_after_review(_state(10, 5)))) == 3


def test_uncapped_still_returns_no_websites_when_there_is_nothing(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'MAX_WEBSITE_URLS', 0)
    assert route_after_review(_state(0, 0)) == 'no_websites'


# ── The two Apify actor inputs ────────────────────────────────────────────────

@pytest.fixture
def actor_input(monkeypatch) -> dict:
    """Captures what would be sent to Apify, without running an actor."""
    captured: dict[str, Any] = {}

    async def _fake_run_actor(self, source, actor_id, input_data):
        captured['source'] = source
        captured['input'] = input_data
        return []

    monkeypatch.setattr(ApifyService, '_run_actor', _fake_run_actor)
    return captured


async def test_googlemaps_zero_omits_the_place_cap(actor_input, monkeypatch) -> None:
    monkeypatch.setattr(settings, 'GOOGLEMAPS_MAX_PLACES', 0)
    await ApifyService(api_token='t').scrape_agencies('City Bell', _noop_progress)
    assert 'maxCrawledPlacesPerSearch' not in actor_input['input']
    assert actor_input['input']['searchStringsArray'] == ['inmobiliarias en City Bell']


async def test_googlemaps_positive_value_is_still_sent(actor_input, monkeypatch) -> None:
    monkeypatch.setattr(settings, 'GOOGLEMAPS_MAX_PLACES', 20)
    await ApifyService(api_token='t').scrape_agencies('City Bell', _noop_progress)
    assert actor_input['input']['maxCrawledPlacesPerSearch'] == 20


async def test_instagram_profile_zero_omits_the_results_limit(actor_input, monkeypatch) -> None:
    monkeypatch.setattr(settings, 'INSTAGRAM_RESULTS_LIMIT', 0)
    await ApifyService(api_token='t').scrape_instagram_profile('remax_ok', _noop_progress)
    assert 'resultsLimit' not in actor_input['input']
    assert actor_input['input']['username'] == ['remax_ok']


async def test_instagram_profile_positive_value_is_still_sent(actor_input, monkeypatch) -> None:
    monkeypatch.setattr(settings, 'INSTAGRAM_RESULTS_LIMIT', 10)
    await ApifyService(api_token='t').scrape_instagram_profile('remax_ok', _noop_progress)
    assert actor_input['input']['resultsLimit'] == 10


# ── `_input_for`: the batch construction site, with its own hard-coded caps ───

def _filters() -> ScrapingFilters:
    return ScrapingFilters(zona='City Bell', tipo_operacion='venta')


def test_instagram_batch_input_zero_omits_the_results_limit(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'INSTAGRAM_RESULTS_LIMIT', 0)
    built = ApifyService(api_token='t')._input_for('instagram', _filters())
    assert 'resultsLimit' not in built


def test_instagram_batch_input_positive_value_is_still_sent(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'INSTAGRAM_RESULTS_LIMIT', 10)
    built = ApifyService(api_token='t')._input_for('instagram', _filters())
    assert built['resultsLimit'] == 10


def test_googlemaps_batch_input_reads_the_setting_not_a_literal(monkeypatch) -> None:
    """This branch hard-coded `maxCrawledPlacesPerSearch: 20`, so raising the
    setting left it capped anyway — the knob lied."""
    monkeypatch.setattr(settings, 'GOOGLEMAPS_MAX_PLACES', 50)
    built = ApifyService(api_token='t')._input_for('googlemaps', _filters())
    assert built['maxCrawledPlacesPerSearch'] == 50


def test_googlemaps_batch_input_zero_omits_the_place_cap(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'GOOGLEMAPS_MAX_PLACES', 0)
    built = ApifyService(api_token='t')._input_for('googlemaps', _filters())
    assert 'maxCrawledPlacesPerSearch' not in built
