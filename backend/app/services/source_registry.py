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
    locations: tuple[tuple[int, str, int, str], ...] = ()
    office_ids: tuple[str, ...] = ()


SEARCH_SOURCES = (
    SearchSource('inmobusqueda', 'InmoBúsqueda', 'https://www.inmobusqueda.com.ar', 'inmobusqueda'),
    SearchSource(
        'mauroperri', 'Mauro Perri Bienes Raíces',
        'https://www.mauroperribienesraices.com.ar', 'tokko',
    ),
    SearchSource('urquiza', 'Urquiza Propiedades', 'https://www.urquiza.com.ar', 'tokko'),
    SearchSource('kwsuma', 'KW Suma', 'https://www.kwsuma.com.ar', 'tokko'),
    SearchSource(
        'dacalbr', 'Dacal Bienes Raíces', 'https://www.dacalbienesraices.com.ar',
        'dacal_api',
    ),
    SearchSource(
        'keymex', 'Keymex La Plata', 'https://www.keymexlaplata.com.ar', 'tokko',
        locations=(
            (26509, 'Abasto', 26499, 'La Plata'),
            (505139, 'Altos de San Lorenzo', 26499, 'La Plata'),
            (26510, 'Arana', 26499, 'La Plata'),
            (26511, 'Arturo Segui', 26499, 'La Plata'),
            (26514, 'City Bell', 26499, 'La Plata'),
            (26503, 'Grand Bell', 26514, 'City Bell'),
            (26500, 'Countries/B.Cerrado (La Plata)', 26499, 'La Plata'),
            (254687, 'Campos de la Enriqueta', 26500, 'Countries/B.Cerrado (La Plata)'),
            (51439, 'Club Miralagos', 26500, 'Countries/B.Cerrado (La Plata)'),
            (102379, 'El Quimilar', 26500, 'Countries/B.Cerrado (La Plata)'),
            (102537, 'Haras del Sur II', 26500, 'Countries/B.Cerrado (La Plata)'),
            (102538, 'Haras del Sur III', 26500, 'Countries/B.Cerrado (La Plata)'),
            (102376, 'Lomas del City Bell', 26500, 'Countries/B.Cerrado (La Plata)'),
            (102377, 'San Facundo', 26500, 'Countries/B.Cerrado (La Plata)'),
            (255807, 'El Rodeo', 26499, 'La Plata'),
            (26518, 'Joaquin Gorina', 26499, 'La Plata'),
            (52083, 'José Hernández', 26499, 'La Plata'),
            (26520, 'La Plata', 26499, 'La Plata'),
            (26522, 'Barrio Norte', 26520, 'La Plata'),
            (26524, 'Microcentro', 26520, 'La Plata'),
            (26525, 'Plaza Italia', 26520, 'La Plata'),
            (26528, 'Ringuelet', 26520, 'La Plata'),
            (26529, 'Zona Sur', 26520, 'La Plata'),
            (26530, 'Lisandro Olmos Etcheverry', 26499, 'La Plata'),
            (26523, 'Los Hornos', 26499, 'La Plata'),
            (26532, 'Manuel B Gonnet', 26499, 'La Plata'),
            (26533, 'Melchor Romero', 26499, 'La Plata'),
            (26534, 'Nueva Hermosura', 26499, 'La Plata'),
            (26537, 'Poblet', 26499, 'La Plata'),
            (505140, 'San Carlos', 26499, 'La Plata'),
            (26538, 'Tolosa', 26499, 'La Plata'),
            (26539, 'Villa Elisa', 26499, 'La Plata'),
            (51842, 'Villa Elvira', 26499, 'La Plata'),
            (51846, 'Villa Montoro', 51842, 'Villa Elvira'),
            (52138, 'Villa Parque Sicardi', 26499, 'La Plata'),
        ),
    ),
    SearchSource(
        'albertodacal', 'Alberto Dacal Propiedades', 'https://dacal.com.ar', 'brokian',
    ),
    SearchSource(
        'remaxroble', 'RE/MAX Roble', 'https://www.remax.com.ar/roble', 'remax_office',
        office_ids=(
            '5daebc89-afcf-4a70-b869-daa3c8afd031',
            'c99679a8-79bb-454f-8bcc-637240ae9ad5',
        ),
    ),
    SearchSource(
        'axion', 'Axion Group', 'https://www.axionpropiedades.com', 'houzez',
    ),
    SearchSource(
        'sabella', 'Sabella Propiedades', 'https://www.sabellapropiedades.com.ar',
        'sabella',
    ),
)

# InmoBúsqueda usa el mismo registro técnico para resolver su catálogo, pero
# conceptualmente es un portal. Este catálogo es el único que puede aparecer
# bajo el selector "Inmobiliarias".
AGENCY_SEARCH_SOURCES = tuple(
    source for source in SEARCH_SOURCES if source.id != 'inmobusqueda'
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
        source_url = urlsplit(source.base_url)
        if host == (source_url.hostname or '').removeprefix('www.'):
            source_path = source_url.path.rstrip('/')
            requested_path = parsed.path.rstrip('/')
            if source_path and not (
                requested_path == source_path or requested_path.startswith(f'{source_path}/')
            ):
                continue
            # An office or a detail URL on a portal must never become a search
            # across the entire portal. Only its catalogue root is equivalent.
            if source.adapter == 'inmobusqueda' and (parsed.path.strip('/') or parsed.query):
                return None
            return source
    return None
