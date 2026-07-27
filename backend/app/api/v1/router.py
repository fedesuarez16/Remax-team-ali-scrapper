from fastapi import APIRouter

from app.api.v1 import properties, leads, scraping, agents, geo, search_history, manual_sources

api_router = APIRouter()

api_router.include_router(properties.router, prefix="/properties", tags=["properties"])
api_router.include_router(leads.router, prefix="/leads", tags=["leads"])
api_router.include_router(scraping.router, prefix="/scraping", tags=["scraping"])
api_router.include_router(agents.router, prefix="/agents", tags=["agents"])
api_router.include_router(geo.router, prefix="/geo", tags=["geo"])
api_router.include_router(search_history.router, prefix="/search-history", tags=["search-history"])
api_router.include_router(manual_sources.router, prefix="/manual-sources", tags=["manual-sources"])
