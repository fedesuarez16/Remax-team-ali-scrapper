"""The filter extractor must qualify a barrio with its city/partido.

This is the contract the whole candidate chain rests on. "el casco de La
Plata" reached the scrapers as the bare barrio "Casco Urbano" — the "La
Plata" the user actually typed was dropped — and a bare barrio is ambiguous
in exactly the way that breaks portal resolution:

  - Argenprop's autocomplete answers "Casco Urbano" with a barrio in SAN LUIS
  - RE/MAX's only literal match is the gated community "Los Eucaliptus Casco
    Urbano", not the casco at all

Both are rejected once the phrase carries ", La Plata", because the guard and
both resolvers require EVERY comma part to match. And a qualified zona is
what gives `zona_candidates` a localidad to fall back to — a bare one has
nowhere to degrade.

Prompt wording is not asserted verbatim; what is pinned is that the composite
format and its rationale are still being asked for.
"""
from app.graphs.extraction.tools import EXTRACT_FILTERS_TOOL, SYSTEM_PROMPT
from app.services.zona import zona_candidates


def _zonas_description() -> str:
    return EXTRACT_FILTERS_TOOL['input_schema']['properties']['zonas']['description']


class TestTheToolAsksForQualifiedZonas:
    def test_schema_documents_the_composite_format(self):
        desc = _zonas_description().lower()
        assert 'coma' in desc or ',' in desc
        assert 'partido' in desc or 'ciudad' in desc

    def test_schema_example_is_composite(self):
        """An example of a BARE barrio would teach exactly the wrong shape."""
        assert 'City Bell, La Plata' in _zonas_description()

    def test_prompt_explains_why_a_bare_barrio_is_ambiguous(self):
        prompt = SYSTEM_PROMPT.lower()
        assert 'barrio' in prompt
        assert 'ambiguo' in prompt or 'ambigua' in prompt

    def test_prompt_carries_the_motivating_example(self):
        assert 'Casco Urbano, La Plata' in SYSTEM_PROMPT

    def test_prompt_keeps_a_bare_city_bare(self):
        """Over-qualifying is its own bug: "La Plata" must not become
        "La Plata, Buenos Aires" and start degrading to the province."""
        assert 'La Plata" → "La Plata' in SYSTEM_PROMPT


class TestTheContractIsWhatTheChainNeeds:
    def test_qualified_zona_has_a_localidad_to_fall_back_to(self):
        assert zona_candidates('Casco Urbano, La Plata') == [
            'Casco Urbano, La Plata', 'La Plata',
        ]

    def test_bare_barrio_has_nowhere_to_degrade(self):
        """The regression this contract prevents."""
        assert zona_candidates('Casco Urbano') == ['Casco Urbano']


class TestElPartidoNoEsLaCabecera:
    """"La Plata" and "La Plata, La Plata" are DIFFERENT PLACES on the portals.

    The bare name is the partido — 900 km² including City Bell, Villa Elisa,
    Gonnet and Tolosa. The doubled one is the localidad inside it: the casco.
    ZonaProp gives them different pages (`/casas-venta-la-plata-...` vs
    `/casas-venta-la-plata-la-plata-...`), and the user's own URLs use the
    doubled slug for the casco.

    The prompt only knew how to keep a bare city bare, so "casas en la plata,
    la plata" collapsed to "La Plata": the scraper fetched the PARTIDO page,
    the guard got a single-part phrase that roams the whole record, `city`
    (the partido) satisfied it for every listing, and a casco search came
    back full of City Bell and Villa Elisa.
    """

    def test_schema_teaches_the_doubled_form(self):
        assert 'La Plata, La Plata' in _zonas_description()

    def test_prompt_teaches_the_doubled_form(self):
        assert 'La Plata, La Plata' in SYSTEM_PROMPT

    def test_prompt_says_the_bare_name_is_the_partido(self):
        prompt = SYSTEM_PROMPT.lower()
        assert 'partido' in prompt
        assert 'casco' in prompt or 'cabecera' in prompt

    def test_a_bare_partido_is_still_left_bare(self):
        """Over-qualifying stays a bug: a user who wants the whole partido
        must not be silently narrowed to the casco."""
        assert 'La Plata" → "La Plata' in SYSTEM_PROMPT


class TestTheDoubledZonaBehavesDownstream:
    def test_it_degrades_to_the_partido_and_stops(self):
        assert zona_candidates('La Plata, La Plata') == [
            'La Plata, La Plata', 'La Plata',
        ]

    def test_the_guard_reads_it_as_localidad_plus_partido(self):
        """The doubled phrase is what lets `_item_matches_zona` apply the
        localidad/partido split at all — a bare name has no split to make."""
        from app.models.property import ScrapingFilters
        from app.services.apify import _guard_phrases, _item_matches_zona

        f = ScrapingFilters(zona='La Plata, La Plata', zona_pedida='La Plata, La Plata')
        casco = {'neighborhood': 'La Plata', 'city': 'La Plata',
                 'address': 'Calle 7 500', 'title': 'Casa', 'description': ''}
        city_bell = {'neighborhood': 'City Bell', 'city': 'La Plata',
                     'address': 'Calle 13 470', 'title': 'Casa', 'description': ''}

        assert _item_matches_zona(casco, _guard_phrases(f))
        assert not _item_matches_zona(city_bell, _guard_phrases(f))

    def test_the_bare_partido_keeps_everything_in_it(self):
        """And the contrast that makes the distinction worth drawing."""
        from app.models.property import ScrapingFilters
        from app.services.apify import _guard_phrases, _item_matches_zona

        f = ScrapingFilters(zona='La Plata', zona_pedida='La Plata')
        city_bell = {'neighborhood': 'City Bell', 'city': 'La Plata',
                     'address': 'Calle 13 470', 'title': 'Casa', 'description': ''}

        assert _item_matches_zona(city_bell, _guard_phrases(f))
