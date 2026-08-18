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
