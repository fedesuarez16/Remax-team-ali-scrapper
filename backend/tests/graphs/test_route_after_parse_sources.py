"""Test-first for `route_after_parse` honouring the user's `source_selection`
(picked in the UI BEFORE the search runs) — written BEFORE `nodes.py` reads
the key, so every assertion here MUST fail until it lands.

Rules under test:
1. `portales` subset restricts which `run_portal_scraper` branches fan out.
2. `buscar_portales=False` → no portal branches at all.
3. `buscar_inmobiliarias=False` → no `discover_agencies` branch (the Google
   Maps + manual-sources track is what feeds inmobiliarias).
4. A selection that survives to zero branches routes to the `no_sources`
   terminal node, so the SSE stream always ends with a `done` instead of
   hanging on an empty `Send` list.
5. Missing key → today's behaviour, byte-for-byte (all portales + agencies).

`SCRAPE_ZONAPROP_ONLY`/`APIFY_DISABLED` are pinned off so the assertions read
against the real `PORTAL_SOURCES`, and the env-gate interaction gets its own
test at the bottom.
"""
from app.core.config import settings
from app.graphs.extraction.nodes import route_after_parse
from app.models.property import ScrapingFilters
from app.services.apify import PORTAL_SOURCES


def _state(**overrides) -> dict:
    state = {
        'clarification_needed': False,
        'filters': ScrapingFilters(zonas=['City Bell']),
        'job_id': 'job-1',
    }
    state.update(overrides)
    return state


def _pin_env(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'APIFY_DISABLED', False)
    monkeypatch.setattr(settings, 'SCRAPE_ZONAPROP_ONLY', False)
    monkeypatch.setattr(settings, 'SCRAPE_GOOGLEMAPS_ONLY', False)


def _nodes(sends) -> list[str]:
    return [s.node for s in sends]


# ── portal subset ─────────────────────────────────────────────────────────────


def test_portal_subset_only_fans_out_selected_portals(monkeypatch) -> None:
    _pin_env(monkeypatch)
    sends = route_after_parse(_state(source_selection={
        'buscar_portales': True,
        'portales': ['zonaprop', 'argenprop'],
        'buscar_inmobiliarias': False,
    }))
    portal_sends = [s for s in sends if s.node == 'run_portal_scraper']
    assert sorted(s.arg['__source'] for s in portal_sends) == ['argenprop', 'zonaprop']
    assert 'discover_agencies' not in _nodes(sends)


def test_empty_portal_subset_means_all_portals(monkeypatch) -> None:
    _pin_env(monkeypatch)
    sends = route_after_parse(_state(source_selection={
        'buscar_portales': True, 'portales': [], 'buscar_inmobiliarias': False,
    }))
    portal_sends = [s for s in sends if s.node == 'run_portal_scraper']
    assert sorted(s.arg['__source'] for s in portal_sends) == sorted(PORTAL_SOURCES)


def test_portal_subset_applies_per_fanout_unit(monkeypatch) -> None:
    _pin_env(monkeypatch)
    sends = route_after_parse(_state(
        filters=ScrapingFilters(zonas=['City Bell', 'Gonnet']),
        source_selection={
            'buscar_portales': True, 'portales': ['zonaprop'], 'buscar_inmobiliarias': False,
        },
    ))
    assert len(sends) == 2  # 2 zonas × 1 portal, no agency branch
    assert sorted(s.arg['filters'].zona for s in sends) == ['City Bell', 'Gonnet']


# ── inmobiliarias toggle ──────────────────────────────────────────────────────


def test_inmobiliarias_only_skips_every_portal_branch(monkeypatch) -> None:
    _pin_env(monkeypatch)
    sends = route_after_parse(_state(source_selection={
        'buscar_portales': False,
        'buscar_inmobiliarias': True,
        'zona_inmobiliarias': 'City Bell',
    }))
    assert 'run_portal_scraper' not in _nodes(sends)
    # Zona-scoped: no discovery either — just the pass-through that carries the
    # graph to `review_agencies`, where the curated registry is read.
    assert _nodes(sends) == ['aggregate_phase1']


