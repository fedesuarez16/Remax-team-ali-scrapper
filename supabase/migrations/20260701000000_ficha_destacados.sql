-- Ficha enrichment: LLM-parsed highlights from the free-text description.
-- destacados = [{ "label": "Orientación", "value": "Norte" }, ...] rendered as boxes.
-- ficha_enriched marks a property whose description was already parsed (idempotency).
ALTER TABLE public.properties
  ADD COLUMN destacados jsonb NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN ficha_enriched boolean NOT NULL DEFAULT false;
