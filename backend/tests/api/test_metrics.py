"""Test-first for the metrics dashboard API.

The endpoints are thin: Postgres does the aggregation (see the `metrics_*` views),
and this layer windows the range, derives the ratios that need a division guard,
and shapes the response. So most of what's worth pinning lives in the pure
helpers — the arithmetic that decides whether a number is honest:

- a ratio whose denominator is 0 must be None, never 0.0 or a crash;
- cost per useful property must ignore searches that produced nothing useful,
  otherwise expensive failures average away to nothing;
- USD is the only currency any average or median is computed in;
- a missing `apify_cost_usd` (job predates the column) is unknown, and must not
  be silently folded in as free.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1 import metrics


# ── fake supabase serving view rows ──────────────────────────────────────────


class _FakeView:
    def __init__(self, rows: list[dict[str, Any]], *, blow_up: bool = False) -> None:
        self._rows = rows
        self._blow_up = blow_up
        self._limit: int | None = None

    def select(self, *_a: Any, **_k: Any) -> '_FakeView':
        return self

    def gte(self, *_a: Any, **_k: Any) -> '_FakeView':
        return self

    def order(self, *_a: Any, **_k: Any) -> '_FakeView':
        return self

    def limit(self, n: int) -> '_FakeView':
        self._limit = n
        return self

    async def execute(self) -> Any:
        if self._blow_up:
            raise RuntimeError('relation "metrics_llm_daily" does not exist')
        rows = self._rows[: self._limit] if self._limit else self._rows
        return type('_Res', (), {'data': rows})()


class _FakeSupabase:
    def __init__(self, views: dict[str, list[dict[str, Any]]], *, broken: set[str] | None = None) -> None:
        self._views = views
        self._broken = broken or set()
        self.queried: list[str] = []

    def table(self, name: str) -> _FakeView:
        self.queried.append(name)
        return _FakeView(self._views.get(name, []), blow_up=name in self._broken)


def _client(sb: Any) -> AsyncClient:
    app = FastAPI()
    app.include_router(metrics.router, prefix='/metrics')
    app.state.supabase = sb
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


# ── window clamping ──────────────────────────────────────────────────────────


def test_window_defaults_to_thirty_days() -> None:
    assert metrics._window_days(None) == metrics.DEFAULT_WINDOW_DAYS


@pytest.mark.parametrize('raw', [0, -5, 'abc', '', None, 99999])
def test_window_rejects_nonsense_instead_of_querying_it(raw: Any) -> None:
    """A garbage `days` must land inside the allowed range, not reach the DB."""
    days = metrics._window_days(raw)
    assert 1 <= days <= metrics.MAX_WINDOW_DAYS


def test_window_honours_a_sane_request() -> None:
    assert metrics._window_days(7) == 7
    assert metrics._window_days('90') == 90


# ── numeric coercion ─────────────────────────────────────────────────────────


def test_numbers_survive_postgrest_returning_numerics_as_strings() -> None:
    """PostgREST can serialise `numeric` as a JSON string to preserve precision."""
    assert metrics._num('0.0412') == pytest.approx(0.0412)
    assert metrics._num(3) == 3.0
    assert metrics._num(None) == 0.0
    assert metrics._num('not a number') == 0.0


# ── ratio guards ─────────────────────────────────────────────────────────────


def test_ratio_of_nothing_is_none_not_zero() -> None:
    """0.0 reads as 'measured, and it was zero'. None reads as 'no data'. Conflating
    them turns an empty dashboard into a dashboard reporting total failure."""
    assert metrics._ratio(0, 0) is None
    assert metrics._ratio(5, 0) is None
    assert metrics._ratio(0, 10) == 0.0
    assert metrics._ratio(3, 4) == pytest.approx(0.75)


def test_percentile_of_an_empty_series_is_none() -> None:
    assert metrics._percentile([], 50) is None


def test_percentile_picks_the_nearest_rank() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    assert metrics._percentile(values, 50) == 5.0
    assert metrics._percentile(values, 95) == 10.0
    assert metrics._percentile([42.0], 95) == 42.0


# ── LLM summary ──────────────────────────────────────────────────────────────


def _llm_row(**kw: Any) -> dict[str, Any]:
    base = {
        'dia': '2026-08-01', 'scope': 'extract_website', 'model': 'claude-haiku-4-5-20251001',
        'llamadas': 1, 'input_tokens': 1000, 'output_tokens': 100,
        'cache_creation_tokens': 0, 'cache_read_tokens': 0, 'cost_usd': 0.0015,
    }
    return {**base, **kw}


def test_llm_summary_totals_and_splits_by_scope() -> None:
    rows = [
        _llm_row(scope='extract_website', cost_usd=0.10, llamadas=20),
        _llm_row(scope='extract_website', cost_usd=0.05, llamadas=10, dia='2026-08-02'),
        _llm_row(scope='search_parse', cost_usd=0.01, llamadas=5),
        _llm_row(scope='ficha_propio', cost_usd=0.04, llamadas=4),
    ]
    out = metrics._summarize_llm(rows)

    assert out['cost_usd'] == pytest.approx(0.20)
    assert out['llamadas'] == 39
    por_scope = {s['scope']: s for s in out['por_scope']}
    assert por_scope['extract_website']['cost_usd'] == pytest.approx(0.15)
    assert por_scope['extract_website']['llamadas'] == 30
    # Ordered most expensive first — that is the only ordering a cost panel wants.
    assert out['por_scope'][0]['scope'] == 'extract_website'


def test_llm_summary_separates_search_spend_from_crm_spend() -> None:
    """The two answer different questions: search spend is a cost of acquiring
    inventory, ficha spend is a cost of publishing it."""
    rows = [
        _llm_row(scope='extract_website', cost_usd=0.10),
        _llm_row(scope='search_parse', cost_usd=0.02),
        _llm_row(scope='ficha_propio', cost_usd=0.03),
        _llm_row(scope='ficha_enrich', cost_usd=0.01),
    ]
    out = metrics._summarize_llm(rows)

    assert out['cost_usd_busquedas'] == pytest.approx(0.12)
    assert out['cost_usd_fichas'] == pytest.approx(0.04)


def test_llm_summary_reports_the_cache_hit_ratio() -> None:
    rows = [_llm_row(input_tokens=250, cache_read_tokens=750, cache_creation_tokens=0)]
    out = metrics._summarize_llm(rows)
    assert out['cache_hit_ratio'] == pytest.approx(0.75)


def test_cache_ratio_is_none_when_no_tokens_were_spent() -> None:
    assert metrics._summarize_llm([])['cache_hit_ratio'] is None


def test_zero_cache_ratio_is_reported_as_zero_not_missing() -> None:
    """Caching is off today (no cache_control on any call). That 0 IS the finding —
    reporting it as 'no data' would hide money being left on the table."""
    rows = [_llm_row(input_tokens=5000, cache_read_tokens=0, cache_creation_tokens=0)]
    assert metrics._summarize_llm(rows)['cache_hit_ratio'] == 0.0


def test_llm_summary_splits_by_model_because_prices_differ() -> None:
    rows = [
        _llm_row(model='claude-haiku-4-5-20251001', cost_usd=0.02),
        _llm_row(model='some-future-model', cost_usd=0.30),
    ]
    out = metrics._summarize_llm(rows)
    por_model = {m['model']: m['cost_usd'] for m in out['por_model']}
    assert por_model['some-future-model'] == pytest.approx(0.30)


# ── Apify summary ────────────────────────────────────────────────────────────


def _apify_row(**kw: Any) -> dict[str, Any]:
    base = {
        'dia': '2026-08-01', 'jobs': 1, 'jobs_ok': 1, 'jobs_error': 0,
        'cost_usd': 0.05, 'cost_usd_desperdiciado': 0.0, 'props': 10,
    }
    return {**base, **kw}


def test_apify_summary_surfaces_wasted_spend() -> None:
    rows = [
        _apify_row(cost_usd=0.10, cost_usd_desperdiciado=0.0, jobs=2, jobs_ok=2),
        _apify_row(dia='2026-08-02', cost_usd=0.06, cost_usd_desperdiciado=0.06,
                   jobs=1, jobs_ok=0, jobs_error=1),
    ]
    out = metrics._summarize_apify(rows)

    assert out['cost_usd'] == pytest.approx(0.16)
    assert out['cost_usd_desperdiciado'] == pytest.approx(0.06)
    assert out['desperdicio_ratio'] == pytest.approx(0.06 / 0.16)
    assert out['jobs'] == 3
    assert out['jobs_error'] == 1


def test_waste_ratio_is_none_when_nothing_was_spent() -> None:
    assert metrics._summarize_apify([])['desperdicio_ratio'] is None


def test_apify_total_discloses_how_many_jobs_had_unrecorded_cost() -> None:
    """`apify_cost_usd` is NULL for jobs predating the column, and the view sums it
    as 0. That makes the total a LOWER BOUND, so the count of unknown jobs has to
    travel with it — otherwise the panel implies a completeness it does not have."""
    rows = [
        _apify_row(cost_usd=0.10, jobs=3, jobs_costo_desconocido=2),
        _apify_row(dia='2026-08-02', cost_usd=0.05, jobs=1, jobs_costo_desconocido=0),
    ]
    out = metrics._summarize_apify(rows)

    assert out['jobs_costo_desconocido'] == 2
    assert out['costo_incompleto'] is True


def test_apify_total_is_complete_when_every_job_recorded_its_cost() -> None:
    rows = [_apify_row(cost_usd=0.10, jobs=2, jobs_costo_desconocido=0)]
    out = metrics._summarize_apify(rows)

    assert out['jobs_costo_desconocido'] == 0
    assert out['costo_incompleto'] is False


# ── merged daily series ──────────────────────────────────────────────────────


def test_daily_series_merges_both_spend_sources_on_the_same_day() -> None:
    llm = [_llm_row(dia='2026-08-01', cost_usd=0.02), _llm_row(dia='2026-08-03', cost_usd=0.05)]
    apify = [_apify_row(dia='2026-08-01', cost_usd=0.10), _apify_row(dia='2026-08-02', cost_usd=0.07)]

    series = metrics._daily_series(llm, apify)

    by_day = {d['dia']: d for d in series}
    assert by_day['2026-08-01']['llm_usd'] == pytest.approx(0.02)
    assert by_day['2026-08-01']['apify_usd'] == pytest.approx(0.10)
    assert by_day['2026-08-01']['total_usd'] == pytest.approx(0.12)
    # A day with only one source still appears, with 0 for the other.
    assert by_day['2026-08-02']['llm_usd'] == 0.0
    assert by_day['2026-08-03']['apify_usd'] == 0.0
    # Chronological — a time axis is useless unsorted.
    assert [d['dia'] for d in series] == ['2026-08-01', '2026-08-02', '2026-08-03']


def test_monthly_projection_uses_the_observed_daily_burn() -> None:
    """$0.60 over a 30-day window projects to $0.60/month, not $18."""
    assert metrics._project_month(0.60, 30) == pytest.approx(0.60)
    assert metrics._project_month(0.70, 7) == pytest.approx(3.0)
    assert metrics._project_month(0.0, 0) is None


# ── job / search summary ─────────────────────────────────────────────────────


def _job_row(**kw: Any) -> dict[str, Any]:
    base = {
        'job_id': 'j1', 'query_raw': 'casas villa elisa', 'zona': 'Villa Elisa',
        'estado': 'done', 'fuentes': ['zonaprop'], 'creado_at': '2026-08-01T10:00:00Z',
        'completado_at': '2026-08-01T10:01:00Z', 'duracion_seg': 60.0, 'prop_count': 10,
        'props_total': 10, 'props_match': 4, 'apify_cost_usd': 0.05, 'llm_cost_usd': 0.02,
        'llm_llamadas': 12, 'total_cost_usd': 0.07, 'costo_por_prop_util': 0.0175,
        'precision_ratio': 0.4,
    }
    return {**base, **kw}


def test_search_summary_builds_the_status_funnel() -> None:
    rows = [
        _job_row(estado='done'), _job_row(estado='done'),
        _job_row(estado='error'), _job_row(estado='running'),
    ]
    out = metrics._summarize_jobs(rows)

    assert out['jobs'] == 4
    assert out['por_estado']['done'] == 2
    assert out['por_estado']['error'] == 1
    assert out['error_ratio'] == pytest.approx(0.25)


def test_fleet_cost_per_useful_property_ignores_searches_with_no_useful_result() -> None:
    """Two searches: one produced 4 useful props for $0.08, one produced nothing for
    $0.20. The fleet number must be $0.28/4 = $0.07 — the failure's spend still
    counts, it just contributes no denominator. Averaging per-job ratios instead
    would let the $0.20 failure vanish entirely."""
    rows = [
        _job_row(total_cost_usd=0.08, props_match=4, costo_por_prop_util=0.02),
        _job_row(total_cost_usd=0.20, props_match=0, costo_por_prop_util=None),
    ]
    out = metrics._summarize_jobs(rows)

    assert out['costo_por_prop_util'] == pytest.approx(0.28 / 4)
    assert out['props_match'] == 4
    assert out['cost_usd'] == pytest.approx(0.28)


def test_cost_per_useful_property_is_none_when_nothing_useful_was_found() -> None:
    rows = [_job_row(total_cost_usd=0.20, props_match=0, costo_por_prop_util=None)]
    assert metrics._summarize_jobs(rows)['costo_por_prop_util'] is None


def test_search_summary_reports_duration_percentiles() -> None:
    rows = [_job_row(duracion_seg=float(s)) for s in range(1, 21)]
    out = metrics._summarize_jobs(rows)
    assert out['duracion_p50_seg'] == pytest.approx(10.0)
    assert out['duracion_p95_seg'] == pytest.approx(19.0)


def test_unfinished_searches_are_excluded_from_duration_stats() -> None:
    """A running job has no duration yet; counting it as 0 would fake a fast p50."""
    rows = [_job_row(duracion_seg=100.0), _job_row(estado='running', duracion_seg=None)]
    out = metrics._summarize_jobs(rows)
    assert out['duracion_p50_seg'] == pytest.approx(100.0)


def test_precision_ratio_is_computed_over_the_fleet_not_averaged() -> None:
    rows = [
        _job_row(props_total=100, props_match=10),
        _job_row(props_total=2, props_match=2),
    ]
    out = metrics._summarize_jobs(rows)
    # 12 matched of 102 scraped. Averaging the per-job ratios (0.10 and 1.00) would
    # claim 55% precision off a 2-property search.
    assert out['precision_ratio'] == pytest.approx(12 / 102)


def test_unrecorded_apify_cost_stays_none_instead_of_becoming_zero() -> None:
    """A job whose cost was never recorded must not render identically to a job that
    genuinely cost nothing (cache-served, or only the non-Apify sources). Those are
    opposite facts about the same number."""
    rows = [_job_row(job_id='legacy', apify_cost_usd=None, llm_cost_usd=0.0, total_cost_usd=0.0)]
    out = metrics._summarize_jobs(rows)

    assert out['mas_caras'][0]['apify_cost_usd'] is None
    # It still contributes 0 to the fleet total — a sum has to be a number.
    assert out['apify_cost_usd'] == 0.0


def test_a_recorded_zero_apify_cost_stays_zero() -> None:
    """The other side of the same coin: 0 is a measurement worth keeping, because it
    is what makes the agency cache's value visible."""
    rows = [_job_row(job_id='cached', apify_cost_usd=0.0)]
    assert metrics._summarize_jobs(rows)['mas_caras'][0]['apify_cost_usd'] == 0.0


