from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.graphs.extraction.nodes import (
    aggregate_phase1,
    clarification,
    deduplicate_properties,
    discover_agencies,
    extract_website_properties_llm,
    no_websites,
    normalize_properties,
    parse_query,
    review_agencies,
    route_after_parse,
    route_after_review,
    run_portal_scraper,
    run_website_scraper,
    save_portal_properties,
    save_website_properties,
)
from app.graphs.extraction.state import ScrapingState


def build_graph(checkpointer: BaseCheckpointSaver[Any] | None = None) -> Any:  # type: ignore[type-arg]
    g = StateGraph(ScrapingState)

    # ── Phase 1 ───────────────────────────────────────────────────────────────
    g.add_node('parse_query', parse_query)
    g.add_node('clarification', clarification)
    g.add_node('run_portal_scraper', run_portal_scraper)   # type: ignore[arg-type,type-var]
    g.add_node('discover_agencies', discover_agencies)      # type: ignore[arg-type,type-var]
    g.add_node('aggregate_phase1', aggregate_phase1)
    g.add_node('normalize_properties', normalize_properties)
    g.add_node('deduplicate_properties', deduplicate_properties)
    g.add_node('save_portal_properties', save_portal_properties)

    # ── Interrupt bridge ──────────────────────────────────────────────────────
    g.add_node('review_agencies', review_agencies)

    # ── Phase 2 ───────────────────────────────────────────────────────────────
    g.add_node('run_website_scraper', run_website_scraper)  # type: ignore[arg-type,type-var]
    g.add_node('extract_website_properties_llm', extract_website_properties_llm)
    g.add_node('save_website_properties', save_website_properties)
    g.add_node('no_websites', no_websites)

    # ── Edges ─────────────────────────────────────────────────────────────────
    g.add_edge(START, 'parse_query')
    g.add_conditional_edges('parse_query', route_after_parse,
                            ['run_portal_scraper', 'discover_agencies', 'clarification'])
    g.add_edge('clarification', END)

    g.add_edge('run_portal_scraper', 'aggregate_phase1')
    g.add_edge('discover_agencies', 'aggregate_phase1')
    g.add_edge('aggregate_phase1', 'normalize_properties')
    g.add_edge('normalize_properties', 'deduplicate_properties')
    g.add_edge('deduplicate_properties', 'save_portal_properties')
    g.add_edge('save_portal_properties', 'review_agencies')

    g.add_conditional_edges('review_agencies', route_after_review,
                            ['run_website_scraper', 'no_websites'])
    g.add_edge('no_websites', END)

    g.add_edge('run_website_scraper', 'extract_website_properties_llm')
    g.add_edge('extract_website_properties_llm', 'save_website_properties')
    g.add_edge('save_website_properties', END)

    return g.compile(checkpointer=checkpointer)