def test_zona_scoped_search_skips_google_maps_discovery(monkeypatch) -> None:
    """Picking a zona means "only the inmobiliarias we filed under it".
    Google-Maps discovery would inject agencies that belong to no curated
    zona, so the discovery branch must not fan out at all — the curated
    registry (route_after_review's manual sources) is the only inmobiliaria
    source for a zona-scoped run."""
    _pin_env(monkeypatch)
    sends = route_after_parse(_state(source_selection={
        'buscar_portales': True,
        'portales': ['zonaprop'],
        'buscar_inmobiliarias': True,
        'zona_inmobiliarias': 'City Bell',
    }))
    assert 'discover_agencies' not in _nodes(sends)
    assert [s.arg['__source'] for s in sends] == ['zonaprop']


def test_zona_scoped_inmobiliarias_only_still_reaches_the_curated_registry(monkeypatch) -> None:
    """Inmobiliarias-only + a zona leaves no phase-1 branch to run, but the
    curated sources are fetched in `review_agencies` — which only runs if the
    graph gets there. Routing to `no_sources` would silently skip them."""
    _pin_env(monkeypatch)
    sends = route_after_parse(_state(source_selection={
        'buscar_portales': False,
        'buscar_inmobiliarias': True,
        'zona_inmobiliarias': 'City Bell',
    }))
    assert sends != 'no_sources'


def test_todas_las_zonas_keeps_google_maps_discovery(monkeypatch) -> None:
    """"Todas las zonas" is the broadest search: curated registry AND
    Google-Maps discovery, i.e. exactly today's behaviour."""
    _pin_env(monkeypatch)
    sends = route_after_parse(_state(source_selection={
        'buscar_portales': False, 'buscar_inmobiliarias': True, 'zona_inmobiliarias': None,
    }))
    assert _nodes(sends) == ['discover_agencies']


def test_both_tracks_selected_fans_out_portals_and_agencies(monkeypatch) -> None:
    _pin_env(monkeypatch)
    sends = route_after_parse(_state(source_selection={
        'buscar_portales': True, 'portales': ['zonaprop'], 'buscar_inmobiliarias': True,
    }))
    assert sorted(_nodes(sends)) == ['discover_agencies', 'run_portal_scraper']


# ── degenerate selection → terminal node, never an empty Send list ────────────


def test_selection_with_no_surviving_branch_routes_to_no_sources(monkeypatch) -> None:
    """Portal picked but disallowed by the deployment gate, inmobiliarias off:
    nothing can run, so route to the terminal node instead of returning []."""
    monkeypatch.setattr(settings, 'APIFY_DISABLED', False)
    monkeypatch.setattr(settings, 'SCRAPE_ZONAPROP_ONLY', True)  # env allows zonaprop only
    monkeypatch.setattr(settings, 'SCRAPE_GOOGLEMAPS_ONLY', False)
    assert route_after_parse(_state(source_selection={
        'buscar_portales': True, 'portales': ['argenprop'], 'buscar_inmobiliarias': False,
    })) == 'no_sources'


def test_env_gate_intersects_with_user_selection(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'APIFY_DISABLED', False)
    monkeypatch.setattr(settings, 'SCRAPE_ZONAPROP_ONLY', True)
    monkeypatch.setattr(settings, 'SCRAPE_GOOGLEMAPS_ONLY', False)
    sends = route_after_parse(_state(source_selection={
        'buscar_portales': True,
        'portales': ['zonaprop', 'argenprop'],
        'buscar_inmobiliarias': False,
    }))
    assert [s.arg['__source'] for s in sends] == ['zonaprop']


# ── backward compatibility ────────────────────────────────────────────────────


def test_missing_source_selection_behaves_exactly_as_before(monkeypatch) -> None:
    _pin_env(monkeypatch)
    plain = route_after_parse(_state())
    selected_all = route_after_parse(_state(source_selection={
        'buscar_portales': True, 'portales': [], 'buscar_inmobiliarias': True,
        'zona_inmobiliarias': None,
    }))
    assert _nodes(plain) == _nodes(selected_all)
    assert [s.arg.get('__source') for s in plain] == [s.arg.get('__source') for s in selected_all]


def test_clarification_short_circuit_still_wins(monkeypatch) -> None:
    _pin_env(monkeypatch)
    assert route_after_parse(_state(
        clarification_needed=True,
        source_selection={'buscar_portales': False, 'buscar_inmobiliarias': False},
    )) == 'clarification'
