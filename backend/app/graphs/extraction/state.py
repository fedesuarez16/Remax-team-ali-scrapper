from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from app.models.property import Agency, NormalizedProperty, RawProperty, ScrapingFilters


class ScrapingState(TypedDict, total=False):
    query: str
    job_id: str
    filters: ScrapingFilters | None
    clarification_needed: bool

    # Phase 1 — portal scraping (fan-in reducer)
    collected_properties: Annotated[list[RawProperty], operator.add]
    normalized_properties: list[NormalizedProperty]
    errors: Annotated[list[str], operator.add]

    # Phase 1 — agency discovery (fan-in reducer)
    agencies: Annotated[list[Agency], operator.add]

    # Phase 2 — Instagram (set after interrupt resume)
    selected_agency_ids: list[str]
    instagram_properties: list[NormalizedProperty]
