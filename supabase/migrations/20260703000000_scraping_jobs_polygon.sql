ALTER TABLE public.scraping_jobs
  ADD COLUMN polygon jsonb;

COMMENT ON COLUMN public.scraping_jobs.polygon IS
  'Zone-search polygon as [[lat,lng],...]; NULL for chat-originated jobs.';
