ALTER TABLE public.properties
  ADD COLUMN lat double precision,
  ADD COLUMN lng double precision,
  ADD COLUMN geocoded_at timestamptz;

-- Backfill selector: rows never attempted
CREATE INDEX IF NOT EXISTS properties_geocode_pending_idx
  ON public.properties (created_at) WHERE geocoded_at IS NULL;

-- Map query: only located rows
CREATE INDEX IF NOT EXISTS properties_located_idx
  ON public.properties (lat, lng) WHERE lat IS NOT NULL AND lng IS NOT NULL;
