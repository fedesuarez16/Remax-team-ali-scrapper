-- Every scraped property now links to its search job; this flag marks which
-- ones satisfy the user's criteria so results order matched-first without
-- dropping the rest. Existing links predate the flag → default TRUE.
ALTER TABLE public.search_property_results
    ADD COLUMN IF NOT EXISTS matches_criteria BOOLEAN NOT NULL DEFAULT TRUE;
