from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.graphs.extraction.nodes import (
    aggregate_phase1,
    clarification,
    deduplicate_properties,
    discover_agencies,
    extract_instagram_llm,
    no_instagram,
    normalize_properties,
    parse_query,
    review_agencies,
    route_after_parse,
    route_after_review,
    run_instagram_for_agency,
    run_portal_scraper,
    save_instagram_properties,
    save_portal_properties,
)
from app.graphs.extraction.state import ScrapingState


def build_graph(checkpointer: BaseCheckpointSaver[Any] | None = None) -> Any:  # type: ignore[type-arg]
    g = StateGraph(ScrapingState)

    # ── Phase 1 nodes ────────────────────────────────────────────────────────
    g.add_node('parse_query', parse_query)
    g.add_node('clarification', clarification)
    g.add_node('run_portal_scraper', run_portal_scraper)   # type: ignore[arg-type,type-var]
    g.add_node('discover_agencies', discover_agencies)      # type: ignore[arg-type,type-var]
    g.add_node('aggregate_phase1', aggregate_phase1)
    g.add_node('normalize_properties', normalize_properties)
    g.add_node('deduplicate_properties', deduplicate_properties)
    g.add_node('save_portal_properties', save_portal_properties)

    # ── Interrupt bridge ─────────────────────────────────────────────────────
    g.add_node('review_agencies', review_agencies)

    # ── Phase 2 nodes ────────────────────────────────────────────────────────
    g.add_node('run_instagram_for_agency', run_instagram_for_agency)  # type: ignore[arg-type,type-var]
    g.add_node('extract_instagram_llm', extract_instagram_llm)
    g.add_node('save_instagram_properties', save_instagram_properties)
    g.add_node('no_instagram', no_instagram)

    # ── Edges ────────────────────────────────────────────────────────────────
    g.add_edge(START, 'parse_query')
    g.add_conditional_edges('parse_query', route_after_parse,
                            ['run_portal_scraper', 'discover_agencies', 'clarification'])
    g.add_edge('clarification', END)

    # Fan-in: both portal scrapers and agency discovery converge
    g.add_edge('run_portal_scraper', 'aggregate_phase1')
    g.add_edge('discover_agencies', 'aggregate_phase1')

    g.add_edge('aggregate_phase1', 'normalize_properties')
    g.add_edge('normalize_properties', 'deduplicate_properties')
    g.add_edge('deduplicate_properties', 'save_portal_properties')
    g.add_edge('save_portal_properties', 'review_agencies')

    # Interrupt bridge → Phase 2
    g.add_conditional_edges('review_agencies', route_after_review,
                            ['run_instagram_for_agency', 'no_instagram'])
    g.add_edge('no_instagram', END)

    # Phase 2 flow
    g.add_edge('run_instagram_for_agency', 'extract_instagram_llm')
    g.add_edge('extract_instagram_llm', 'save_instagram_properties')
    g.add_edge('save_instagram_properties', END)

    return g.compile(checkpointer=checkpointer)
