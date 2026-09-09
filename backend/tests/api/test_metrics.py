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

from datetime import UTC, date, datetime, timedelta
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
        # Los bounds se registran en vez de aplicarse: lo que hay que verificar es
        # que el rango LLEGUE a la query, no reimplementar PostgREST en el fake.
        self.bounds: dict[str, tuple[str, Any]] = {}

    def select(self, *_a: Any, **_k: Any) -> '_FakeView':
        return self

    def gte(self, column: str, value: Any) -> '_FakeView':
        self.bounds['gte'] = (column, value)
        return self

    def lt(self, column: str, value: Any) -> '_FakeView':
        self.bounds['lt'] = (column, value)
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
        self.views: dict[str, _FakeView] = {}

    def table(self, name: str) -> _FakeView:
        self.queried.append(name)
        view = _FakeView(self._views.get(name, []), blow_up=name in self._broken)
        self.views[name] = view
        return view


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


# ── rango explícito de fechas ────────────────────────────────────────────────


def _hoy() -> date:
    return datetime.now(UTC).date()


def test_an_explicit_range_beats_the_days_preset() -> None:
    """`desde`/`hasta` es la primitiva; `days` es solo el atajo para el default."""
    desde, hasta, span = metrics._resolve_window(30, '2026-03-01', '2026-03-31')
    assert (desde, hasta) == (date(2026, 3, 1), date(2026, 3, 31))
    # Ambos extremos incluidos: pedir el 1 al 31 de marzo son 31 días, no 30.
    assert span == 31


def test_a_range_without_an_end_runs_up_to_today() -> None:
    desde, hasta, _ = metrics._resolve_window(None, '2026-03-01', None)
    assert desde == date(2026, 3, 1)
    assert hasta == _hoy()


def test_a_range_without_a_start_backs_off_the_days_window_from_its_end() -> None:
    desde, hasta, span = metrics._resolve_window(7, None, '2026-03-31')
    assert (desde, hasta) == (date(2026, 3, 25), date(2026, 3, 31))
    assert span == 7


def test_a_reversed_range_is_swapped_instead_of_refused() -> None:
    """Dos inputs de fecha invertidos son un desliz de UI. Un 422 deja la pantalla
    en blanco; darlo vuelta muestra lo que el usuario quiso pedir."""
    desde, hasta, _ = metrics._resolve_window(None, '2026-03-31', '2026-03-01')
    assert (desde, hasta) == (date(2026, 3, 1), date(2026, 3, 31))


def test_an_oversized_range_keeps_its_END_and_moves_the_start() -> None:
    """El tope se aplica desde el final: el lado que se está leyendo es el reciente,
    así que recortar por el principio conserva la pregunta y no la respuesta."""
    desde, hasta, span = metrics._resolve_window(None, '2000-01-01', '2026-03-31')
    assert hasta == date(2026, 3, 31)
    assert span == metrics.MAX_WINDOW_DAYS
    assert desde == hasta - timedelta(days=metrics.MAX_WINDOW_DAYS - 1)


def test_garbage_dates_fall_back_to_the_days_window_instead_of_erroring() -> None:
    desde, hasta, span = metrics._resolve_window(7, 'no-es-una-fecha', '')
    assert span == 7
    assert hasta == _hoy()
    assert desde == hasta - timedelta(days=6)


def test_no_range_at_all_is_the_default_window_ending_today() -> None:
    desde, hasta, span = metrics._resolve_window(None, None, None)
    assert span == metrics.DEFAULT_WINDOW_DAYS
    assert hasta == _hoy()
    assert desde == hasta - timedelta(days=metrics.DEFAULT_WINDOW_DAYS - 1)


# ── granularidad ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize('gran', ['dia', 'semana', 'mes'])
def test_an_explicit_granularity_is_honoured_whatever_the_span(gran: str) -> None:
    assert metrics._granularidad(gran, 400) == gran
    assert metrics._granularidad(gran.upper(), 3) == gran


@pytest.mark.parametrize('raw', [None, '', 'trimestre', 42])
def test_an_unusable_granularity_falls_back_to_auto(raw: Any) -> None:
    """Un valor inválido no puede tumbar el panel: se resuelve por el largo del rango."""
    assert metrics._granularidad(raw, 30) == 'dia'


def test_auto_granularity_widens_as_the_span_grows() -> None:
    """365 barras diarias en 600px son ~1.6px cada una: eso no es un gráfico, es una
    textura. La granularidad por defecto sube con el rango para que cada marca se lea."""
    assert metrics._granularidad(None, 1) == 'dia'
    assert metrics._granularidad(None, 30) == 'dia'
    assert metrics._granularidad(None, 90) == 'semana'
    assert metrics._granularidad(None, 365) == 'semana'
    assert metrics._granularidad(None, 400) == 'mes'


# ── buckets ──────────────────────────────────────────────────────────────────


def test_month_buckets_start_on_the_first() -> None:
    assert metrics._bucket_start(date(2026, 3, 17), 'mes') == date(2026, 3, 1)


