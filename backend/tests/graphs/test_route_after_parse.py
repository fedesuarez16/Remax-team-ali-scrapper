"""Test-first for `route_after_parse` fan-out unit (T-6.1) — written BEFORE
the localidad branch lands in `nodes.py`, so the localidades-present
assertions MUST fail until T-6.2 lands.

Uses `settings.SCRAPE_ZONAPROP_ONLY = True` to pin `sources = ('zonaprop',)`
and skip agency-discovery `Send`s, so the assertions can count `Send`s
deterministically without depending on `PORTAL_SOURCES`'s exact membership.
"""
from app.core.config import settings
from app.graphs.extraction.nodes import route_after_parse
from app.models.property import ScrapingFilters


def test_localidades_present_fans_out_per_localidad_not_per_barrio(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'SCRAPE_ZONAPROP_ONLY', True)
    monkeypatch.setattr(settings, 'APIFY_DISABLED', False)
    filters = ScrapingFilters(zonas=['Palermo', 'Chacarita'])
    state = {
        'clarification_needed': False,
        'filters': filters,
        'job_id': 'job-1',
        'localidades': ['CABA'],
    }
    sends = route_after_parse(state)
    # One run_portal_scraper branch per (localidad × source) = 1 localidad × 1 source = 1
    assert len(sends) == 1
    send = sends[0]
    assert send.node == 'run_portal_scraper'
    assert send.arg['filters'].zona == 'CABA'


def test_no_localidades_fans_out_per_barrio_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'SCRAPE_ZONAPROP_ONLY', True)
    monkeypatch.setattr(settings, 'APIFY_DISABLED', False)
    filters = ScrapingFilters(zonas=['Palermo', 'Chacarita'])
    state = {
        'clarification_needed': False,
        'filters': filters,
        'job_id': 'job-1',
    }
    sends = route_after_parse(state)
    assert len(sends) == 2
    zonas_sent = sorted(s.arg['filters'].zona for s in sends)
    assert zonas_sent == ['Chacarita', 'Palermo']


def test_empty_localidades_list_falls_back_to_per_barrio(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'SCRAPE_ZONAPROP_ONLY', True)
    monkeypatch.setattr(settings, 'APIFY_DISABLED', False)
    filters = ScrapingFilters(zonas=['Palermo', 'Chacarita'])
    state = {
        'clarification_needed': False,
        'filters': filters,
        'job_id': 'job-1',
        'localidades': [],
    }
    sends = route_after_parse(state)
    assert len(sends) == 2


def test_localidad_branch_filters_keep_full_barrio_list_for_guard(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'SCRAPE_ZONAPROP_ONLY', True)
    monkeypatch.setattr(settings, 'APIFY_DISABLED', False)
    filters = ScrapingFilters(zonas=['Palermo', 'Chacarita'])
    state = {
        'clarification_needed': False,
        'filters': filters,
        'job_id': 'job-1',
        'localidades': ['CABA'],
    }
    sends = route_after_parse(state)
    branch_filters = sends[0].arg['filters']
    assert set(branch_filters.zonas) == {'Palermo', 'Chacarita'}
    assert branch_filters.localidades == ['CABA']
