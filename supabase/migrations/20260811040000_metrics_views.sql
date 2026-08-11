-- Aggregate views behind the metrics dashboard.
--
-- Why views and not Python: `llm_usage` gets one row per scraped page, so it is
-- the fastest-growing table in the schema. Pulling it over PostgREST to sum in
-- the API process would move tens of thousands of rows per dashboard load. These
-- views push the aggregation into Postgres, so each panel reads tens of rows.
--
-- Grain choice: the `_daily` views pre-aggregate by day, which keeps them small
-- (a year of data is a few hundred rows) while letting the API answer ANY date
-- range by filtering on `dia`. A parameterised view would have needed an RPC per
-- range; day-grain needs none.
--
-- `security_invoker = true` (PG15+) makes each view run with the CALLER's
-- privileges rather than the owner's, so the base tables' default-deny RLS still
-- applies. The backend reads these with service_role (which bypasses RLS); anon
-- gets nothing, which is the same posture as every other table here. The explicit
-- revokes below are belt-and-braces against Supabase's default grants.

-- ── LLM spend, by day / scope / model ────────────────────────────────────────

create or replace view public.metrics_llm_daily
with (security_invoker = true) as
select
    (created_at at time zone 'UTC')::date          as dia,
    scope,
    model,
    count(*)                                       as llamadas,
    sum(input_tokens)::bigint                      as input_tokens,
    sum(output_tokens)::bigint                     as output_tokens,
    sum(cache_creation_input_tokens)::bigint       as cache_creation_tokens,
    sum(cache_read_input_tokens)::bigint           as cache_read_tokens,
    sum(cost_usd)                                  as cost_usd
from public.llm_usage
group by 1, 2, 3;

comment on view public.metrics_llm_daily is
  'Token spend per day/scope/model. Every Anthropic call site books here, so this '
  'is the authoritative LLM cost series. Split by model because a model swap '
  'changes the price per token, making a blended average misleading.';

-- ── Apify spend + job outcomes, by day ───────────────────────────────────────

create or replace view public.metrics_apify_daily
with (security_invoker = true) as
select
    (creado_at at time zone 'UTC')::date                         as dia,
    count(*)                                                     as jobs,
    count(*) filter (where estado = 'done')                      as jobs_ok,
    count(*) filter (where estado = 'error')                      as jobs_error,
    sum(coalesce(apify_cost_usd, 0))                             as cost_usd,
    -- Spend that bought nothing: a job that errored or returned no property but
    -- still billed actor time. Directly actionable waste.
    sum(coalesce(apify_cost_usd, 0)) filter (
        where estado = 'error' or prop_count = 0
    )                                                            as cost_usd_desperdiciado,
    sum(prop_count)::bigint                                      as props
from public.scraping_jobs
group by 1;

comment on view public.metrics_apify_daily is
  'Apify spend and job outcomes per day. `cost_usd_desperdiciado` is spend on jobs '
  'that errored or yielded zero properties. NULL apify_cost_usd (jobs predating the '
  'column) counts as 0, so early days understate rather than break.';

-- ── Per-search economics ─────────────────────────────────────────────────────
--
-- The core view: joins the two spend sources onto one job row and derives cost
-- per USEFUL property. One row per job, so it stays small enough to filter by
-- date on read.

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
    coalesce(j.apify_cost_usd, 0)               as apify_cost_usd,
    coalesce(l.llm_cost_usd, 0)                 as llm_cost_usd,
    coalesce(l.llm_llamadas, 0)                 as llm_llamadas,
    coalesce(j.apify_cost_usd, 0) + coalesce(l.llm_cost_usd, 0) as total_cost_usd,
    -- NULL, not 0, when the search produced nothing useful: "no useful property"
    -- and "a useful property that was free" are opposite findings, and averaging
    -- a 0 into the fleet number would hide the expensive failures.
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
  'property. `costo_por_prop_util` and `precision_ratio` are NULL (not 0) when the '
  'denominator is 0 — a search that returned nothing is not a search that returned '
  'free results, and averaging a 0 would hide the expensive failures.';

-- ── Apify spend per source ───────────────────────────────────────────────────

create or replace view public.metrics_apify_source_spend
with (security_invoker = true) as
select
    entry.key                                                       as fuente,
    sum(coalesce((entry.value ->> 'usd')::numeric, 0))              as cost_usd,
    sum(coalesce((entry.value ->> 'runs')::int, 0))::bigint          as runs,
    count(distinct j.id)                                            as jobs,
    min(j.creado_at)                                                as primer_uso,
    max(j.creado_at)                                                as ultimo_uso
