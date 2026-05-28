from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from app.models.property import NormalizedProperty, RawProperty, ScrapingFilters


class ScrapingState(TypedDict, total=False):
    query: str
    job_id: str
    filters: ScrapingFilters | None
    clarification_needed: bool
    # fan-in reducer: each run_scraper branch returns its slice, merged via +
    collected_properties: Annotated[list[RawProperty], operator.add]
    normalized_properties: list[NormalizedProperty]
    errors: Annotated[list[str], operator.add]
