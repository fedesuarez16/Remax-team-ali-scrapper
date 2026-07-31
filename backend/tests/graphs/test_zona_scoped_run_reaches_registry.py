"""End-to-end check of the load-bearing new mechanism: a zona-scoped,
inmobiliarias-only search has NO phase-1 work to do, so `route_after_parse`
routes through `aggregate_phase1` as a pass-through. This test runs the real
compiled graph to prove the run actually reaches `review_agencies` (where the
curated registry is read) and fans out to `run_website_scraper` for that
zona's inmobiliarias only — instead of dying on an empty fan-out.

Only the two external boundaries are faked: `parse_query` (Anthropic call) and
Supabase. Every routing/edge decision under test is the real graph.
"""
import pytest

from app.core.config import settings
from app.graphs.extraction import graph as graph_module
from app.graphs.extraction.graph import build_graph
from app.models.property import ScrapingFilters
from app.services.zona import normalize_zona


class _Res:
    def __init__(self, data) -> None:
        self.data = data


class _FakeQuery:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self._filters: list[tuple[str, object]] = []

    def select(self, *_a, **_kw) -> '_FakeQuery':
        return self

    def eq(self, field: str, value) -> '_FakeQuery':
        self._filters.append((field, value))
        return self

    async def execute(self) -> _Res:
        rows = self._rows
        for field, value in self._filters:
            rows = [r for r in rows if r.get(field) == value]
        return _Res(rows)


class _FakeSupabase:
    def __init__(self, manual_sources: list[dict]) -> None:
        self._manual_sources = manual_sources

    def table(self, name: str) -> _FakeQuery:
        if name == 'manual_sources':
            return _FakeQuery(self._manual_sources)
        # Agency cache — empty, so "todas las zonas" discovery finds nothing
        # via Google Maps and the curated registry is the only source.
        if name == 'real_estate_agencies':
            return _FakeQuery([])
        raise AssertionError(f'unexpected table {name}')


def _src(nombre: str, url: str, zona: str) -> dict:
    return {
        'nombre': nombre, 'url': url, 'activo': True,
        'zona': zona, 'zona_norm': normalize_zona(zona),
    }


REGISTRY = [
    _src('Inmobiliaria A', 'https://a.com', 'City Bell'),
    _src('Inmobiliaria B', 'https://b.com', 'City Bell'),
    _src('Inmobiliaria D', 'https://d.com', 'Gonnet'),
]


@pytest.fixture
def scraped_urls(monkeypatch) -> list[str]:
    """Stub the two external boundaries; record which sites get scraped."""
    urls: list[str] = []

    async def _fake_parse_query(state, config):
        return {'clarification_needed': False, 'filters': ScrapingFilters(zonas=['City Bell'])}

    async def _fake_website_scraper(state, config):
        urls.append(state['url'])
        return {'website_pages': []}

    async def _fake_discover_agencies(state, config):
        return {'agencies': []}

    # `graph.py` binds node callables into its own namespace at import time, so
    # the patch has to land there, not on `nodes` — patching `nodes` leaves the
    # real (network-hitting) functions wired into the compiled graph.
    monkeypatch.setattr(graph_module, 'parse_query', _fake_parse_query)
    monkeypatch.setattr(graph_module, 'run_website_scraper', _fake_website_scraper)
    monkeypatch.setattr(graph_module, 'discover_agencies', _fake_discover_agencies)
    monkeypatch.setattr(settings, 'APIFY_DISABLED', False)
    monkeypatch.setattr(settings, 'SCRAPE_ZONAPROP_ONLY', False)
    monkeypatch.setattr(settings, 'SCRAPE_GOOGLEMAPS_ONLY', False)
    monkeypatch.setattr(settings, 'MAX_WEBSITE_URLS', 10)
    return urls


async def _run(selection: dict, sb) -> None:
    # build_graph resolves node callables at build time, so it must be built
    # AFTER the monkeypatches above.
    graph = build_graph()
    await graph.ainvoke(
        {'query': 'Casa 3 dormitorios en City Bell', 'job_id': 'job-1',
         'source_selection': selection},
        {'configurable': {'supabase': sb}},
    )


async def test_zona_scoped_run_scrapes_only_that_zonas_inmobiliarias(scraped_urls) -> None:
    await _run(
        {'buscar_portales': False, 'buscar_inmobiliarias': True,
         'zona_inmobiliarias': 'City Bell'},
        _FakeSupabase(list(REGISTRY)),
    )
    assert sorted(scraped_urls) == ['https://a.com', 'https://b.com']


async def test_zona_scoped_run_never_touches_another_zonas_inmobiliarias(scraped_urls) -> None:
    await _run(
        {'buscar_portales': False, 'buscar_inmobiliarias': True,
         'zona_inmobiliarias': 'Gonnet'},
        _FakeSupabase(list(REGISTRY)),
    )
    assert scraped_urls == ['https://d.com']


async def test_todas_las_zonas_run_scrapes_every_registered_inmobiliaria(scraped_urls) -> None:
    await _run(
        {'buscar_portales': False, 'buscar_inmobiliarias': True, 'zona_inmobiliarias': None},
        _FakeSupabase(list(REGISTRY)),
    )
    assert sorted(scraped_urls) == ['https://a.com', 'https://b.com', 'https://d.com']


async def test_zona_with_no_loaded_inmobiliarias_ends_cleanly(scraped_urls) -> None:
    await _run(
        {'buscar_portales': False, 'buscar_inmobiliarias': True,
         'zona_inmobiliarias': 'Hudson'},
        _FakeSupabase(list(REGISTRY)),
    )
    assert scraped_urls == []
