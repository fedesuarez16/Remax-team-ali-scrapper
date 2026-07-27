ALTER TABLE public.properties DROP CONSTRAINT properties_fuente_check;
ALTER TABLE public.properties ADD CONSTRAINT properties_fuente_check CHECK (fuente IN ('zonaprop','mercadolibre','googlemaps','instagram','argenprop','remax','manual'));
