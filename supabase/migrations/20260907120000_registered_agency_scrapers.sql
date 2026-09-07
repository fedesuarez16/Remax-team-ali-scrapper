-- Source identity is independent from the adapter shared by the agency sites.
ALTER TABLE public.properties DROP CONSTRAINT properties_fuente_check;
ALTER TABLE public.properties ADD CONSTRAINT properties_fuente_check
    CHECK (fuente IN ('zonaprop', 'mercadolibre', 'googlemaps', 'instagram',
                     'argenprop', 'remax', 'inmobusqueda', 'mudafy', 'century21',
                     'mauroperri', 'urquiza', 'manual'));

-- Keep any existing names, activation flags and manually classified zonas.
-- No inferred coverage: the scraper filters the actual properties by location.
INSERT INTO public.manual_sources (nombre, url)
SELECT source.nombre, source.url
FROM (VALUES
    ('Mauro Perri Bienes Raíces', 'https://www.mauroperribienesraices.com.ar/'),
    ('Urquiza Propiedades', 'https://www.urquiza.com.ar/')
) AS source(nombre, url)
WHERE NOT EXISTS (
    SELECT 1 FROM public.manual_sources existing
    WHERE lower(split_part(regexp_replace(existing.url, '^https?://(www\.)?', '', 'i'), '/', 1))
        = lower(split_part(regexp_replace(source.url, '^https?://(www\.)?', '', 'i'), '/', 1))
);

-- InmoBúsqueda already belongs to portal_settings. Its root URL is also
-- recognized by the adapter registry when selected as an existing manual source.