from public.scraping_jobs j
cross join lateral jsonb_each(j.apify_cost_breakdown) as entry(key, value)
where j.apify_cost_breakdown is not null
  and jsonb_typeof(j.apify_cost_breakdown) = 'object'
group by entry.key;

comment on view public.metrics_apify_source_spend is
  'Apify spend broken out per source from `scraping_jobs.apify_cost_breakdown`. '
  '`runs` > 1 per job is normal — ZonaProp fires one actor run per listing page, '
  'which is exactly why per-source attribution matters.';

-- ── Property inventory health ────────────────────────────────────────────────

create or replace view public.metrics_property_health
with (security_invoker = true) as
select
    count(*)                                                             as total,
    count(*) filter (where precio is not null)                           as con_precio,
    count(*) filter (where m2_total is not null and m2_total > 0)         as con_m2,
    count(*) filter (where lat is not null and lng is not null)           as geocodificadas,
    count(*) filter (where direccion_norm is not null
                       and btrim(direccion_norm) <> '')                   as con_direccion_norm,
    count(*) filter (where array_length(imagenes, 1) > 0)                 as con_imagenes,
    count(*) filter (where descripcion is not null
                       and btrim(descripcion) <> '')                      as con_descripcion,
    count(*) filter (where enviada_at is not null)                        as enviadas,
    count(*) filter (where fuente = 'manual')                            as fichas_propias,
    count(*) filter (where ultima_verificacion is null)                   as nunca_verificadas,
    count(*) filter (where ultima_verificacion < now() - interval '30 days')
                                                                          as verificacion_vencida,
    avg(confianza_extraccion)                                            as confianza_promedio,
    min(created_at)                                                      as primera_alta,
    max(created_at)                                                      as ultima_alta
from public.properties;

comment on view public.metrics_property_health is
  'Single-row data-completeness and freshness snapshot of the inventory. The '
  'completeness counters matter because ficha generation degrades field by field: '
  'a property with no m2 cannot be priced per m2, one with no lat/lng is invisible '
  'on the map.';

-- ── Inventory composition and growth ─────────────────────────────────────────

create or replace view public.metrics_property_daily
with (security_invoker = true) as
select
    (created_at at time zone 'UTC')::date        as dia,
    fuente,
    tipo_operacion,
    count(*)                                     as props,
    count(*) filter (where precio is not null)   as con_precio,
    avg(precio) filter (where moneda = 'USD')    as precio_promedio_usd
from public.properties
group by 1, 2, 3;

comment on view public.metrics_property_daily is
  'Daily inventory growth by source and operation type. Averages are USD-only: '
  'blending ARS and USD prices in one mean produces a meaningless number.';

-- ── Zone leaderboard ─────────────────────────────────────────────────────────
--
-- `properties` has no zona column, so zone attribution runs through the job that
-- scraped the property (`search_property_results` → `scraping_jobs.zona`). A
-- property found by two searches in different zonas is counted in both, hence
-- `count(distinct)`.

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
        -- USD-only, and only where m2 is real: a median mixing currencies or
        -- dividing by a zero/NULL m2 is not a price per m2, it is noise.
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
)
select
    coalesce(jz.zona, pz.zona)                                       as zona,
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
from jobs_por_zona jz
full outer join props_por_zona pz on pz.zona is not distinct from jz.zona
left join llm_por_zona lz         on lz.zona is not distinct from coalesce(jz.zona, pz.zona);

comment on view public.metrics_zone_stats is
  'Per-zone leaderboard: search volume, yield, spend, and median USD price / price '
  'per m2. Zone comes from the scraping job, since `properties` has no zona column '
  '— so a property found by searches in two zonas counts in both. NULL zona (jobs '
  'run without one, e.g. polygon searches) is its own row and must be labelled as '
  'such by the caller, never dropped: it can carry real spend.';

-- ── Grants: service_role only, mirroring the base tables ─────────────────────

revoke all on public.metrics_llm_daily          from anon, authenticated;
revoke all on public.metrics_apify_daily        from anon, authenticated;
revoke all on public.metrics_job_costs          from anon, authenticated;
revoke all on public.metrics_apify_source_spend from anon, authenticated;
revoke all on public.metrics_property_health    from anon, authenticated;
revoke all on public.metrics_property_daily     from anon, authenticated;
revoke all on public.metrics_zone_stats         from anon, authenticated;

grant select on public.metrics_llm_daily          to service_role;
grant select on public.metrics_apify_daily        to service_role;
grant select on public.metrics_job_costs          to service_role;
grant select on public.metrics_apify_source_spend to service_role;
grant select on public.metrics_property_health    to service_role;
grant select on public.metrics_property_daily     to service_role;
grant select on public.metrics_zone_stats         to service_role;
