-- Reviewed public catalogues. Prado uses its own InmoBúsqueda profile (2134)
-- while its website is under construction; it never searches the whole portal.
ALTER TABLE public.properties DROP CONSTRAINT IF EXISTS properties_fuente_check;
ALTER TABLE public.properties ADD CONSTRAINT properties_fuente_check
    CHECK (fuente IN ('zonaprop', 'mercadolibre', 'googlemaps', 'instagram',
                     'argenprop', 'remax', 'inmobusqueda', 'mudafy', 'century21',
                     'mauroperri', 'urquiza', 'kwsuma', 'dacalbr', 'keymex',
                     'albertodacal', 'remaxroble', 'axion', 'sabella',
                     'yacoub', 'piazza', 'feysulaj', 'arraras', 'prado', 'manual'));

-- Preserve existing names, administrative zones and active/inactive choices.
INSERT INTO public.manual_sources (nombre, url)
SELECT source.nombre, source.url
FROM (VALUES
    ('Yacoub', 'https://yacoub.com.ar/'),
    ('Piazza Propiedades', 'https://www.piazzapropiedades.com.ar/'),
    ('Feysulaj Propiedades', 'https://www.feysulaj.com.ar/'),
    ('Arrarás Propiedades', 'https://www.japropiedades.com.ar/'),
    ('Prado Propiedades', 'http://www.pradopropiedades.com.ar/')
) AS source(nombre, url)
WHERE NOT EXISTS (
    SELECT 1 FROM public.manual_sources existing
    WHERE lower(split_part(regexp_replace(existing.url, '^https?://(www\.)?', '', 'i'), '/', 1))
        = lower(split_part(regexp_replace(source.url, '^https?://(www\.)?', '', 'i'), '/', 1))
);