def test_week_buckets_start_on_monday() -> None:
    """Semana ISO: el gráfico y cualquier reporte externo tienen que cortar igual."""
    # 2026-03-17 es martes.
    assert metrics._bucket_start(date(2026, 3, 17), 'semana') == date(2026, 3, 16)
    # Un lunes es su propio comienzo de semana.
    assert metrics._bucket_start(date(2026, 3, 16), 'semana') == date(2026, 3, 16)


def test_day_buckets_are_the_day_itself() -> None:
    assert metrics._bucket_start(date(2026, 3, 17), 'dia') == date(2026, 3, 17)


def test_month_buckets_roll_over_the_year() -> None:
    assert metrics._next_bucket(date(2026, 12, 1), 'mes') == date(2027, 1, 1)


def test_buckets_cover_every_period_the_window_touches() -> None:
    """Enero parcial cuenta como bucket: si se cae, su gasto desaparece del total
    de la serie mientras sigue en el total del panel — dos números que no cierran."""
    buckets = metrics._buckets(date(2026, 1, 20), date(2026, 3, 5), 'mes')
    assert buckets == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]


# ── serie por período ────────────────────────────────────────────────────────


def test_series_folds_daily_rows_into_the_requested_period() -> None:
    llm = [
        _llm_row(dia='2026-01-05', cost_usd=0.02),
        _llm_row(dia='2026-01-28', cost_usd=0.03),
        _llm_row(dia='2026-02-10', cost_usd=0.05),
    ]
    apify = [_apify_row(dia='2026-01-06', cost_usd=0.10)]

    serie = metrics._spend_series(
        llm, apify, desde=date(2026, 1, 1), hasta=date(2026, 2, 28), gran='mes',
    )

    por_periodo = {b['periodo']: b for b in serie}
    assert por_periodo['2026-01-01']['llm_usd'] == pytest.approx(0.05)
    assert por_periodo['2026-01-01']['apify_usd'] == pytest.approx(0.10)
    assert por_periodo['2026-01-01']['total_usd'] == pytest.approx(0.15)
    assert por_periodo['2026-02-01']['llm_usd'] == pytest.approx(0.05)
    assert por_periodo['2026-02-01']['apify_usd'] == 0.0


def test_series_keeps_empty_periods_at_zero() -> None:
    """Un hueco en la serie se dibuja igual que un cero real pero es otro hecho.
    El eje tiene que ser proporcional al tiempo, así que los períodos sin gasto van
    explícitos — y además ese cero es el dato correcto."""
    serie = metrics._spend_series(
        [_llm_row(dia='2026-01-05', cost_usd=0.02)], [],
        desde=date(2026, 1, 1), hasta=date(2026, 3, 31), gran='mes',
    )
    assert [b['periodo'] for b in serie] == ['2026-01-01', '2026-02-01', '2026-03-01']
    assert serie[1]['total_usd'] == 0.0


def test_series_flags_a_period_the_window_only_half_covers() -> None:
    """Comparar un septiembre a mitad de camino contra un agosto completo es la
    mentira clásica de estos paneles. El bucket parcial se marca para que la UI
    pueda decirlo en vez de dejar leer una caída de gasto que no existe."""
    serie = metrics._spend_series(
        [], [], desde=date(2026, 1, 20), hasta=date(2026, 2, 28), gran='mes',
    )
    enero, febrero = serie

    assert enero['parcial'] is True
    # `periodo` es el comienzo REAL del mes (así la etiqueta dice "enero"), pero
    # los extremos cubiertos se recortan a la ventana pedida.
    assert enero['periodo'] == '2026-01-01'
    assert enero['desde'] == '2026-01-20'
    assert enero['fin'] == '2026-01-31'
    assert febrero['parcial'] is False
    assert febrero['fin'] == '2026-02-28'


def test_series_carries_llm_tokens_and_calls_per_period() -> None:
    """La pregunta es el consumo del LLM, no solo su precio: sin tokens ni llamadas
    no se puede distinguir un mes caro por volumen de uno caro por prompts largos."""
    llm = [
        _llm_row(dia='2026-01-05', cost_usd=0.02, llamadas=12,
                 input_tokens=9000, output_tokens=400),
        _llm_row(dia='2026-01-06', cost_usd=0.01, llamadas=3,
                 input_tokens=1000, output_tokens=100),
    ]
    serie = metrics._spend_series(
        llm, [], desde=date(2026, 1, 1), hasta=date(2026, 1, 31), gran='mes',
    )

    assert serie[0]['llm_llamadas'] == 15
    assert serie[0]['llm_input_tokens'] == 10_000
    assert serie[0]['llm_output_tokens'] == 500


