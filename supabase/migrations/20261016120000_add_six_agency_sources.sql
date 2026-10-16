-- Six additional La Plata agency sources for the precise /chat selector.
ALTER TABLE public.properties DROP CONSTRAINT IF EXISTS properties_fuente_check;
ALTER TABLE public.properties ADD CONSTRAINT properties_fuente_check
    CHECK (fuente IN ('zonaprop', 'mercadolibre', 'googlemaps', 'instagram',
                     'argenprop', 'remax', 'inmobusqueda', 'mudafy', 'century21',
                     'mauroperri', 'urquiza', 'kwsuma', 'dacalbr', 'keymex',
                     'albertodacal', 'remaxroble', 'axion', 'sabella',
                     'yacoub', 'piazza', 'feysulaj', 'arraras', 'prado',
                     'reyesaversa', 'doorotero', 'oterorossilp', 'peterspuhl',
                     'pabloamado', 'jeronimoponce', 'manuelponce', 'babenco',
                     'nbabenco', 'menacho', 'menachocanale', 'matiasgalan', 'gesino',
                     'manual'));

-- Keep existing names, administrative zones, and active/inactive choices.
INSERT INTO public.manual_sources (nombre, url)
SELECT source.nombre, source.url
FROM (VALUES
    ('Babenco Propiedades', 'https://www.babencopropiedades.com.ar/'),
    ('N Babenco Propiedades', 'https://www.nbabencopropiedades.com.ar/'),
    ('Menacho', 'https://www.menacho.com.ar/'),
    ('Menacho Canale', 'https://www.menachocanale.com.ar/'),
    ('Matias Galan Propiedades', 'https://www.matiasgalan.com.ar/'),
    ('Gesino Propiedades', 'https://gesinonegociosinmobiliarios.com/')
) AS source(nombre, url)
WHERE NOT EXISTS (
    SELECT 1 FROM public.manual_sources existing
    WHERE lower(split_part(regexp_replace(existing.url, '^https?://(www\.)?', '', 'i'), '/', 1))
        = lower(split_part(regexp_replace(source.url, '^https?://(www\.)?', '', 'i'), '/', 1))
);
