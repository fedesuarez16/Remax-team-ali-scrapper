"""Pure point-in-polygon classification (ray-casting). No external deps —
used to classify scraped properties as inside/outside a user-drawn polygon
(`GET /{job_id}/properties`, ADR-3: polygon classification lives in the
endpoint, not graph state).

Polygon points are ``[lat, lng]`` pairs, matching the shape persisted on
`scraping_jobs.polygon`.
"""
from __future__ import annotations

from typing import Any

MIN_POLYGON_POINTS = 3


def point_in_polygon(lat: float, lng: float, polygon: list[Any] | None) -> bool:
    """Ray-cast a point against a polygon; never raises — malformed/empty/
    too-small polygons simply return ``False`` so callers can fall back to
    the no-classification path instead of crashing."""
    if not polygon or len(polygon) < MIN_POLYGON_POINTS:
        return False

    points: list[tuple[float, float]] = []
    for vertex in polygon:
        try:
            v_lat, v_lng = vertex
            points.append((float(v_lat), float(v_lng)))
        except (TypeError, ValueError):
            return False

    inside = False
    n = len(points)
    j = n - 1
    for i in range(n):
        yi, xi = points[i]
        yj, xj = points[j]
        if ((yi > lat) != (yj > lat)) and (
            lng < (xj - xi) * (lat - yi) / (yj - yi) + xi
        ):
            inside = not inside
        j = i
    return inside
