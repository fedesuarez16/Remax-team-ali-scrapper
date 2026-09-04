-- Carpetas para agrupar Fichas Propio.
--
-- Las fichas propias (`properties` con `fuente='manual'`) se acumulan en una
-- sola lista y ya no se sabe qué se le mandó a cada cliente. Misma idea que
-- `search_history_folders`: carpeta con nombre libre, una ficha vive en UNA
-- carpeta o en ninguna.
--
-- La FK es `on delete set null` a propósito: borrar una carpeta jamás borra
-- fichas, sólo las devuelve a "Sin carpeta".
--
-- Orden: la tabla de carpetas tiene que existir antes de declarar la FK.

create table public.ficha_folders (
    id         uuid primary key default gen_random_uuid(),
    name       text not null,
    created_at timestamptz not null default now()
);
alter table public.ficha_folders enable row level security;

create policy "default deny" on public.ficha_folders
    as restrictive for all using (false);

alter table public.properties
    add column if not exists ficha_folder_id uuid
        references public.ficha_folders(id) on delete set null;

-- La pestaña Ficha Propio filtra por carpeta.
create index if not exists idx_properties_ficha_folder_id
    on public.properties (ficha_folder_id);