def test_series_by_week_cuts_on_mondays() -> None:
    llm = [
        _llm_row(dia='2026-03-16', cost_usd=0.02),  # lunes
        _llm_row(dia='2026-03-22', cost_usd=0.03),  # domingo, misma semana
        _llm_row(dia='2026-03-23', cost_usd=0.05),  # lunes siguiente
    ]
    serie = metrics._spend_series(
        llm, [], desde=date(2026, 3, 16), hasta=date(2026, 3, 29), gran='semana',
    )

    assert [b['periodo'] for b in serie] == ['2026-03-16', '2026-03-23']
    assert serie[0]['llm_usd'] == pytest.approx(0.05)
    assert serie[1]['llm_usd'] == pytest.approx(0.05)


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

    series = metrics._spend_series(
        llm, apify, desde=date(2026, 8, 1), hasta=date(2026, 8, 3), gran='dia',
    )

    by_day = {d['periodo']: d for d in series}
    assert by_day['2026-08-01']['llm_usd'] == pytest.approx(0.02)
    assert by_day['2026-08-01']['apify_usd'] == pytest.approx(0.10)
    assert by_day['2026-08-01']['total_usd'] == pytest.approx(0.12)
    # A day with only one source still appears, with 0 for the other.
    assert by_day['2026-08-02']['llm_usd'] == 0.0
    assert by_day['2026-08-03']['apify_usd'] == 0.0
    # Chronological — a time axis is useless unsorted.
    assert [d['periodo'] for d in series] == ['2026-08-01', '2026-08-02', '2026-08-03']


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


async def test_costs_endpoint_windows_on_an_explicit_date_range() -> None:
    """El rango tiene que llegar a la query con AMBOS extremos. Sin el tope
    superior, pedir 'marzo' devolvía marzo y todo lo posterior."""
    sb = _FakeSupabase({
        'metrics_llm_daily': [_llm_row(dia='2026-03-10', cost_usd=0.04)],
        'metrics_apify_daily': [_apify_row(dia='2026-03-10', cost_usd=0.16)],
    })
    async with _client(sb) as c:
        body = (await c.get('/metrics/costs?desde=2026-03-01&hasta=2026-03-31')).json()

    assert body['desde'] == '2026-03-01'
    assert body['hasta'] == '2026-03-31'
    assert body['dias'] == 31

    bounds = sb.views['metrics_llm_daily'].bounds
    assert bounds['gte'] == ('dia', '2026-03-01')
    # Cota superior EXCLUSIVA en el día siguiente: sobre una columna timestamptz un
    # `lte` contra '2026-03-31' compara contra la medianoche y se come el último día.
    assert bounds['lt'] == ('dia', '2026-04-01')


async def test_costs_endpoint_groups_the_series_by_month_when_asked() -> None:
    sb = _FakeSupabase({
        'metrics_llm_daily': [
            _llm_row(dia='2026-01-05', cost_usd=0.02, llamadas=4),
            _llm_row(dia='2026-01-25', cost_usd=0.03, llamadas=6),
            _llm_row(dia='2026-02-14', cost_usd=0.05, llamadas=1),
        ],
        'metrics_apify_daily': [],
    })
    async with _client(sb) as c:
        body = (await c.get(
            '/metrics/costs?desde=2026-01-01&hasta=2026-02-28&granularidad=mes'
        )).json()

    assert body['granularidad'] == 'mes'
    assert [b['periodo'] for b in body['serie']] == ['2026-01-01', '2026-02-01']
    assert body['serie'][0]['llm_usd'] == pytest.approx(0.05)
    assert body['serie'][0]['llm_llamadas'] == 10


async def test_costs_endpoint_groups_the_series_by_week_when_asked() -> None:
    sb = _FakeSupabase({
        'metrics_llm_daily': [_llm_row(dia='2026-03-18', cost_usd=0.07)],
        'metrics_apify_daily': [],
    })
    async with _client(sb) as c:
        body = (await c.get(
            '/metrics/costs?desde=2026-03-16&hasta=2026-03-29&granularidad=semana'
        )).json()

    assert body['granularidad'] == 'semana'
    assert [b['periodo'] for b in body['serie']] == ['2026-03-16', '2026-03-23']


async def test_costs_endpoint_picks_the_granularity_when_the_caller_does_not() -> None:
    sb = _FakeSupabase({'metrics_llm_daily': [], 'metrics_apify_daily': []})
    async with _client(sb) as c:
        body = (await c.get('/metrics/costs?days=365')).json()

    # Se devuelve la granularidad resuelta para que la UI marque el botón activo
    # con lo que el backend hizo de verdad, y no con lo que el usuario pidió.
    assert body['granularidad'] == 'semana'


@pytest.mark.parametrize('path', ['/metrics/searches', '/metrics/properties'])
async def test_every_windowed_endpoint_accepts_the_same_date_range(path: str) -> None:
    """El filtro es uno solo arriba del dashboard: si un panel ignorara el rango,
    sus números no cerrarían con los de al lado."""
    sb = _FakeSupabase({
        'metrics_job_costs': [],
        'metrics_property_health': [{'total': 0}],
        'metrics_property_daily': [],
    })
    async with _client(sb) as c:
        body = (await c.get(f'{path}?desde=2026-03-01&hasta=2026-03-31')).json()

    assert body['desde'] == '2026-03-01'
    assert body['hasta'] == '2026-03-31'
    assert body['dias'] == 31


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
