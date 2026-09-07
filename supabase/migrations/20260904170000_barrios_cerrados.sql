-- Gated communities (barrio cerrado / club de campo / country) as a
-- first-class, hand-curated search target.
--
-- WHY A TABLE AND NOT A CONSTANT. `app.services.zona.ZONA_TERMS` hardcodes the
-- five Gran La Plata localidades because they are a closed, stable set. Gated
-- communities are neither: there are hundreds, they open and close, and only
-- the operator knows which ones the agency actually works. So the catalogue is
-- data, edited from the UI, not code shipped in a release.
--
-- WHAT `localidad` IS FOR — the load-bearing column. `zona_candidates` builds
-- its fallback chain from the comma string it is handed: "City Bell, La Plata"
-- degrades to "La Plata". A gated community never arrives with that string
-- (the user says "Grand Bell"), so its chain is one link long and any portal
-- that cannot resolve it returns NOTHING. This column supplies the containment
-- nobody can derive, and `barrio_zona()` splices it back in.
--
-- RLS enabled + default-deny, same as `saved_zones`: the backend uses
-- service_role (bypasses RLS) and no anon-key access is expected in this phase.

create table public.barrios_cerrados (
    id          uuid primary key default gen_random_uuid(),
    -- The identity, as the operator writes it: "Grand Bell". Store it WITHOUT
    -- the kind when you can ("Grand Bell", not "Barrio Cerrado Grand Bell") —
    -- `barrio_aliases()` generates the kind-prefixed forms, and a name that
    -- already carries one is not expanded again.
    nombre      text not null,
    -- "City Bell, La Plata" — the containing localidad, most specific first,
    -- exactly as the zona pipeline spells a zona. This is what the candidate
    -- chain degrades THROUGH.
    localidad   text not null,
    kind        text not null default 'barrio_cerrado'
                check (kind in ('barrio_cerrado', 'club_de_campo', 'country', 'barrio_privado')),
    -- Spellings no algorithm will guess: "Grand Bell II" listed as "Grand Bell 2".
    -- Merged into the generated alias set by `barrio_aliases(nombre, extra=...)`.
    aliases     text[] not null default '{}',
    -- Optional geo-fence. A gated community has a REAL boundary, which makes
    -- point-in-polygon the most reliable filter available for portals with no
    -- native entity (Mudafy has none — every country URL 404s). Same
    -- [[lat, lng], ...] shape as `saved_zones.polygon`, so the same Leaflet
    -- component and the same `app.services.polygon.point_in_polygon` apply.
    polygon     jsonb,
    activo      boolean not null default true,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

-- One row per (barrio, portal): how THIS portal names the barrio, and how the
-- scraper should ask it.
--
-- This is a cache with provenance, not a derived view. Resolution costs a live
-- autocomplete call per portal, the answers change rarely, and — the real
-- reason — they need HUMAN CONFIRMATION: homonyms are rampant. Argenprop alone
-- serves a "Los Ceibos" in Tigre, in La Plata, in Córdoba, in Corrientes and in
-- González Catán, and disambiguates them with suffixes a slugifier will never
-- produce (LOS-CEIBOS-LP vs LOS-CEIBOS-TIG). An unconfirmed row is a guess.
create table public.barrio_cerrado_portal_refs (
    id          uuid primary key default gen_random_uuid(),
    barrio_id   uuid not null references public.barrios_cerrados (id) on delete cascade,
    portal      text not null,
    -- 'native'    → the portal has the barrio as its own location entity; `ref`
    --               is the slug/id the scraper filters on server-side.
    -- 'localidad' → the portal has no page for it (InmoBusqueda barrios,
    --               Mudafy). Search the localidad and filter by alias/polygon.
    strategy    text not null check (strategy in ('native', 'localidad')),
    -- The portal's own handle: 'GRAND-BELL' (Argenprop CodigoBarrio),
    -- 'in::::::2439:' (RE/MAX locations string), 'grand-bell' (ZonaProp slug).
    -- Null when strategy = 'localidad'.
    ref         text,
    -- The portal's own LABEL, verbatim. This is the answer to "cómo figura
    -- este barrio en cada portal" and the only thing a human can review:
    -- "Grand Bell, City Bell, La Plata, Buenos Aires".
    label       text,
    -- Listings the probe saw. THE confirmation signal: a ref that resolves to
    -- a real place and serves 0 listings resolved to the wrong place. Null
    -- when the probe could not count without burning proxy bandwidth.
    result_count integer,
    -- False until a human says "yes, that is my barrio". The scraper may still
    -- use an unconfirmed ref, but the UI has to show it as a guess.
    confirmed   boolean not null default false,
    -- Why the probe decided what it decided, for the review screen.
    note        text,
    probed_at   timestamptz not null default now(),
    unique (barrio_id, portal)
);

alter table public.barrios_cerrados enable row level security;
alter table public.barrio_cerrado_portal_refs enable row level security;

create policy "default deny" on public.barrios_cerrados
    as restrictive for all using (false);
create policy "default deny" on public.barrio_cerrado_portal_refs
    as restrictive for all using (false);

-- The catalogue is read whole on every search that names a barrio, so the
-- active set is the hot path.
create index idx_barrios_cerrados_activo
    on public.barrios_cerrados (activo, nombre);
-- Name lookup is case/accent-insensitive in the service layer, but the plain
-- index still serves the ordered listing the UI renders.
create index idx_barrios_cerrados_nombre on public.barrios_cerrados (nombre);
create index idx_barrio_refs_barrio on public.barrio_cerrado_portal_refs (barrio_id);

-- Which gated communities a search was scoped to. Ids, not names: the fan-out
-- needs the row's `localidad` and `aliases`, and denormalising them onto the
-- job would freeze a barrio's spelling at search time — a later alias fix
-- would silently not apply to re-runs.
alter table public.scraping_jobs
    add column if not exists barrios_cerrados uuid[];