def test_search_summary_ranks_the_most_expensive_searches() -> None:
    rows = [
        _job_row(job_id='cheap', total_cost_usd=0.01),
        _job_row(job_id='pricey', total_cost_usd=0.90),
        _job_row(job_id='mid', total_cost_usd=0.30),
    ]
    out = metrics._summarize_jobs(rows)
    assert [j['job_id'] for j in out['mas_caras']] == ['pricey', 'mid', 'cheap']


# ── endpoints ────────────────────────────────────────────────────────────────


async def test_costs_endpoint_reports_both_sources_and_the_combined_total() -> None:
    sb = _FakeSupabase({
        'metrics_llm_daily': [_llm_row(cost_usd=0.04)],
        'metrics_apify_daily': [_apify_row(cost_usd=0.16)],
        'metrics_apify_source_spend': [
            {'fuente': 'zonaprop', 'cost_usd': 0.14, 'runs': 12, 'jobs': 3},
            {'fuente': 'argenprop', 'cost_usd': 0.02, 'runs': 2, 'jobs': 2},
        ],
    })
    async with _client(sb) as c:
        body = (await c.get('/metrics/costs?days=30')).json()

    assert body['total_usd'] == pytest.approx(0.20)
    assert body['llm']['cost_usd'] == pytest.approx(0.04)
    assert body['apify']['cost_usd'] == pytest.approx(0.16)
    assert body['apify']['por_fuente'][0]['fuente'] == 'zonaprop'
    assert body['dias'] == 30
    assert 'error' not in body


