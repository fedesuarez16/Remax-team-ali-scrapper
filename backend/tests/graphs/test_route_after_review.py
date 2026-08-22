"""Test-first for folding manually-registered sources (backend/app/api/v1/
manual_sources.py) into `route_after_review`'s website-scraping fan-out.

Manually-added sources (e.g. a RE/MAX office pasted into the "Fuentes" tab)
must reach the SAME `run_website_scraper` -> LLM-extraction pipeline as
Google-Maps-discovered agency websites — regardless of whether the user
selected any agency in the `agencies_review` step — and share the existing
`MAX_WEBSITE_URLS` cap with them.

The curated sources go FIRST in that shared cap. They were filed by hand for
this zona, so they outrank whatever the Google Maps actor happened to surface:
with the selector defaulting to every agency checked, putting them last meant a
zona with 10+ discovered agencies silently never scraped a single one of them.
"""
from app.core.config import settings
from app.graphs.extraction.nodes import route_after_review
from app.models.property import Agency


def _agency(agency_id: str, *, sitio_web: str | None = None) -> Agency:
    return Agency(id=agency_id, nombre=f'Agencia {agency_id}', sitio_web=sitio_web)


def test_manual_sources_included_even_with_no_agencies(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'MAX_WEBSITE_URLS', 10)
    state = {
        'job_id': 'job-1',
        'agencies': [],
        'selected_agency_ids': [],
        'manual_sources': [{'nombre': 'RE/MAX Belgrano', 'url': 'https://remax.com.ar/belgrano'}],
    }
    sends = route_after_review(state)
    assert len(sends) == 1
    assert sends[0].node == 'run_website_scraper'
    assert sends[0].arg == {'nombre': 'RE/MAX Belgrano', 'url': 'https://remax.com.ar/belgrano', 'job_id': 'job-1'}


def test_no_agencies_and_no_manual_sources_returns_no_websites(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'MAX_WEBSITE_URLS', 10)
    state = {'job_id': 'job-1', 'agencies': [], 'selected_agency_ids': [], 'manual_sources': []}
    assert route_after_review(state) == 'no_websites'


def test_manual_sources_combine_with_selected_agency_websites(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'MAX_WEBSITE_URLS', 10)
    monkeypatch.setattr(settings, 'SCRAPE_GOOGLEMAPS_ONLY', False)
    agency = _agency('a1', sitio_web='https://agencia-a1.com')
    state = {
        'job_id': 'job-1',
        'agencies': [agency],
        'selected_agency_ids': ['a1'],
        'manual_sources': [{'nombre': 'RE/MAX Belgrano', 'url': 'https://remax.com.ar/belgrano'}],
    }
    sends = route_after_review(state)
    urls_sent = sorted(s.arg['url'] for s in sends if s.node == 'run_website_scraper')
    assert urls_sent == ['https://agencia-a1.com', 'https://remax.com.ar/belgrano']


def test_manual_sources_win_the_cap_over_discovered_agencies(monkeypatch) -> None:
    """One slot, one curated source and one discovered agency → the curated one
    takes it. Hand-filed beats Google-Maps-discovered."""
    monkeypatch.setattr(settings, 'MAX_WEBSITE_URLS', 1)
    agency = _agency('a1', sitio_web='https://agencia-a1.com')
    state = {
        'job_id': 'job-1',
        'agencies': [agency],
        'selected_agency_ids': ['a1'],
        'manual_sources': [{'nombre': 'RE/MAX Belgrano', 'url': 'https://remax.com.ar/belgrano'}],
    }
    sends = route_after_review(state)
    website_sends = [s for s in sends if s.node == 'run_website_scraper']
    assert len(website_sends) == 1
    assert website_sends[0].arg['url'] == 'https://remax.com.ar/belgrano'


def test_agencies_fill_the_cap_left_over_by_manual_sources(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'MAX_WEBSITE_URLS', 2)
    agency = _agency('a1', sitio_web='https://agencia-a1.com')
    state = {
        'job_id': 'job-1',
        'agencies': [agency],
        'selected_agency_ids': ['a1'],
        'manual_sources': [
            {'nombre': 'RE/MAX Belgrano', 'url': 'https://remax.com.ar/belgrano'},
            {'nombre': 'Inmobiliaria Sur', 'url': 'https://inmosur.com.ar'},
        ],
    }
    sends = route_after_review(state)
    urls_sent = [s.arg['url'] for s in sends if s.node == 'run_website_scraper']
    assert urls_sent == ['https://remax.com.ar/belgrano', 'https://inmosur.com.ar']


def test_every_manual_source_is_sent_before_any_agency(monkeypatch) -> None:
    """The regression that started this: 10 discovered agencies checked by
    default used to eat the whole cap and drop the curated registry."""
    monkeypatch.setattr(settings, 'MAX_WEBSITE_URLS', 10)
    monkeypatch.setattr(settings, 'SCRAPE_GOOGLEMAPS_ONLY', False)
    agencies = [_agency(f'a{i}', sitio_web=f'https://agencia-a{i}.com') for i in range(10)]
    state = {
        'job_id': 'job-1',
        'agencies': agencies,
        'selected_agency_ids': [a.id for a in agencies],
        'manual_sources': [{'nombre': 'RE/MAX Belgrano', 'url': 'https://remax.com.ar/belgrano'}],
    }
    sends = route_after_review(state)
    urls_sent = [s.arg['url'] for s in sends if s.node == 'run_website_scraper']
    assert urls_sent[0] == 'https://remax.com.ar/belgrano'
    assert len(urls_sent) == 10


def test_manual_sources_missing_key_defaults_to_empty(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'MAX_WEBSITE_URLS', 10)
    state = {'job_id': 'job-1', 'agencies': [], 'selected_agency_ids': []}
    assert route_after_review(state) == 'no_websites'
