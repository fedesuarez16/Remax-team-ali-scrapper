from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from app.models.property import Agency, NormalizedProperty, RawProperty, ScrapingFilters


class ScrapingState(TypedDict, total=False):
    query: str
    job_id: str
    filters: ScrapingFilters | None
    clarification_needed: bool
    # From /start via stream_scraping's `inputs`; drives per-localidad fan-out in
    # route_after_parse. `polygon` is injected into `inputs` too (spec) but stays
    # OUT of graph state (ADR-3) — it's dead state here, consumed authoritatively
    # by GET /{job_id}/properties instead.
    localidades: list[str]

    # Phase 1 — portal scraping (fan-in reducer)
    collected_properties: Annotated[list[RawProperty], operator.add]
    normalized_properties: list[NormalizedProperty]
    errors: Annotated[list[str], operator.add]

    # Phase 1 — agency discovery (fan-in reducer)
    agencies: Annotated[list[Agency], operator.add]

    # Phase 2 — Website + Instagram scraping (set after interrupt resume)
    selected_agency_ids: list[str]
    website_pages: Annotated[list[dict], operator.add]
    website_properties: list[NormalizedProperty]
    instagram_posts: Annotated[list[dict], operator.add]
    instagram_properties: list[NormalizedProperty]
