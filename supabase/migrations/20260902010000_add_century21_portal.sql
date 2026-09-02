-- CENTURY 21 — alta como PORTAL del catálogo fijo, baja como inmobiliaria.
--
-- C21 es una franquicia: cada oficina ("CENTURY 21 Alianza Urbana S.A.
-- (Gonnet)") es un negocio con dirección propia, así que el track de Google
-- Maps la devolvía como una inmobiliaria más para "inmobiliarias en {zona}",
-- le mandaba el crawler genérico y terminaba leyendo — oficina por oficina,
-- sin filtro de zona server-side — el MISMO inventario que century21.com.ar
-- publica entero detrás de una sola API pública. Es el caso RE/MAX otra vez.
--
-- El costo no era sólo el crawler: la misma propiedad entraba N veces (una
-- por oficina que la publica) y las sucursales inflaban el conteo de
-- "inmobiliarias en la zona".
--
-- El scraper usa la API propia del portal, sin Apify. century21.com.ar es una
-- SPA (el HTML de /v/resultados no trae un solo /propiedad/), pero la MISMA
-- URL con `?json=true` devuelve el JSON que la SPA consume — público, sin auth
-- y sin WAF. La ubicación se resuelve contra `/v/busqueda?q=...`, cuyo
-- autocompletado devuelve el tramo de path ya armado, con nivel de barrio
-- (`en-colonia_`) y hasta de barrio cerrado (`en-division_`).
--
-- Dos cosas cambian acá:
--  1. `properties_fuente_check` acepta la nueva fuente (mismo patrón que la
--     migración 20260805010000 cuando entraron InmoBusqueda y Mudafy).
--  2. `portal_settings` recibe su fila, activa por defecto como el resto.
--
-- El set de ids sigue viviendo en código (backend/app/services/apify.py
-- PORTAL_SOURCES · backend/app/api/v1/portals.py PORTAL_CATALOG ·
-- frontend/lib/sources.ts PORTALES); esta tabla sólo guarda el on/off. La baja
-- del track de inmobiliarias vive en `agency_is_portal_brand` — las filas de
-- oficinas C21 ya guardadas se limpian abajo, porque el caché de agencias
-- tiene 30 días de TTL y si no seguirían volviendo sin pasar nunca por la
-- guarda nueva.

ALTER TABLE public.properties DROP CONSTRAINT properties_fuente_check;
ALTER TABLE public.properties ADD CONSTRAINT properties_fuente_check
    CHECK (fuente IN ('zonaprop','mercadolibre','googlemaps','instagram',
                      'argenprop','remax','inmobusqueda','mudafy','century21',
                      'manual'));

INSERT INTO public.portal_settings (id) VALUES
    ('century21')
    ON CONFLICT (id) DO NOTHING;

-- Las oficinas C21 ya cacheadas. `real_estate_agencies` es un caché
-- read-through con TTL de 30 días (migración 20260702020000): sin este
-- borrado, las filas viejas siguen volviendo de caché sin pasar nunca por
-- `agency_is_portal_brand`, y la guarda nueva no se nota hasta que expiren.
--
-- Mismo criterio que la guarda de código: la MARCA en el nombre o el dominio,
-- anclada al nombre y nunca al número suelto — hay inmobiliarias que se
-- llaman "Calle 21" o "Grupo 21".
DELETE FROM public.real_estate_agencies
 WHERE nombre ~* '(^|[^a-z0-9])century[ -]?21([^a-z0-9]|$)'
    OR nombre ~* '(^|[^a-z0-9])c21([^a-z0-9]|$)'
    OR sitio_web ILIKE '%century21.com%'
    OR sitio_web ILIKE '%21online.lat%';
