-- Attribute LLM spend to the search that paid for it.
--
-- `apify_cost_usd` already lives on `scraping_jobs`, so Apify spend per search is
-- one column read. Token spend was not answerable the same way: `llm_usage` only
-- had `property_id`, and the `properties` dedup index (direccion, precio,
-- tipo_operacion) leaves a re-scraped listing attached to the job that FIRST saw
-- it — so per-job token cost derived from that column drifts on every repeat
-- search. `job_id` records the payer directly.
--
-- Nullable on purpose: CRM-side calls (Ficha Propio import, POST /properties/match)
-- are not part of any job, and rows written before this column exist keep NULL.
-- `on delete set null` mirrors `property_id`: deleting a job must not erase the
-- accounting record of money already spent.

alter table public.llm_usage
    add column if not exists job_id uuid
    references public.scraping_jobs(id) on delete set null;

comment on column public.llm_usage.job_id is
  'Search that paid for this call, when there was one. NULL means the call belongs '
  'to no job — either a CRM-side call (ficha_propio / match_parse via '
  'POST /properties/match) or a row predating this column.';

-- Aggregation index: the cost-per-search and cost-per-useful-property metrics
-- both group by job_id. Partial — NULL rows are never grouped by job.
create index if not exists idx_llm_usage_job_id
    on public.llm_usage (job_id) where job_id is not null;

-- Composite for the per-scope-per-day spend series (dashboard time axis).
create index if not exists idx_llm_usage_scope_created_at
    on public.llm_usage (scope, created_at desc);

-- Widen the scope documentation now that the search side is instrumented too.
-- Every Anthropic call site in the backend books one of these:
--   ficha_propio      importer._extract_llm            (counts for the CRM counter)
--   ficha_enrich      ficha.enrich_ficha               (does NOT count)
--   search_parse      graphs.extraction.nodes.parse_query
--   match_parse       matcher._parse_criteria
--   extract_website   nodes.extract_website_properties_llm    (per scraped page)
--   extract_instagram nodes.extract_instagram_properties_llm  (per scraped post)
comment on column public.llm_usage.scope is
  'Which job paid for this call, by call site: `ficha_propio` = Ficha Propio '
  'generation (the ONLY scope the CRM counter in GET /properties/ficha-propio/stats '
  'sums); `ficha_enrich` = the same enrichment over a portal-scraped property; '
  '`search_parse` / `match_parse` = one call per search for query and criteria '
  'parsing; `extract_website` / `extract_instagram` = one call per scraped page or '
  'post, the loops that dominate token spend. The scope is decided at the call '
  'site: it is the only thing keeping the CRM counter from inflating.';