async def test_costs_endpoint_degrades_to_zeros_when_a_view_is_missing() -> None:
    """The views ship in a migration. If it has not run, the page must render an
    empty dashboard with the reason — not a 500."""
    sb = _FakeSupabase({'metrics_apify_daily': [_apify_row()]}, broken={'metrics_llm_daily'})
    async with _client(sb) as c:
        res = await c.get('/metrics/costs')

    assert res.status_code == 200
    body = res.json()
    assert body['llm']['cost_usd'] == 0.0
    assert body['error']


async def test_every_endpoint_survives_an_unconfigured_supabase() -> None:
    async with _client(None) as c:
        for path in ('/metrics/costs', '/metrics/searches', '/metrics/properties', '/metrics/zones'):
            res = await c.get(path)
            assert res.status_code == 200, path
            assert res.json()['error'], path


async def test_searches_endpoint_shapes_the_job_summary() -> None:
    sb = _FakeSupabase({
        'metrics_job_costs': [
            _job_row(job_id='j1', estado='done', total_cost_usd=0.08, props_match=4),
            _job_row(job_id='j2', estado='error', total_cost_usd=0.02, props_match=0,
                     props_total=0, duracion_seg=None, costo_por_prop_util=None),
        ],
    })
    async with _client(sb) as c:
        body = (await c.get('/metrics/searches?days=7')).json()

    assert body['jobs'] == 2
    assert body['por_estado']['error'] == 1
    assert body['cost_usd'] == pytest.approx(0.10)
    assert body['dias'] == 7


