-- INMOBUSQUEDA + MUDAFY — nuevos portales del catálogo fijo.
--
-- InmoBusqueda: portal server-rendered (sin WAF, sin actor de Apify), se
-- scrapea por httpx directo y pagina por URL. Es el único del catálogo con
-- profundidad real en el partido de La Plata (Manuel B Gonnet, City Bell,
-- Villa Elisa, casco urbano) — justo donde Zonaprop y Argenprop se quedan
-- cortos.
--
-- Mudafy: sitio Next.js; los datos viajan en el payload RSC, no en el DOM.
-- Volumen bajo, pero es el payload mas rico del catalogo (calle y altura por
-- separado, coordenadas, ambientes/banos/cocheras), y esa direccion
-- estructurada es justo lo que alimenta el fingerprint de deduplicacion.
--
-- Dos cosas cambian acá:
--  1. `properties_fuente_check` acepta la nueva fuente (mismo patrón que la
--     migración 20260718000000 cuando entró RE/MAX).
--  2. `portal_settings` recibe su fila, activa por defecto como el resto.
--
-- El set de ids sigue viviendo en código (backend/app/services/apify.py
-- PORTAL_SOURCES · backend/app/api/v1/portals.py PORTAL_CATALOG ·
-- frontend/lib/sources.ts PORTALES); esta tabla sólo guarda el on/off.

ALTER TABLE public.properties DROP CONSTRAINT properties_fuente_check;
ALTER TABLE public.properties ADD CONSTRAINT properties_fuente_check
    CHECK (fuente IN ('zonaprop','mercadolibre','googlemaps','instagram',
                      'argenprop','remax','inmobusqueda','mudafy','manual'));

INSERT INTO public.portal_settings (id) VALUES
    ('inmobusqueda'),
    ('mudafy')
    ON CONFLICT (id) DO NOTHING;
