"""CENTURY 21 es un PORTAL, no una inmobiliaria.

C21 es una franquicia: cada oficina ("CENTURY 21 Alianza Urbana S.A.(Gonnet)")
es un negocio con dirección propia, así que Google Maps la devuelve como una
inmobiliaria más para "inmobiliarias en {zona}". El track de inmobiliarias la
tomaba, le scrapeaba el sitio con el crawler genérico, y terminaba leyendo —
oficina por oficina, sin filtro de zona server-side — el MISMO inventario que
century21.com.ar publica entero detrás de una sola API pública.

Es el caso RE/MAX otra vez: marca-franquicia con portal nacional propio. Y la
consecuencia de tratarla como inmobiliaria no es sólo el costo del crawler,
es el conteo: la misma propiedad entraba N veces (una por oficina) y las
oficinas inflaban el número de "inmobiliarias en la zona".

Este archivo fija las dos mitades de la decisión:

- **Alta como portal.** `century21` entra al catálogo fijo (PORTAL_SOURCES ·
  PORTAL_CATALOG · `Fuente`), igual que cualquier otro portal.
- **Baja como inmobiliaria.** El track de Google Maps descarta las oficinas
  C21 ANTES de persistirlas, que es el único punto donde descartar sirve:
  todo lo que sobrevive a `_norm_googlemaps_agency` se guarda bajo `zona_norm`
  y vuelve del caché en cada búsqueda posterior durante los 30 días del TTL.

La baja mira el NOMBRE y el SITIO, al revés que `agency_matches_zona` — que
mira sólo la dirección y por buenas razones. La diferencia es qué se está
preguntando: la zona es un hecho geográfico que el nombre miente todo el
tiempo ("Inmobiliaria La Plata S.A." en Berisso); la marca es un hecho del
nombre y del dominio, y no hay otro lugar donde leerla.
"""
import pytest

from app.api.v1.portals import PORTAL_CATALOG
from app.models.property import RawProperty
from app.services.apify import (
    PORTAL_SOURCES,
    _norm_googlemaps_agency,
    agency_is_portal_brand,
)


# ── Alta como portal ──────────────────────────────────────────────────────────

def test_century21_es_una_fuente_de_portal() -> None:
    assert 'century21' in PORTAL_SOURCES


def test_century21_esta_en_el_catalogo_fijo() -> None:
    """PORTAL_CATALOG alimenta la tarjeta "Portales Inmobiliarios" y debe
    quedar en sync con PORTAL_SOURCES — la nota está en el módulo."""
    assert 'century21' in {p['id'] for p in PORTAL_CATALOG}


def test_century21_es_una_fuente_valida_de_property() -> None:
    """Sin esto el scraper corre y después pydantic rechaza cada fila."""
    prop = RawProperty(fuente='century21', direccion='Calle 45 123, La Plata')
    assert prop.fuente == 'century21'


# ── Baja como inmobiliaria: la guarda, sola ───────────────────────────────────

@pytest.mark.parametrize('nombre', [
    'CENTURY 21 Alianza Urbana S.A.(Gonnet)',
    'Century 21 Nexus',
    'CENTURY21 Delta',
    'C21 Vanguardia Inmobiliaria',
])
def test_una_oficina_c21_se_reconoce_por_el_nombre(nombre: str) -> None:
    assert agency_is_portal_brand(nombre, None)


def test_una_oficina_c21_se_reconoce_por_el_sitio() -> None:
    """Franquiciados que se anuncian con nombre de fantasía: el dominio los
    delata igual, y es el dato con el que el crawler iba a entrar."""
    assert agency_is_portal_brand('Alianza Urbana Propiedades',
                                  'https://century21.com.ar/oficina/194')


def test_una_inmobiliaria_comun_no_es_marca_de_portal() -> None:
    assert not agency_is_portal_brand('Inmobiliaria Del Sur', 'https://delsur.com.ar')


def test_no_alcanza_con_que_el_nombre_diga_21() -> None:
    """La guarda no puede ser "contiene 21": los nombres de inmobiliarias
    llevan números todo el tiempo (calles, años, direcciones)."""
    assert not agency_is_portal_brand('Inmobiliaria Calle 21', 'https://calle21.com.ar')
    assert not agency_is_portal_brand('Grupo 21 Propiedades', None)


# ── Baja como inmobiliaria: en el punto donde se persiste ─────────────────────

def _item(**over: object) -> dict:
    base = {
        'title': 'CENTURY 21 Alianza Urbana',
        'address': 'Calle 50 456, La Plata, Buenos Aires',
        'website': 'https://century21.com.ar/oficina/194',
    }
    return {**base, **over}


def test_norm_googlemaps_descarta_la_oficina_c21() -> None:
    assert _norm_googlemaps_agency(_item(), 'La Plata') is None


def test_norm_googlemaps_descarta_la_oficina_c21_aunque_este_en_la_zona() -> None:
    """La dirección es correcta y `agency_matches_zona` la aprobaría: lo que
    la deja afuera es la marca, no la geografía."""
    from app.services.apify import agency_matches_zona
    item = _item()
    assert agency_matches_zona(item['address'], 'La Plata')
    assert _norm_googlemaps_agency(item, 'La Plata') is None


def test_norm_googlemaps_sigue_aceptando_una_inmobiliaria_comun() -> None:
    agency = _norm_googlemaps_agency(
        _item(title='Inmobiliaria Del Sur', website='https://delsur.com.ar'),
        'La Plata',
    )
    assert agency is not None
    assert agency.nombre == 'Inmobiliaria Del Sur'
