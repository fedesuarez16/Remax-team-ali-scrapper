create table public.manual_sources (
  id uuid primary key default gen_random_uuid(),
  nombre text not null,
  url text not null,
  activo boolean not null default true,
  created_at timestamptz not null default now()
);
