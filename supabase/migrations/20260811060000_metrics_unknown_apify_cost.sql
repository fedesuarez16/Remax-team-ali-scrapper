-- Stop reporting unknown Apify cost as $0.
--
-- `scraping_jobs.apify_cost_usd` is NULL for jobs that predate the column, and its
-- own comment says so: "NULL means unknown". `metrics_job_costs` coalesced that to
-- 0 before exposing it, so the dashboard rendered a job whose cost was never
-- recorded identically to a job that genuinely cost nothing (cache-served, or
-- mercadolibre/remax only). Those are opposite facts, and the whole dashboard is
-- built on keeping "no data" distinguishable from "measured zero".
--
-- Fix, in two parts:
--   1. `metrics_job_costs.apify_cost_usd` passes the raw nullable value through, so
--      a per-job row can render "—". `total_cost_usd` keeps coalescing, because a
--      SUM has to be a number — it just understates, which the caller discloses.
--   2. `metrics_apify_daily` gains `jobs_costo_desconocido`, so a panel can say
--      how much of its own total is missing instead of implying completeness.
--
-- Column order and types are unchanged (part 2 appends), so both views can be
-- replaced in place.

create or replace view public.metrics_job_costs
with (security_invoker = true) as
with llm as (
    select job_id,
           sum(cost_usd) as llm_cost_usd,
           count(*)      as llm_llamadas
    from public.llm_usage
    where job_id is not null
    group by job_id
),
results as (
    select job_id,
           count(*)                                    as props_total,
           count(*) filter (where matches_criteria)    as props_match
    from public.search_property_results
    group by job_id
)
select
    j.id                                        as job_id,
    j.query_raw,
    nullif(btrim(coalesce(j.zona, '')), '')     as zona,
    j.estado,
    j.fuentes,
    j.creado_at,
    j.completado_at,
    extract(epoch from (j.completado_at - j.creado_at))::numeric(12, 2) as duracion_seg,
    j.prop_count,
    coalesce(r.props_total, 0)                  as props_total,
    coalesce(r.props_match, 0)                  as props_match,
    -- Raw and nullable ON PURPOSE. NULL = never recorded; 0 = recorded as free.
    -- Cast to unconstrained `numeric` so the column type matches what the previous
    -- definition produced (`coalesce(numeric(10,4), 0)` widens to plain `numeric`);
    -- `create or replace view` refuses any column type change, even a narrowing one.
    j.apify_cost_usd::numeric                   as apify_cost_usd,
    coalesce(l.llm_cost_usd, 0)                 as llm_cost_usd,
    coalesce(l.llm_llamadas, 0)                 as llm_llamadas,
    -- The total coalesces, so it is a lower bound when any input is unknown.
    coalesce(j.apify_cost_usd, 0) + coalesce(l.llm_cost_usd, 0) as total_cost_usd,
    case
        when coalesce(r.props_match, 0) > 0
        then (coalesce(j.apify_cost_usd, 0) + coalesce(l.llm_cost_usd, 0)) / r.props_match
    end                                         as costo_por_prop_util,
    case
        when coalesce(r.props_total, 0) > 0
        then coalesce(r.props_match, 0)::numeric / r.props_total
    end                                         as precision_ratio
from public.scraping_jobs j
left join llm     l on l.job_id = j.id
left join results r on r.job_id = j.id;

comment on view public.metrics_job_costs is
  'Per-search economics: both spend sources, yield, and cost per criteria-matching '
  'property. `apify_cost_usd` is nullable and means what the base column means — '
  'NULL is "never recorded", 0 is "recorded as free" (cache-served, or only the '
  'non-Apify sources). `total_cost_usd` coalesces NULL to 0, so it is a LOWER BOUND '
  'whenever apify cost is unknown. `costo_por_prop_util` and `precision_ratio` are '
  'NULL (not 0) when the denominator is 0 — a search that returned nothing is not a '
  'search that returned free results.';

create or replace view public.metrics_apify_daily
with (security_invoker = true) as
select
    (creado_at at time zone 'UTC')::date                         as dia,
    count(*)                                                     as jobs,
    count(*) filter (where estado = 'done')                      as jobs_ok,
    count(*) filter (where estado = 'error')                      as jobs_error,
    sum(coalesce(apify_cost_usd, 0))                             as cost_usd,
    sum(coalesce(apify_cost_usd, 0)) filter (
        where estado = 'error' or prop_count = 0
    )                                                            as cost_usd_desperdiciado,
    sum(prop_count)::bigint                                      as props,
    -- Appended: how many jobs contributed an unknown (NULL) cost to `cost_usd`.
    -- Non-zero here means the day's total is a lower bound, and the UI must say so
    -- rather than presenting the number as complete.
    count(*) filter (where apify_cost_usd is null)               as jobs_costo_desconocido
from public.scraping_jobs
group by 1;

comment on view public.metrics_apify_daily is
  'Apify spend and job outcomes per day. `cost_usd` treats an unrecorded cost as 0, '
  'so it is a lower bound whenever `jobs_costo_desconocido` > 0 — surface that count '
  'alongside the total instead of implying the total is complete. '
  '`cost_usd_desperdiciado` is spend on jobs that errored or yielded zero properties.';

revoke all on public.metrics_job_costs   from anon, authenticated;
revoke all on public.metrics_apify_daily from anon, authenticated;
grant select on public.metrics_job_costs   to service_role;
grant select on public.metrics_apify_daily to service_role;
