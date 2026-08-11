"""Read models for the metrics dashboard.

Postgres does the aggregation — see the `metrics_*` views in
`supabase/migrations/20260811040000_metrics_views.sql`. `llm_usage` gets one row
per scraped page, so summing it in this process would mean moving tens of
thousands of rows per dashboard load.

What is left here is the part that has to be gotten right rather than fast:

1. **Division guards.** Every ratio returns None when its denominator is 0.
   Returning 0.0 would read as "measured, and the answer is zero" — an empty
   dashboard would claim 0% precision and 0% cache hits, which are findings, not
   absences.

2. **Fleet ratios, never averaged ratios.** Cost per useful property is
   `sum(cost) / sum(useful)`, not the mean of the per-job ratios. Averaging lets a
   search that spent $0.20 and found nothing disappear, and lets a 2-property
   search claim the same weight as a 200-property one.

3. **Failures keep their spend.** A search that errored still burned Apify credits
   and tokens. It contributes to the numerator and not the denominator, which is
   what makes the cost-per-useful number uncomfortable enough to be useful.

Every endpoint degrades instead of raising: a missing view (migration not applied)
or an unconfigured Supabase returns zeroed panels plus an `error`, because a
dashboard that 500s tells the user less than one that says why it is empty.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request

from app.services.llm_costs import SEARCH_SCOPES

logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_WINDOW_DAYS = 30
MAX_WINDOW_DAYS = 365

# Zone stats key off `scraping_jobs.zona`, which is NULL for polygon searches.
# Those rows carry real spend, so they are labelled rather than dropped — else the
# per-zone spend column silently fails to add up to the total.
SIN_ZONA_LABEL = '(sin zona)'

# How many rows the "worst offender" lists return. Enough to act on, few enough to
# read without scrolling.
TOP_N = 10
DEFAULT_ZONE_LIMIT = 20
MAX_ZONE_LIMIT = 100


# ── primitives ───────────────────────────────────────────────────────────────


def _window_days(raw: Any) -> int:
    """Clamp a caller-supplied window into [1, MAX_WINDOW_DAYS].

    Clamped rather than validated with a 422: `days` is a view control, and a
    dashboard that refuses to render over a bad querystring is worse than one
    that shows the default range.
    """
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_DAYS
    return max(1, min(MAX_WINDOW_DAYS, days))


def _num(value: Any) -> float:
    """Coerce a PostgREST numeric to float. Absent/garbage → 0.0.

    PostgREST may serialise `numeric` as a JSON string to preserve precision, so
    a column that is a float in one response can be a string in another.
    """
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _ratio(numerator: float, denominator: float) -> float | None:
    """A share, or None when there is nothing to take a share of. See module docstring."""
    if not denominator:
        return None
    return numerator / denominator


def _percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile. None for an empty series.

    Nearest-rank rather than interpolated on purpose: these are durations of real
    runs, and reporting a p95 that no run actually took invites arguments about
    the number instead of about the slow searches.
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), round(pct / 100 * len(ordered))))
    return ordered[rank - 1]


def _project_month(total_usd: float, days: int) -> float | None:
    """Extrapolate the observed burn to a 30-day month. None when the window is empty."""
    if days <= 0:
        return None
    return total_usd / days * 30


def _since(days: int) -> str:
    """Inclusive lower bound for the window, as a date the views can compare on."""
    return (datetime.now(UTC).date() - timedelta(days=days - 1)).isoformat()


async def _rows(sb: Any, view: str, *, since: str | None = None,
                date_column: str = 'dia') -> tuple[list[dict[str, Any]], str | None]:
    """Read a metrics view. Returns (rows, error) — never raises.

    The error is surfaced instead of swallowed so the dashboard can say "this panel
    is empty because the migration has not run" rather than just showing zeros.
    """
    if sb is None:
        return [], 'Supabase no configurado'
    try:
        query = sb.table(view).select('*')
        if since is not None:
            query = query.gte(date_column, since)
        res = await query.execute()
    except Exception as exc:
        logger.warning('metrics view %s unavailable: %s', view, exc)
        return [], f'{view}: {exc}'
    return list(res.data or []), None


# ── LLM spend ────────────────────────────────────────────────────────────────


def _summarize_llm(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold `metrics_llm_daily` into totals, per-scope and per-model splits.

    Split by model as well as scope because a model swap changes the price per
    token: one blended cost-per-call average across two price tables is a number
    that describes nothing.
    """
    por_scope: dict[str, dict[str, float]] = defaultdict(
        lambda: {'cost_usd': 0.0, 'llamadas': 0.0, 'input_tokens': 0.0, 'output_tokens': 0.0}
    )
    por_model: dict[str, dict[str, float]] = defaultdict(
        lambda: {'cost_usd': 0.0, 'llamadas': 0.0}
    )

    total = llamadas = input_tokens = output_tokens = 0.0
    cache_read = cache_creation = 0.0
    busquedas_usd = fichas_usd = 0.0

    for row in rows:
        cost = _num(row.get('cost_usd'))
        calls = _int(row.get('llamadas'))
        scope = row.get('scope') or 'desconocido'
        model = row.get('model') or 'desconocido'
        row_input = _num(row.get('input_tokens'))
        row_output = _num(row.get('output_tokens'))

        total += cost
        llamadas += calls
        input_tokens += row_input
        output_tokens += row_output
        cache_read += _num(row.get('cache_read_tokens'))
        cache_creation += _num(row.get('cache_creation_tokens'))

        # Acquiring inventory vs publishing it — two different budgets.
        if scope in SEARCH_SCOPES:
            busquedas_usd += cost
        else:
            fichas_usd += cost

        bucket = por_scope[scope]
        bucket['cost_usd'] += cost
        bucket['llamadas'] += calls
        bucket['input_tokens'] += row_input
        bucket['output_tokens'] += row_output

        model_bucket = por_model[model]
        model_bucket['cost_usd'] += cost
        model_bucket['llamadas'] += calls

    # Cache reads are billed at a tenth of the input rate, so this ratio is the
    # single most actionable LLM number: it is 0 today because no call site sets
    # cache_control, and the extraction prompts are long and identical per run.
    cacheable = input_tokens + cache_read + cache_creation

    return {
        'cost_usd': total,
        'cost_usd_busquedas': busquedas_usd,
        'cost_usd_fichas': fichas_usd,
        'llamadas': int(llamadas),
        'input_tokens': int(input_tokens),
        'output_tokens': int(output_tokens),
        'cache_read_tokens': int(cache_read),
        'cache_creation_tokens': int(cache_creation),
        'cache_hit_ratio': _ratio(cache_read, cacheable),
        'costo_por_llamada': _ratio(total, llamadas),
        'por_scope': sorted(
            ({'scope': s, **{k: (int(v) if k == 'llamadas' else v) for k, v in vals.items()}}
             for s, vals in por_scope.items()),
            key=lambda d: d['cost_usd'], reverse=True,
        ),
        'por_model': sorted(
            ({'model': m, 'cost_usd': vals['cost_usd'], 'llamadas': int(vals['llamadas'])}
             for m, vals in por_model.items()),
            key=lambda d: d['cost_usd'], reverse=True,
        ),
    }


