-- Semilla de los portales agregados al catálogo DESPUÉS de crear portal_settings:
-- InmoBusqueda y Mudafy (ver PORTAL_CATALOG en backend/app/api/v1/portals.py).
--
-- La migración original 20260804010000 sólo sembró los 4 portales que existían
-- entonces y YA está aplicada, así que no se toca. Esta migración nueva agrega
-- las filas faltantes. Sin esto igual funcionan (GET /portals cae a activo=true
-- para un portal sin fila), pero dejamos el estado inicial completo y explícito.
insert into public.portal_settings (id) values
    ('inmobusqueda'),
    ('mudafy')
    on conflict (id) do nothing;
