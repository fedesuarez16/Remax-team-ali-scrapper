-- Manually-curated zona classification for registered sources.
--
-- The zona of an inmobiliaria is NOT inferred by the system: we type it when
-- loading the source. `zona_norm` is the lookup key (written by the API with
-- app.services.zona.normalize_zona, same convention as real_estate_agencies)
-- so 'City Bell', 'city bell' and 'City Bell, La Plata' share one bucket.
alter table public.manual_sources add column if not exists zona text;
alter table public.manual_sources add column if not exists zona_norm text;

create index if not exists manual_sources_zona_norm_idx
  on public.manual_sources (zona_norm);

-- Source selection made by the user BEFORE the search runs (which portales,
-- whether to hit inmobiliarias, and for which zona). Read back by
-- stream_scraping and injected into the extraction graph's inputs.
alter table public.scraping_jobs add column if not exists source_selection jsonb;
