ALTER TABLE public.scraping_jobs
  ADD COLUMN localidades jsonb NULL DEFAULT NULL;

COMMENT ON COLUMN public.scraping_jobs.localidades IS
  'Localidades (partido/ciudad) resolved for a zone-search polygon; NULL for chat-originated jobs.';
