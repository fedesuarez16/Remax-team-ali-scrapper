from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.graphs.extraction.nodes import (
    aggregate_results, clarification, deduplicate_properties,
    normalize_properties, parse_query, route_after_parse, run_scraper,
    save_to_db,
)
from app.graphs.extraction.state import ScrapingState


def build_graph(checkpointer: BaseCheckpointSaver[Any] | None = None) -> Any:  # type: ignore[type-arg]
    g = StateGraph(ScrapingState)
    g.add_node('parse_query', parse_query)
    g.add_node('run_scraper', run_scraper)  # type: ignore[arg-type,type-var]
    g.add_node('clarification', clarification)
    g.add_node('aggregate_results', aggregate_results)
    g.add_node('normalize_properties', normalize_properties)
    g.add_node('deduplicate_properties', deduplicate_properties)
    g.add_node('save_to_db', save_to_db)

    g.add_edge(START, 'parse_query')
    # conditional fan-out via Send list OR to clarification
    g.add_conditional_edges('parse_query', route_after_parse,
                            ['run_scraper', 'clarification'])
    g.add_edge('clarification', END)
    g.add_edge('run_scraper', 'aggregate_results')   # fan-in barrier
    g.add_edge('aggregate_results', 'normalize_properties')
    g.add_edge('normalize_properties', 'deduplicate_properties')
    g.add_edge('deduplicate_properties', 'save_to_db')
    g.add_edge('save_to_db', END)
    return g.compile(checkpointer=checkpointer)
