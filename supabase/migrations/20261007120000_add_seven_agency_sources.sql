-- New La Plata area sources for the precise /chat selector. Tokko is used by
-- Door Otero Rossi, Otero Rossi LP and Pablo Amado; the other four use the
-- generic public website crawler and page extraction.
ALTER TABLE public.properties DROP CONSTRAINT IF EXISTS properties_fuente_check;
ALTER TABLE public.properties ADD CONSTRAINT properties_fuente_check
    CHECK (fuente IN ('zonaprop', 'mercadolibre', 'googlemaps', 'instagram',
                     'argenprop', 'remax', 'inmobusqueda', 'mudafy', 'century21',
                     'mauroperri', 'urquiza', 'kwsuma', 'dacalbr', 'keymex',
                     'albertodacal', 'remaxroble', 'axion', 'sabella',
                     'yacoub', 'piazza', 'feysulaj', 'arraras', 'prado',
                     'reyesaversa', 'doorotero', 'oterorossilp', 'peterspuhl',
                     'pabloamado', 'jeronimoponce', 'manuelponce', 'manual'));

-- Preserve existing names, administrative zones and active/inactive choices.
INSERT INTO public.manual_sources (nombre, url)
SELECT source.nombre, source.url
FROM (VALUES
    ('Reyes Aversa Propiedades', 'https://www.reyesaversabienesraices.com.ar/'),
    ('Door Otero Rossi CB', 'https://www.oterorossi.com.ar/'),
    ('Otero Rossi LP', 'https://oterorossi.com/'),
    ('Peter Puhl Company', 'https://peterspuhlcompany.com/'),
    ('Pablo Amado Propiedades', 'https://www.pabloamado.com/'),
    ('Jerónimo Ponce Propiedades', 'https://jeronimoponcepropiedades.com.ar/'),
    ('Manuel Ponce Propiedades', 'https://manuelponce.com.ar/')
) AS source(nombre, url)
WHERE NOT EXISTS (
    SELECT 1 FROM public.manual_sources existing
    WHERE lower(split_part(regexp_replace(existing.url, '^https?://(www\.)?', '', 'i'), '/', 1))
        = lower(split_part(regexp_replace(source.url, '^https?://(www\.)?', '', 'i'), '/', 1))
);
