from __future__ import annotations

import asyncio

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.geocode import (
    RATE_LIMIT_SECONDS, TransientGeocodeError, reverse_geocode, reverse_geocode_pair,
)
from app.services.zona import normalize_zona

router = APIRouter()

# Defense-in-depth cap — the frontend already caps `samplePolygon` output at 8
# points, but a malicious/buggy client could send more; bound the cost here too.
MAX_REVERSE_ZONAS_POINTS = 8


class ReverseGeocodeRequest(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    zoom: int = 14


class ReverseGeocodeResponse(BaseModel):
    zona: str | None
    display_name: str | None
    lat: float
    lng: float


@router.post('/reverse')
async def reverse(body: ReverseGeocodeRequest) -> ReverseGeocodeResponse:
    """Resolve a single lat/lng centroid to a zona name (see `reverse_geocode`).

    Returns 200 with `zona: null` when Nominatim has no usable address
    component (an "unresolved" result, distinct from a transient failure),
    and 503 when Nominatim itself is throttled/erroring/timing out.
    """
    try:
        async with httpx.AsyncClient() as client:
            zona = await reverse_geocode(body.lat, body.lng, client=client, zoom=body.zoom)
    except TransientGeocodeError:
        raise HTTPException(status_code=503, detail='reverse geocoding temporarily unavailable')
    return ReverseGeocodeResponse(zona=zona, display_name=zona, lat=body.lat, lng=body.lng)


class LatLng(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class ReverseZonasRequest(BaseModel):
    points: list[LatLng] = Field(min_length=1)
    zoom: int = 14


class ReverseZonasResponse(BaseModel):
    zonas: list[str]
    localidades: list[str]
    requested: int
    resolved: int
    failed: int


@router.post('/reverse-zonas')
async def reverse_zonas(body: ReverseZonasRequest) -> ReverseZonasResponse:
    """Resolve a sampled set of points (centroid + capped vertices) to a deduped
    list of zona (barrio) names, plus a deduped list of localidad (partido/
    ciudad) names — both extracted from the SAME Nominatim response per point
    (zero extra calls, ADR-2).

    Points are resolved STRICTLY sequentially with a throttle pause between
    calls (never in parallel) to respect Nominatim's usage policy. A per-point
    `TransientGeocodeError` is skipped (does not abort the whole request) so a
    single 429 doesn't waste the rest of the already-sampled points. Only when
    ALL points fail transiently (zero zonas resolved AND failed>0) do we
    surface a 503 — an unresolvable-but-not-erroring point (`None`) is not a
    failure, just "unresolved".
    """
    points = body.points[:MAX_REVERSE_ZONAS_POINTS]
    requested = len(points)
    resolved = 0
    failed = 0
    zonas: dict[str, str] = {}  # normalize_zona(name) -> first-seen surface name
    localidades: dict[str, str] = {}  # normalize_zona(name) -> first-seen surface name

    async with httpx.AsyncClient() as client:
        for i, point in enumerate(points):
            try:
                barrio, localidad = await reverse_geocode_pair(
                    point.lat, point.lng, client=client, zoom=body.zoom,
                )
            except TransientGeocodeError:
                failed += 1
                barrio, localidad = None, None
                ok = False
            else:
                ok = True
            if ok and barrio:
                resolved += 1
                key = normalize_zona(barrio)
                if key and key not in zonas:
                    zonas[key] = barrio
            if ok and localidad:
                loc_key = normalize_zona(localidad)
                if loc_key and loc_key not in localidades:
                    localidades[loc_key] = localidad
            if i < len(points) - 1:
                await asyncio.sleep(RATE_LIMIT_SECONDS)

    zona_list = list(zonas.values())
    localidad_list = list(localidades.values())
    if not zona_list and failed > 0:
        raise HTTPException(status_code=503, detail='reverse geocoding temporarily unavailable')
    return ReverseZonasResponse(
        zonas=zona_list, localidades=localidad_list,
        requested=requested, resolved=resolved, failed=failed,
    )
