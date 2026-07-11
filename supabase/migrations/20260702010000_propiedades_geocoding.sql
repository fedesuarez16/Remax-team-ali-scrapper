-- Cartera propia (tabla propiedades): coordenadas para el mapa, mismo patrón que properties.
ALTER TABLE public.propiedades
  ADD COLUMN lat double precision,
  ADD COLUMN lng double precision,
  ADD COLUMN geocoded_at timestamptz;
