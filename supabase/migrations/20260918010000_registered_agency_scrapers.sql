-- KW Suma uses Tokko's current public website template and is scraped with
-- exact location ids from the site's own catalogue.
ALTER TABLE public.properties DROP CONSTRAINT IF EXISTS properties_fuente_check;
ALTER TABLE public.properties ADD CONSTRAINT properties_fuente_check
    CHECK (fuente IN ('zonaprop', 'mercadolibre', 'googlemaps', 'instagram',
                     'argenprop', 'remax', 'inmobusqueda', 'mudafy', 'century21',
                     'mauroperri', 'urquiza', 'kwsuma', 'dacalbr', 'keymex',
                     'albertodacal', 'remaxroble', 'axion', 'sabella', 'manual'));

INSERT INTO public.manual_sources (nombre, url)
SELECT 'KW Suma', 'https://www.kwsuma.com.ar/'
WHERE NOT EXISTS (
    SELECT 1 FROM public.manual_sources existing
    WHERE lower(split_part(regexp_replace(existing.url, '^https?://(www\.)?', '', 'i'), '/', 1))
        = 'kwsuma.com.ar'
);

-- Dacal Bienes Raíces publishes its inventory and exact location hierarchy
-- through the same public JSON API used by its website.
INSERT INTO public.manual_sources (nombre, url)
SELECT 'Dacal Bienes Raíces', 'https://www.dacalbienesraices.com.ar/'
WHERE NOT EXISTS (
    SELECT 1 FROM public.manual_sources existing
    WHERE lower(split_part(regexp_replace(existing.url, '^https?://(www\.)?', '', 'i'), '/', 1))
        = 'dacalbienesraices.com.ar'
);

INSERT INTO public.manual_sources (nombre, url)
SELECT 'Keymex La Plata', 'https://www.keymexlaplata.com.ar/'
WHERE NOT EXISTS (
    SELECT 1 FROM public.manual_sources existing
    WHERE lower(split_part(regexp_replace(existing.url, '^https?://(www\.)?', '', 'i'), '/', 1))
        = 'keymexlaplata.com.ar'
);

-- Alberto Dacal's Brokian catalogue exposes exact hierarchical routes such as
-- G.B.A. Zona Sur -> La Plata -> City Bell, which the dedicated scraper uses.
INSERT INTO public.manual_sources (nombre, url)
SELECT 'Alberto Dacal Propiedades', 'https://dacal.com.ar/'
WHERE NOT EXISTS (
    SELECT 1 FROM public.manual_sources existing
    WHERE lower(split_part(regexp_replace(existing.url, '^https?://(www\.)?', '', 'i'), '/', 1))
        = 'dacal.com.ar'
);

-- The API is filtered by the reviewed UUIDs for Roble and Roble II; this URL
-- is the office landing page, not the unscoped RE/MAX portal root.
INSERT INTO public.manual_sources (nombre, url)
SELECT 'RE/MAX Roble', 'https://www.remax.com.ar/roble'
WHERE NOT EXISTS (
    SELECT 1 FROM public.manual_sources existing
    WHERE lower(regexp_replace(existing.url, '/+$', '')) = 'https://www.remax.com.ar/roble'
       OR lower(regexp_replace(existing.url, '/+$', '')) = 'https://remax.com.ar/roble'
);

INSERT INTO public.manual_sources (nombre, url)
SELECT 'Axion Group', 'https://www.axionpropiedades.com/'
WHERE NOT EXISTS (
    SELECT 1 FROM public.manual_sources existing
    WHERE lower(split_part(regexp_replace(existing.url, '^https?://(www\.)?', '', 'i'), '/', 1))
        = 'axionpropiedades.com'
);

INSERT INTO public.manual_sources (nombre, url)
SELECT 'Sabella Propiedades', 'https://www.sabellapropiedades.com.ar/'
WHERE NOT EXISTS (
    SELECT 1 FROM public.manual_sources existing
    WHERE lower(split_part(regexp_replace(existing.url, '^https?://(www\.)?', '', 'i'), '/', 1))
        = 'sabellapropiedades.com.ar'
);
