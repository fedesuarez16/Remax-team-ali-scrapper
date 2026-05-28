-- EP-01: domain tables for chat scraping. RLS enabled + default-deny on all.
-- Backend uses service_role (bypasses RLS). Frontend (authenticated) has no policies yet => no access.

-- real_estate_agencies ---------------------------------------------------
create table public.real_estate_agencies (
    id          uuid primary key default gen_random_uuid(),
    nombre      text not null,
    telefono    text,
    email       text,
    sitio_web   text,
    zona        text,
    created_at  timestamptz not null default now()
);
alter table public.real_estate_agencies enable row level security;

create policy "default deny" on public.real_estate_agencies
    as restrictive for all using (false);

-- scraping_jobs ----------------------------------------------------------
create table public.scraping_jobs (
    id            uuid primary key default gen_random_uuid(),
    query_raw     text not null,
    zona          text,
    fuentes       text[] not null default '{}',
    filters       jsonb,
    estado        text not null default 'pending'
                  check (estado in ('pending','running','done','error')),
    prop_count    integer not null default 0,
    error_msg     text,
    creado_at     timestamptz not null default now(),
    completado_at timestamptz
);
alter table public.scraping_jobs enable row level security;

create policy "default deny" on public.scraping_jobs
    as restrictive for all using (false);

-- properties -------------------------------------------------------------
create table public.properties (
    id                   uuid primary key default gen_random_uuid(),
    titulo               text,
    direccion            text not null,
    direccion_norm       text,
    precio               numeric(14,2),
    moneda               text not null default 'USD' check (moneda in ('USD','ARS')),
    tipo_operacion       text not null
                         check (tipo_operacion in ('venta','alquiler','alquiler_temp')),
    tipo_propiedad       text not null default 'otro'
                         check (tipo_propiedad in ('departamento','casa','ph','local','oficina','terreno','otro')),
    ambientes            smallint,
    m2_total             numeric(10,2),
    m2_cubiertos         numeric(10,2),
    antiguedad           smallint,
    amenities            text[] not null default '{}',
    imagenes             text[] not null default '{}',
    fuente               text not null
                         check (fuente in ('zonaprop','mercadolibre','googlemaps','argenprop','manual')),
    url_origen           text,
    scraping_job_id      uuid references public.scraping_jobs(id) on delete set null,
    timestamp_scrapeo    timestamptz not null default now(),
    confianza_extraccion numeric(4,3) check (confianza_extraccion between 0 and 1),
    created_at           timestamptz not null default now(),
    updated_at           timestamptz not null default now()
);
alter table public.properties enable row level security;

create policy "default deny" on public.properties
    as restrictive for all using (false);

create index properties_scraping_job_id_idx on public.properties (scraping_job_id);
-- dedup: same listing reappearing across runs is the same (direccion, precio, tipo_operacion)
create unique index properties_dedup_idx
    on public.properties (direccion, precio, tipo_operacion);
