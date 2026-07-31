-- BOT LIMPIADOR — verificación periódica de avisos y borrado de los caídos.
--
-- El bot entra a cada `properties.url_origen`, decide si el aviso sigue
-- publicado y borra la propiedad entera cuando ya no existe (vendida, dada de
-- baja, link roto). Ver backend/app/services/cleaner.py.

-- Cuándo se verificó por última vez esta propiedad. NULL = nunca.
-- El bot prioriza los NULL y después los más viejos, así una base grande se
-- cubre entera a lo largo de varias corridas en vez de repetir siempre las
-- mismas filas.
alter table public.properties add column if not exists ultima_verificacion timestamptz;

create index if not exists properties_ultima_verificacion_idx
  on public.properties (ultima_verificacion);

-- Programación de la limpieza automática: fila única (id = 'default').
-- Vive en la base y no en memoria para que un restart del backend no pierda la
-- configuración ni re-dispare una limpieza que ya corrió.
create table if not exists public.cleanup_schedule (
    id            text primary key default 'default',
    -- El borrado automático se opta explícitamente: default apagado.
    enabled       boolean not null default false,
    interval_days integer not null default 7 check (interval_days between 1 and 365),
    last_run_at   timestamptz,
    updated_at    timestamptz not null default now()
);
alter table public.cleanup_schedule enable row level security;

drop policy if exists "default deny" on public.cleanup_schedule;
create policy "default deny" on public.cleanup_schedule
    as restrictive for all using (false);

insert into public.cleanup_schedule (id) values ('default')
    on conflict (id) do nothing;

-- Auditoría: qué revisó cada corrida y, sobre todo, QUÉ borró y por qué.
-- `eliminadas` guarda la foto de cada propiedad eliminada (id, título,
-- dirección, url, motivo) — es la red de contención ante un falso positivo.
create table if not exists public.cleanup_runs (
    id               uuid primary key default gen_random_uuid(),
    origen           text not null default 'manual'
                     check (origen in ('manual', 'scheduled')),
    dry_run          boolean not null default false,
    revisadas        integer not null default 0,
    activas          integer not null default 0,
    caidas           integer not null default 0,
    -- Verificaciones que no concluyeron (429/403/5xx/timeout): NUNCA borran.
    indeterminadas   integer not null default 0,
    eliminadas_count integer not null default 0,
    eliminadas       jsonb not null default '[]'::jsonb,
    error            text,
    started_at       timestamptz not null default now(),
    finished_at      timestamptz
);
alter table public.cleanup_runs enable row level security;

drop policy if exists "default deny" on public.cleanup_runs;
create policy "default deny" on public.cleanup_runs
    as restrictive for all using (false);

create index if not exists cleanup_runs_started_at_idx
  on public.cleanup_runs (started_at desc);