# ── Apify spend ──────────────────────────────────────────────────────────────


def _summarize_apify(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold `metrics_apify_daily` into totals plus the wasted-spend share.

    `jobs_costo_desconocido` travels with the total on purpose. Jobs predating
    `scraping_jobs.apify_cost_usd` have NULL there, which the view sums as 0 — so
    whenever that count is non-zero the total is a LOWER BOUND, and `costo_incompleto`
    lets the UI say so instead of presenting the number as complete.
    """
    total = wasted = 0.0
    jobs = jobs_ok = jobs_error = props = desconocido = 0

    for row in rows:
        total += _num(row.get('cost_usd'))
        wasted += _num(row.get('cost_usd_desperdiciado'))
        jobs += _int(row.get('jobs'))
        jobs_ok += _int(row.get('jobs_ok'))
        jobs_error += _int(row.get('jobs_error'))
        props += _int(row.get('props'))
        desconocido += _int(row.get('jobs_costo_desconocido'))

    return {
        'cost_usd': total,
        'cost_usd_desperdiciado': wasted,
        'desperdicio_ratio': _ratio(wasted, total),
        'jobs': jobs,
        'jobs_ok': jobs_ok,
        'jobs_error': jobs_error,
        'jobs_costo_desconocido': desconocido,
        'costo_incompleto': desconocido > 0,
        'props': props,
        'costo_por_prop': _ratio(total, props),
    }


def _daily_series(llm_rows: list[dict[str, Any]],
                  apify_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One chronological row per day carrying both spend sources.

    Days present in only one source still appear, with 0 for the other: a gap in
    the series would read as "no spend" on a stacked chart, which is the same
    pixel as a real zero but a different fact.
    """
    days: dict[str, dict[str, float]] = defaultdict(lambda: {'llm_usd': 0.0, 'apify_usd': 0.0})

    for row in llm_rows:
        dia = str(row.get('dia'))
        days[dia]['llm_usd'] += _num(row.get('cost_usd'))
    for row in apify_rows:
        dia = str(row.get('dia'))
        days[dia]['apify_usd'] += _num(row.get('cost_usd'))

    return [
        {'dia': dia, 'llm_usd': v['llm_usd'], 'apify_usd': v['apify_usd'],
         'total_usd': v['llm_usd'] + v['apify_usd']}
        for dia, v in sorted(days.items())
    ]


# ── searches ─────────────────────────────────────────────────────────────────


def _summarize_jobs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold `metrics_job_costs` into the search panel.

    Ratios are computed over the fleet (`sum / sum`), never as the mean of the
    per-job ratios — see the module docstring for why that distinction decides
    whether the number is honest.
    """
    por_estado: dict[str, int] = defaultdict(int)
    duraciones: list[float] = []
    total_cost = apify_cost = llm_cost = 0.0
    props_total = props_match = 0
    llm_llamadas = 0

    for row in rows:
        por_estado[row.get('estado') or 'desconocido'] += 1
        total_cost += _num(row.get('total_cost_usd'))
        apify_cost += _num(row.get('apify_cost_usd'))
        llm_cost += _num(row.get('llm_cost_usd'))
        props_total += _int(row.get('props_total'))
        props_match += _int(row.get('props_match'))
        llm_llamadas += _int(row.get('llm_llamadas'))
        # Only finished searches have a duration. Treating a running job as 0
        # would fake a fast p50.
        if row.get('duracion_seg') is not None:
            duraciones.append(_num(row.get('duracion_seg')))

    jobs = len(rows)
    mas_caras = sorted(rows, key=lambda r: _num(r.get('total_cost_usd')), reverse=True)[:TOP_N]

    return {
        'jobs': jobs,
        'por_estado': dict(por_estado),
        'error_ratio': _ratio(por_estado.get('error', 0), jobs),
        'cost_usd': total_cost,
        'apify_cost_usd': apify_cost,
        'llm_cost_usd': llm_cost,
        'llm_llamadas': llm_llamadas,
        'costo_por_busqueda': _ratio(total_cost, jobs),
        'props_total': props_total,
        'props_match': props_match,
        'precision_ratio': _ratio(props_match, props_total),
        'costo_por_prop_util': _ratio(total_cost, props_match),
        'duracion_p50_seg': _percentile(duraciones, 50),
        'duracion_p95_seg': _percentile(duraciones, 95),
        'mas_caras': [
            {
                'job_id': r.get('job_id'),
                'query_raw': r.get('query_raw'),
                'zona': r.get('zona'),
                'estado': r.get('estado'),
                'creado_at': r.get('creado_at'),
                'fuentes': r.get('fuentes') or [],
                'total_cost_usd': _num(r.get('total_cost_usd')),
                # Passed through nullable: NULL means the cost was never recorded
                # (job predates the column), which is the opposite finding from a
                # recorded 0 (cache-served, or only the non-Apify sources).
                'apify_cost_usd': (
                    None if r.get('apify_cost_usd') is None
                    else _num(r.get('apify_cost_usd'))
                ),
                'llm_cost_usd': _num(r.get('llm_cost_usd')),
                'props_total': _int(r.get('props_total')),
                'props_match': _int(r.get('props_match')),
                'costo_por_prop_util': (
                    None if r.get('costo_por_prop_util') is None
                    else _num(r.get('costo_por_prop_util'))
                ),
            }
            for r in mas_caras
        ],
    }


# ── endpoints ────────────────────────────────────────────────────────────────


@router.get('/costs')
async def costs_metrics(request: Request, days: int | None = None) -> dict[str, Any]:
    """Apify + Anthropic spend over the window: totals, daily series, and the
    breakdowns that say WHERE the money went (per source, per scope, per model).

    These are the only two spend sources in the system — geocoding runs on
    Nominatim, which is free.
    """
    sb = getattr(request.app.state, 'supabase', None)
    window = _window_days(days)
    since = _since(window)

    llm_rows, llm_err = await _rows(sb, 'metrics_llm_daily', since=since)
    apify_rows, apify_err = await _rows(sb, 'metrics_apify_daily', since=since)
    source_rows, source_err = await _rows(sb, 'metrics_apify_source_spend')

    llm = _summarize_llm(llm_rows)
    apify = _summarize_apify(apify_rows)
    apify['por_fuente'] = sorted(
        (
            {
                'fuente': r.get('fuente'),
                'cost_usd': _num(r.get('cost_usd')),
                'runs': _int(r.get('runs')),
                'jobs': _int(r.get('jobs')),
                # ZonaProp fires one run per listing page, so this is the number
                # that explains an unexpectedly large bill.
                'costo_por_run': _ratio(_num(r.get('cost_usd')), _int(r.get('runs'))),
            }
            for r in source_rows
        ),
        key=lambda d: d['cost_usd'], reverse=True,
    )

    total = llm['cost_usd'] + apify['cost_usd']
    body: dict[str, Any] = {
        'dias': window,
        'desde': since,
        'total_usd': total,
        'proyeccion_mensual_usd': _project_month(total, window),
        'llm': llm,
        'apify': apify,
        'serie_diaria': _daily_series(llm_rows, apify_rows),
    }
    error = next((e for e in (llm_err, apify_err, source_err) if e), None)
    if error:
        body['error'] = error
    return body


@router.get('/searches')
async def search_metrics(request: Request, days: int | None = None) -> dict[str, Any]:
    """Per-search economics and health: status funnel, precision, duration
    percentiles, cost per search and per criteria-matching property."""
    sb = getattr(request.app.state, 'supabase', None)
    window = _window_days(days)
    since = _since(window)

    rows, error = await _rows(sb, 'metrics_job_costs', since=since, date_column='creado_at')

    body: dict[str, Any] = {'dias': window, 'desde': since, **_summarize_jobs(rows)}
    if error:
        body['error'] = error
    return body


@router.get('/properties')
async def property_metrics(request: Request, days: int | None = None) -> dict[str, Any]:
    """Inventory size, data completeness, commercial funnel and freshness.

    Completeness is not vanity: ficha generation degrades field by field, and a
    property with no m2 cannot enter the price-per-m2 medians at all.
    """
    sb = getattr(request.app.state, 'supabase', None)
    window = _window_days(days)
    since = _since(window)

    health_rows, health_err = await _rows(sb, 'metrics_property_health')
    daily_rows, daily_err = await _rows(sb, 'metrics_property_daily', since=since)

    health = health_rows[0] if health_rows else {}
    total = _int(health.get('total'))

    por_fuente: dict[str, int] = defaultdict(int)
    por_operacion: dict[str, int] = defaultdict(int)
    crecimiento: dict[str, int] = defaultdict(int)
    for row in daily_rows:
        props = _int(row.get('props'))
        por_fuente[row.get('fuente') or 'desconocido'] += props
        por_operacion[row.get('tipo_operacion') or 'desconocido'] += props
        crecimiento[str(row.get('dia'))] += props

    body: dict[str, Any] = {
        'dias': window,
        'desde': since,
        'total': total,
        'enviadas': _int(health.get('enviadas')),
        'enviadas_ratio': _ratio(_int(health.get('enviadas')), total),
        'fichas_propias': _int(health.get('fichas_propias')),
        'confianza_promedio': (
            None if health.get('confianza_promedio') is None
            else _num(health.get('confianza_promedio'))
        ),
        'completitud': {
            'precio': _ratio(_int(health.get('con_precio')), total),
            'm2': _ratio(_int(health.get('con_m2')), total),
            'geocodificadas': _ratio(_int(health.get('geocodificadas')), total),
            'direccion_norm': _ratio(_int(health.get('con_direccion_norm')), total),
            'imagenes': _ratio(_int(health.get('con_imagenes')), total),
            'descripcion': _ratio(_int(health.get('con_descripcion')), total),
        },
        'frescura': {
            'nunca_verificadas': _int(health.get('nunca_verificadas')),
            'verificacion_vencida': _int(health.get('verificacion_vencida')),
            'nunca_verificadas_ratio': _ratio(_int(health.get('nunca_verificadas')), total),
            'primera_alta': health.get('primera_alta'),
            'ultima_alta': health.get('ultima_alta'),
        },
        'altas_en_ventana': sum(crecimiento.values()),
        'por_fuente': sorted(
            ({'fuente': f, 'props': n} for f, n in por_fuente.items()),
            key=lambda d: d['props'], reverse=True,
        ),
        'por_operacion': sorted(
            ({'tipo_operacion': t, 'props': n} for t, n in por_operacion.items()),
            key=lambda d: d['props'], reverse=True,
        ),
        'serie_altas': [{'dia': d, 'props': n} for d, n in sorted(crecimiento.items())],
    }
    error = next((e for e in (health_err, daily_err) if e), None)
    if error:
        body['error'] = error
    return body


@router.get('/zones')
async def zone_metrics(request: Request, limit: int | None = None) -> dict[str, Any]:
    """Zone leaderboard: volume, yield, spend and median USD prices.

    Not windowed. Zone stats read as "what do we know about this zone", and a
    30-day cut would drop the medians for any zone not searched this month —
    exactly the zones a user asks about.

    Zone comes from the scraping job, since `properties` has no zona column, so a
    property found by searches in two zonas is counted in both.
    """
    sb = getattr(request.app.state, 'supabase', None)
    cap = max(1, min(MAX_ZONE_LIMIT, _int(limit) or DEFAULT_ZONE_LIMIT))

    rows, error = await _rows(sb, 'metrics_zone_stats')

    zonas = sorted(
        (
            {
                'zona': r.get('zona') or SIN_ZONA_LABEL,
                'busquedas': _int(r.get('busquedas')),
                'busquedas_error': _int(r.get('busquedas_error')),
                'ultima_busqueda': r.get('ultima_busqueda'),
                'props': _int(r.get('props')),
                'props_match': _int(r.get('props_match')),
                'props_enviadas': _int(r.get('props_enviadas')),
                'props_geocodificadas': _int(r.get('props_geocodificadas')),
                'cobertura_geo_ratio': _ratio(
                    _int(r.get('props_geocodificadas')), _int(r.get('props'))
                ),
                'precision_ratio': _ratio(_int(r.get('props_match')), _int(r.get('props'))),
                'precio_mediano_usd': (
                    None if r.get('precio_mediano_usd') is None
                    else _num(r.get('precio_mediano_usd'))
                ),
                'precio_m2_mediano_usd': (
                    None if r.get('precio_m2_mediano_usd') is None
                    else _num(r.get('precio_m2_mediano_usd'))
                ),
                'apify_cost_usd': _num(r.get('apify_cost_usd')),
                'llm_cost_usd': _num(r.get('llm_cost_usd')),
                'total_cost_usd': _num(r.get('total_cost_usd')),
                'costo_por_prop_util': (
                    None if r.get('costo_por_prop_util') is None
                    else _num(r.get('costo_por_prop_util'))
                ),
            }
            for r in rows
        ),
        key=lambda d: _num(d.get('total_cost_usd')), reverse=True,
    )

    body: dict[str, Any] = {
        'total_zonas': len(zonas),
        'zonas': zonas[:cap],
        # Stated explicitly so a reader knows the medians are not currency-blended.
        'moneda_medianas': 'USD',
    }
    if error:
        body['error'] = error
    return body
