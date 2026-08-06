-- ESTADO DE PORTALES — activar/desactivar cada portal inmobiliario del catálogo.
--
-- Los portales (Zonaprop, Argenprop, Mercado Libre, RE/MAX) son un catálogo
-- FIJO en código (frontend/lib/sources.ts · backend PORTAL_SOURCES). Esta tabla
-- guarda SOLO el estado activo/inactivo de cada uno, para poder mostrar en la
-- pantalla de Fuentes cuántos portales están activos vs. inactivos — el mismo
-- estilo que la tarjeta "Campañas Activas" del CRM.
--
-- Fila por portal, id = el mismo slug que usa el frontend. Vive en la base (no
-- en memoria) para que sobreviva a un restart del backend.
--
-- NOTA: hoy este estado es de CONFIGURACIÓN/DISPLAY. El fan-out de búsqueda
-- (route_after_parse en backend/app/graphs/extraction/nodes.py) todavía NO lo
-- consulta; sigue gobernado por los env gates + la selección por búsqueda.
create table if not exists public.portal_settings (
    id         text primary key,
    activo     boolean not null default true,
    updated_at timestamptz not null default now()
);

alter table public.portal_settings enable row level security;

drop policy if exists "default deny" on public.portal_settings;
create policy "default deny" on public.portal_settings
    as restrictive for all using (false);

-- Semilla: los 4 portales del catálogo, todos activos por defecto.
insert into public.portal_settings (id) values
    ('zonaprop'),
    ('argenprop'),
    ('mercadolibre'),
    ('remax')
    on conflict (id) do nothing;
