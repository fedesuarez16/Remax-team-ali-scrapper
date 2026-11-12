-- Borrado lógico de fichas de selección.
--
-- Borrar una selección desde /ficha-propio la saca de la lista del agente,
-- pero el link público /s/{id} que ya tiene el cliente tiene que seguir
-- andando. En vez de borrar la fila (y sus items por cascade), el endpoint
-- DELETE sella `archivada_at`; la lista filtra `archivada_at is null`.

alter table public.ficha_colecciones
    add column archivada_at timestamptz;

create index ficha_colecciones_activas_idx
    on public.ficha_colecciones (created_at desc)
    where archivada_at is null;
