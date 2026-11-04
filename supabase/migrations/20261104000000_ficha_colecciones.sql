-- Fichas de selección: varias Fichas Propio en UNA ficha para un cliente.
--
-- El agente marca N fichas propias y genera una página compartible
-- ("Propiedades para Romina García"). La personaliza (título, subtítulo,
-- comentario por propiedad, orden, mapa) y el cliente califica cada propiedad
-- desde el link público con "Me interesa" / "No me interesa".
--
-- Los items APUNTAN a `properties`, no las copian: editar una ficha propia se
-- refleja en todas sus selecciones. Borrar la propiedad o la selección se
-- lleva el item (cascade); borrar una selección jamás borra fichas.

create table public.ficha_colecciones (
    id            uuid primary key default gen_random_uuid(),
    cliente       text,
    titulo        text not null default 'Propiedades seleccionadas',
    subtitulo     text,
    agente_email  text,
    mostrar_mapa  boolean not null default false,
    enviada_at    timestamptz,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);
alter table public.ficha_colecciones enable row level security;
create policy "default deny" on public.ficha_colecciones
    as restrictive for all using (false);

create table public.ficha_coleccion_items (
    id             uuid primary key default gen_random_uuid(),
    coleccion_id   uuid not null references public.ficha_colecciones(id) on delete cascade,
    property_id    uuid not null references public.properties(id) on delete cascade,
    orden          integer not null default 0,
    comentario     text,
    calificacion   text check (calificacion in ('interesa', 'no_interesa')),
    calificada_at  timestamptz,
    created_at     timestamptz not null default now(),
    unique (coleccion_id, property_id)
);
alter table public.ficha_coleccion_items enable row level security;
create policy "default deny" on public.ficha_coleccion_items
    as restrictive for all using (false);

create index if not exists idx_ficha_coleccion_items_coleccion
    on public.ficha_coleccion_items (coleccion_id, orden);
create index if not exists idx_ficha_coleccion_items_property
    on public.ficha_coleccion_items (property_id);
