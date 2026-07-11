CREATE TABLE IF NOT EXISTS public.search_property_results (
    job_id      UUID NOT NULL REFERENCES public.scraping_jobs(id) ON DELETE CASCADE,
    property_id UUID NOT NULL REFERENCES public.properties(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, property_id)
);

ALTER TABLE public.search_property_results ENABLE ROW LEVEL SECURITY;

CREATE POLICY "deny all" ON public.search_property_results
    AS RESTRICTIVE FOR ALL USING (false);

CREATE INDEX IF NOT EXISTS idx_spr_job_id ON public.search_property_results (job_id);
CREATE INDEX IF NOT EXISTS idx_spr_property_id ON public.search_property_results (property_id);
