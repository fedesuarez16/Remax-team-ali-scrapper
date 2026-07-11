-- agency-cache-by-zona: turn real_estate_agencies into a read-through TTL cache
-- keyed by normalized zona. Backend uses service_role (bypasses RLS) — no policy
-- change needed (matches properties/scraping_jobs default-deny pattern in EP-01).

alter table public.real_estate_agencies
    add column if not exists direccion        text,
    add column if not exists google_maps_url  text,
    add column if not exists instagram_handle text,
    add column if not exists calificacion     numeric(3,2),
    add column if not exists zona_norm         text,
    add column if not exists scraped_at        timestamptz not null default now();

-- Deterministic, never-NULL dedup discriminator: prefer the Google Maps URL
-- (stable external identity); fall back to lowercased name when a place has no
-- url. Generated in-DB so Python never duplicates the rule and NULLs never
-- collapse distinct url-less agencies together.
alter table public.real_estate_agencies
    add column if not exists dedup_key text
    generated always as (coalesce(google_maps_url, 'name:' || lower(nombre))) stored;

-- Idempotent upsert target: one agency per (normalized zona, dedup_key).
create unique index if not exists real_estate_agencies_dedup_idx
    on public.real_estate_agencies (zona_norm, dedup_key);

-- Cache-read access path: WHERE zona_norm = :z AND scraped_at >= :cutoff
create index if not exists real_estate_agencies_zona_fresh_idx
    on public.real_estate_agencies (zona_norm, scraped_at);