async def test_properties_endpoint_returns_completeness_ratios() -> None:
    sb = _FakeSupabase({
        'metrics_property_health': [{
            'total': 200, 'con_precio': 180, 'con_m2': 100, 'geocodificadas': 150,
            'con_direccion_norm': 200, 'con_imagenes': 190, 'con_descripcion': 120,
            'enviadas': 20, 'fichas_propias': 12, 'nunca_verificadas': 30,
            'verificacion_vencida': 5, 'confianza_promedio': 0.82,
            'primera_alta': '2026-05-01T00:00:00Z', 'ultima_alta': '2026-08-10T00:00:00Z',
        }],
        'metrics_property_daily': [
            {'dia': '2026-08-01', 'fuente': 'zonaprop', 'tipo_operacion': 'venta',
             'props': 30, 'con_precio': 28, 'precio_promedio_usd': 150000},
        ],
    })
    async with _client(sb) as c:
        body = (await c.get('/metrics/properties')).json()

    assert body['total'] == 200
    assert body['completitud']['precio'] == pytest.approx(0.90)
    assert body['completitud']['m2'] == pytest.approx(0.50)
    assert body['completitud']['geocodificadas'] == pytest.approx(0.75)
    # Commercial funnel: scraped → sent.
    assert body['enviadas'] == 20
    assert body['enviadas_ratio'] == pytest.approx(0.10)


