"""Dormitorios deserve their own field, because the portals filter on them.

`ScrapingFilters` only had `ambientes_*`, and the extractor squashed
"2 dormitorios" into `ambientes_min=2` — documented as a deliberate
under-approximation ("a 3-bedroom home usually has 4+ ambientes"). Fine while
nothing consumed it; not fine once ZonaProp's URL can filter, because the two
are different filters on the same page:

    -2-habitaciones-   bedrooms  →  523 listings
    -2-ambientes-      rooms     → 1012 listings
    -mas-de-2-ambientes-         → 1703 listings

Asking for "2 dormitorios" and sending the ambientes floor is three times
wider than the question — a 1-bedroom, 2-ambiente flat comes back as a match.
The information exists in the query; it was being thrown away before anything
could use it.
"""
from app.graphs.extraction.tools import EXTRACT_FILTERS_TOOL, SYSTEM_PROMPT
from app.models.property import ScrapingFilters


def _props() -> dict:
    return EXTRACT_FILTERS_TOOL['input_schema']['properties']


class TestTheModelHoldsThem:
    def test_the_filters_carry_dormitorios(self):
        f = ScrapingFilters(dormitorios_min=2, dormitorios_max=3)
        assert (f.dormitorios_min, f.dormitorios_max) == (2, 3)

    def test_they_default_to_absent(self):
        f = ScrapingFilters()
        assert f.dormitorios_min is None
        assert f.dormitorios_max is None

    def test_ambientes_still_exist_separately(self):
        """Someone who says "3 ambientes" means ambientes."""
        f = ScrapingFilters(ambientes_min=3)
        assert f.ambientes_min == 3
        assert f.dormitorios_min is None


class TestTheExtractorIsAskedForThem:
    def test_the_schema_exposes_the_fields(self):
        assert 'dormitorios_min' in _props()
        assert 'dormitorios_max' in _props()

    def test_the_schema_says_they_are_not_ambientes(self):
        blob = ' '.join(
            str(_props()[k].get('description', '')) for k in ('dormitorios_min', 'ambientes_min')
        ).lower()
        assert 'ambiente' in blob
        assert 'dormitorio' in blob

    def test_the_prompt_routes_dormitorios_to_its_own_field(self):
        assert 'dormitorios_min' in SYSTEM_PROMPT

    def test_the_prompt_no_longer_sends_dormitorios_to_ambientes(self):
        """The old rule — `"3 dormitorios" → ambientes_min=3` — is exactly the
        conflation that cost the precision."""
        assert '"3 dormitorios" → ambientes_min=3' not in SYSTEM_PROMPT
