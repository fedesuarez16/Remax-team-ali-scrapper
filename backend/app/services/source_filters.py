"""Final checks for reviewed scrapers, applied independently of website filters."""
from __future__ import annotations

from app.models.property import RawProperty, ScrapingFilters


def matches_source_filters(prop: RawProperty, filters: ScrapingFilters) -> bool:
    from app.services.apify import _keep_barrio_cerrado, agency_matches_zona

    if filters.tipo_operacion and prop.tipo_operacion != filters.tipo_operacion:
        return False
    if filters.tipos_propiedad and prop.tipo_propiedad not in filters.tipos_propiedad:
        return False
    zonas = [filters.zona_pedida] if filters.zona_pedida else (
        filters.localidades or filters.zonas or [filters.zona or '']
    )
    if any(zonas) and not any(
        agency_matches_zona(prop.direccion, zona) for zona in zonas if zona
    ):
        return False
    if filters.barrio_aliases and not _keep_barrio_cerrado([prop], filters.barrio_aliases):
        return False
    bounds = (
        (prop.precio, filters.precio_min, filters.precio_max),
        (prop.ambientes, filters.ambientes_min, filters.ambientes_max),
        (prop.raw.get('dormitorios'), filters.dormitorios_min, filters.dormitorios_max),
        (prop.m2_total, filters.m2_min, filters.m2_max),
    )
    for value, minimum, maximum in bounds:
        if minimum is None and maximum is None:
            continue
        # An unknown requested field cannot establish a match.
        if value is None:
            return False
        if minimum is not None and value < minimum:
            return False
        if maximum is not None and value > maximum:
            return False
    return True
