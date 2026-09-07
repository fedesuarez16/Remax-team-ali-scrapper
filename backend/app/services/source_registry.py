"""Reviewed integrations. Registering a URL alone does not define a scraper."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from app.models.property import Fuente


@dataclass(frozen=True)
class SearchSource:
    id: Fuente
    name: str
    base_url: str
    adapter: str


SEARCH_SOURCES = (
    SearchSource('inmobusqueda', 'InmoBúsqueda', 'https://www.inmobusqueda.com.ar', 'inmobusqueda'),
    SearchSource(
        'mauroperri', 'Mauro Perri Bienes Raíces',
        'https://www.mauroperribienesraices.com.ar', 'tokko',
    ),
    SearchSource('urquiza', 'Urquiza Propiedades', 'https://www.urquiza.com.ar', 'tokko'),
)


def source_by_id(source_id: str) -> SearchSource | None:
    return next((source for source in SEARCH_SOURCES if source.id == source_id), None)


def source_for_url(url: str) -> SearchSource | None:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower().removeprefix('www.')
        if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password:
            return None
        if parsed.port not in (None, 80, 443):
            return None
    except ValueError:
        return None
    for source in SEARCH_SOURCES:
        if host == (urlsplit(source.base_url).hostname or '').removeprefix('www.'):
            # An office or a detail URL on a portal must never become a search
            # across the entire portal. Only its catalogue root is equivalent.
            if source.adapter == 'inmobusqueda' and (parsed.path.strip('/') or parsed.query):
                return None
            return source
    return None
