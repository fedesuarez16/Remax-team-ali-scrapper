from fastapi import APIRouter

from app.api.v1 import (
    properties, leads, scraping, agents, geo, search_history, saved_zones, manual_sources,
    portals, cleanup, ficha_settings, metrics,
)

api_router = APIRouter()

api_router.include_router(properties.router, prefix="/properties", tags=["properties"])
api_router.include_router(leads.router, prefix="/leads", tags=["leads"])
api_router.include_router(scraping.router, prefix="/scraping", tags=["scraping"])
api_router.include_router(agents.router, prefix="/agents", tags=["agents"])
api_router.include_router(geo.router, prefix="/geo", tags=["geo"])
api_router.include_router(search_history.router, prefix="/search-history", tags=["search-history"])
api_router.include_router(saved_zones.router, prefix="/saved-zones", tags=["saved-zones"])
api_router.include_router(manual_sources.router, prefix="/manual-sources", tags=["manual-sources"])
api_router.include_router(portals.router, prefix="/portals", tags=["portals"])
api_router.include_router(cleanup.router, prefix="/cleanup", tags=["cleanup"])
api_router.include_router(ficha_settings.router, prefix="/ficha-settings", tags=["ficha-settings"])
api_router.include_router(metrics.router, prefix="/metrics", tags=["metrics"])
