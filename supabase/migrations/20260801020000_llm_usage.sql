-- Ledger de gasto en tokens de Anthropic, una fila por llamada.
-- Alimenta los contadores automáticos de la solapa Ficha Propio: la cantidad de
-- fichas sale de `properties.fuente='manual'` y el gasto de sumar acá.

create table if not exists public.llm_usage (
    id          uuid primary key default gen_random_uuid(),
    scope       text not null,
    model       text not null,
    input_tokens                 integer not null default 0,
    output_tokens                integer not null default 0,
    cache_creation_input_tokens  integer not null default 0,
    cache_read_input_tokens      integer not null default 0,
    cost_usd    numeric(12, 8) not null default 0,
    property_id uuid references public.properties(id) on delete set null,
    url         text,
    created_at  timestamptz not null default now()
);

comment on column public.llm_usage.scope is
  'Qué trabajo pagó esta llamada. `ficha_propio` = generación/enriquecimiento de una '
  'Ficha Propio (cuenta para el contador del CRM); `ficha_enrich` = el mismo enrich '
  'corriendo sobre una ficha scrapeada de portal (NO cuenta). El scope se decide en '
  'el call site: es lo único que evita que el contador infle.';

comment on column public.llm_usage.cost_usd is
  'USD facturados por esta llamada, calculados desde los tokens reales con la tabla '
  'de precios de backend/app/services/llm_costs.py — no es una estimación.';

comment on column public.llm_usage.property_id is
  'Propiedad asociada cuando se conoce. NULL en el import: la llamada de extracción '
  'se cobra aunque la página no tenga propiedad y no se persista ninguna fila.';

create index if not exists idx_llm_usage_scope on public.llm_usage (scope);
create index if not exists idx_llm_usage_created_at on public.llm_usage (created_at desc);

alter table public.llm_usage enable row level security;

-- Default-deny: el backend usa service_role (bypassea RLS); no hay acceso anon.
create policy "default deny" on public.llm_usage
    as restrictive for all using (false);
