-- Named zone delineations for the map: geometry ONLY, no search results.
-- A row here is "this polygon is called Belgrano R" — reusable across
-- searches, never tied to a scraping job (that's what search_history is for).
--
-- RLS enabled + default-deny: backend uses service_role (bypasses RLS); no
-- frontend/anon-key access is expected in this phase (no auth yet).

create table public.saved_zones (
    id         uuid primary key default gen_random_uuid(),
    name       text not null,
    -- [[lat, lng], ...] — same shape the Leaflet Polygon consumes. Validated
    -- server-side (>= 3 numeric vertices, in-range coords) before insert.
    polygon    jsonb not null,
    created_at timestamptz not null default now()
);
alter table public.saved_zones enable row level security;

create policy "default deny" on public.saved_zones
    as restrictive for all using (false);

create index idx_saved_zones_created_at
    on public.saved_zones (created_at desc);
