-- Fix: `metrics_zone_stats` raised at query time, not at creation time.
--
--   FULL JOIN is only supported with merge-joinable or hash-joinable join
--   conditions  (SQLSTATE 0A000)
--
-- The view joined its CTEs with `FULL OUTER JOIN ... ON a.zona IS NOT DISTINCT
-- FROM b.zona`. `IS NOT DISTINCT FROM` is not hash- or merge-joinable, and a FULL
-- JOIN cannot fall back to a nested loop, so Postgres accepts the view definition
-- and then refuses to execute it. Nothing catches that until something SELECTs.
--
-- The NULL-safe comparison was needed because zona is NULL for polygon searches,
-- and those rows carry real spend — dropping them would stop the per-zone spend
-- column from adding up to the total.
--
-- Fix: derive the key set with UNION (which folds NULLs into a single row) and
-- LEFT JOIN each CTE onto it. LEFT JOIN *can* use a nested loop, so the NULL-safe
-- condition is fine there. Same output columns in the same order, so the view can
-- be replaced in place.

create or replace view public.metrics_zone_stats
with (security_invoker = true) as
with jobs_por_zona as (
    select
        nullif(btrim(coalesce(zona, '')), '')       as zona,
        count(*)                                     as busquedas,
        count(*) filter (where estado = 'error')     as busquedas_error,
        sum(coalesce(apify_cost_usd, 0))             as apify_cost_usd,
        max(creado_at)                               as ultima_busqueda
    from public.scraping_jobs
    group by 1
),
llm_por_zona as (
    select
        nullif(btrim(coalesce(j.zona, '')), '')     as zona,
        sum(l.cost_usd)                              as llm_cost_usd
    from public.llm_usage l
    join public.scraping_jobs j on j.id = l.job_id
    group by 1
),
props_por_zona as (
    select
        nullif(btrim(coalesce(j.zona, '')), '')                      as zona,
        count(distinct p.id)                                          as props,
        count(distinct p.id) filter (where r.matches_criteria)        as props_match,
        count(distinct p.id) filter (where p.enviada_at is not null)   as props_enviadas,
        count(distinct p.id) filter (
            where p.lat is not null and p.lng is not null
        )                                                             as props_geocodificadas,
        percentile_cont(0.5) within group (
            order by p.precio / p.m2_total
        ) filter (
            where p.moneda = 'USD' and p.precio is not null
              and p.m2_total is not null and p.m2_total > 0
        )                                                             as precio_m2_mediano_usd,
        percentile_cont(0.5) within group (order by p.precio) filter (
            where p.moneda = 'USD' and p.precio is not null
        )                                                             as precio_mediano_usd
    from public.search_property_results r
    join public.scraping_jobs j on j.id = r.job_id
    join public.properties p     on p.id = r.property_id
    group by 1
),
-- UNION, not UNION ALL: it dedupes, and it treats NULL as equal to NULL, so the
-- zoneless bucket becomes exactly one key.
zonas as (
    select zona from jobs_por_zona
    union
    select zona from props_por_zona
)
select
    z.zona,
    coalesce(jz.busquedas, 0)                                        as busquedas,
    coalesce(jz.busquedas_error, 0)                                  as busquedas_error,
    jz.ultima_busqueda,
    coalesce(pz.props, 0)                                            as props,
    coalesce(pz.props_match, 0)                                      as props_match,
    coalesce(pz.props_enviadas, 0)                                   as props_enviadas,
    coalesce(pz.props_geocodificadas, 0)                             as props_geocodificadas,
    pz.precio_mediano_usd,
    pz.precio_m2_mediano_usd,
    coalesce(jz.apify_cost_usd, 0)                                   as apify_cost_usd,
    coalesce(lz.llm_cost_usd, 0)                                     as llm_cost_usd,
    coalesce(jz.apify_cost_usd, 0) + coalesce(lz.llm_cost_usd, 0)    as total_cost_usd,
    case
        when coalesce(pz.props_match, 0) > 0
        then (coalesce(jz.apify_cost_usd, 0) + coalesce(lz.llm_cost_usd, 0))
             / pz.props_match
    end                                                              as costo_por_prop_util
from zonas z
left join jobs_por_zona  jz on jz.zona is not distinct from z.zona
left join props_por_zona pz on pz.zona is not distinct from z.zona
left join llm_por_zona   lz on lz.zona is not distinct from z.zona;

comment on view public.metrics_zone_stats is
  'Per-zone leaderboard: search volume, yield, spend, and median USD price / price '
  'per m2. Zone comes from the scraping job, since `properties` has no zona column '
  '— so a property found by searches in two zonas counts in both. NULL zona (jobs '
  'run without one, e.g. polygon searches) is its own row and must be labelled as '
  'such by the caller, never dropped: it can carry real spend.';

revoke all on public.metrics_zone_stats from anon, authenticated;
grant select on public.metrics_zone_stats to service_role;