async def test_properties_endpoint_handles_an_empty_inventory() -> None:
    """Fresh install: every ratio is None, nothing divides by zero."""
    sb = _FakeSupabase({'metrics_property_health': [{'total': 0}]})
    async with _client(sb) as c:
        body = (await c.get('/metrics/properties')).json()

    assert body['total'] == 0
    assert body['completitud']['precio'] is None
    assert body['enviadas_ratio'] is None


async def test_zones_endpoint_labels_the_zoneless_row_instead_of_dropping_it() -> None:
    """Polygon searches run with no zona but still cost money. Dropping that row
    would make the zone spend column not add up to the total."""
    sb = _FakeSupabase({
        'metrics_zone_stats': [
            {'zona': 'Villa Elisa', 'busquedas': 4, 'busquedas_error': 0, 'props': 40,
             'props_match': 12, 'props_enviadas': 3, 'props_geocodificadas': 35,
             'precio_mediano_usd': 145000, 'precio_m2_mediano_usd': 1200,
             'apify_cost_usd': 0.2, 'llm_cost_usd': 0.05, 'total_cost_usd': 0.25,
             'costo_por_prop_util': 0.0208},
            {'zona': None, 'busquedas': 2, 'busquedas_error': 1, 'props': 5,
             'props_match': 1, 'props_enviadas': 0, 'props_geocodificadas': 5,
             'precio_mediano_usd': None, 'precio_m2_mediano_usd': None,
             'apify_cost_usd': 0.11, 'llm_cost_usd': 0.01, 'total_cost_usd': 0.12,
             'costo_por_prop_util': 0.12},
        ],
    })
    async with _client(sb) as c:
        body = (await c.get('/metrics/zones')).json()

    zonas = {z['zona']: z for z in body['zonas']}
    assert metrics.SIN_ZONA_LABEL in zonas
    assert zonas[metrics.SIN_ZONA_LABEL]['total_cost_usd'] == pytest.approx(0.12)
    # Most spend first: that is the ranking a cost dashboard is read for.
    assert body['zonas'][0]['zona'] == 'Villa Elisa'


async def test_zones_endpoint_caps_how_many_rows_it_returns() -> None:
    rows = [
        {'zona': f'z{i}', 'busquedas': 1, 'props': 1, 'props_match': 1,
         'total_cost_usd': float(i), 'apify_cost_usd': float(i), 'llm_cost_usd': 0.0}
        for i in range(40)
    ]
    sb = _FakeSupabase({'metrics_zone_stats': rows})
    async with _client(sb) as c:
        body = (await c.get('/metrics/zones?limit=5')).json()

    assert len(body['zonas']) == 5
