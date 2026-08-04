-- Per-search Apify spend. Every actor run object carries `usageTotalUsd`; the
-- backend books it while polling the run (no extra API call) and writes the
-- tally here when the job reaches a terminal state.

ALTER TABLE public.scraping_jobs
  ADD COLUMN IF NOT EXISTS apify_cost_usd numeric(10, 4),
  ADD COLUMN IF NOT EXISTS apify_cost_breakdown jsonb;

COMMENT ON COLUMN public.scraping_jobs.apify_cost_usd IS
  'Total USD billed by Apify for this search (sum of every actor run, failed runs included). '
  '0 means the search really was free — served from the agency cache, or only from the '
  'direct non-Apify sources (mercadolibre, remax). NULL means unknown: job predates this column.';

COMMENT ON COLUMN public.scraping_jobs.apify_cost_breakdown IS
  'Per-source tally {source: {usd: numeric, runs: int}}. ZonaProp fires one run per listing '
  'page, so `runs` > 1 is normal. A source absent from the object never reached an actor.';
