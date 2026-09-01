"""Una inmobiliaria cuenta para una zona si ESTÁ en la zona.

`_norm_googlemaps_agency` construía la `Agency` con `zona=zona` — la zona que
se PIDIÓ — y nunca miraba `item['address']`. Google Maps ensancha el radio solo
cuando se le acaban los resultados locales, así que "inmobiliarias en La Plata"
vuelve con Berisso, Ensenada y City Bell adentro, y las tres se guardaban
estampadas como La Plata. Una búsqueda del casco devolvía 1218 inmobiliarias de
las que muchas no eran de ahí.

Los portales ya habían peleado exactamente este bug: `_item_matches_zona` y
`_locality_haystack` existen por eso, con un comentario que cuenta cómo el
casco de La Plata volvía lleno de City Bell. El track de inmobiliarias quedó
afuera de esa guarda.

Dos decisiones que este archivo fija:

- **Sólo la dirección.** El nombre del negocio NO cuenta. "Inmobiliaria La
  Plata S.A." con oficina en Berisso pasaría cualquier guarda que mire el
  título, y ese falso positivo es justo el que vuelve inútil al filtro: los
  nombres de inmobiliarias nombran zonas todo el tiempo.
- **Sin dirección se descarta.** Es un dato que Google Maps casi siempre trae;
  cuando falta no hay forma de sostener que la inmobiliaria es de la zona, y
  conservarla es volver al problema original en menor escala.
"""
import pytest

from app.services.apify import _norm_googlemaps_agency, agency_matches_zona


# ── La guarda, sola ───────────────────────────────────────────────────────────

def test_una_direccion_en_la_zona_pasa() -> None:
    assert agency_matches_zona('Calle 50 456, La Plata, Buenos Aires', 'La Plata')


def test_una_direccion_de_otra_localidad_no_pasa() -> None:
    """El caso que trajo las 1218: Google Maps ensancha el radio y devuelve
    Berisso para una búsqueda de La Plata."""
    assert not agency_matches_zona('Av. Montevideo 820, Berisso, Buenos Aires', 'La Plata')


def test_sin_direccion_se_descarta() -> None:
    for vacio in (None, '', '   '):
        assert not agency_matches_zona(vacio, 'La Plata')


def test_acentos_y_mayusculas_no_deciden_nada() -> None:
    assert agency_matches_zona('Belgrano 123, GONNET, Buenos Aires', 'Gonnet')
    assert agency_matches_zona('Ruta 2 km 40, Chascomús', 'chascomus')


def test_una_zona_vacia_no_filtra_nada() -> None:
    """Misma convención que `_item_matches_zona`: sin zona que exigir, la
    guarda no tiene nada que decir y deja pasar todo."""
    assert agency_matches_zona('Calle 50 456, La Plata', '')


# ── Zona compuesta ────────────────────────────────────────────────────────────

def test_una_zona_compuesta_exige_las_dos_partes() -> None:
    """"City Bell, La Plata" es la localidad Y el partido. Una dirección del
    casco menciona La Plata pero no City Bell: es del partido, no de la zona
    pedida."""
    assert agency_matches_zona(
        'Cantilo 1234, City Bell, La Plata, Buenos Aires', 'City Bell, La Plata',
    )
    assert not agency_matches_zona(
        'Calle 50 456, La Plata, Buenos Aires', 'City Bell, La Plata',
    )


# ── El falso positivo que importa ─────────────────────────────────────────────

def test_el_nombre_de_la_inmobiliaria_no_es_su_direccion() -> None:
    """Una guarda que mirara el título dejaría entrar a toda inmobiliaria que
    se llame como la zona, esté donde esté. Y se llaman así todo el tiempo."""
    item = {
        'title': 'Inmobiliaria La Plata S.A.',
        'address': 'Av. Montevideo 820, Berisso, Buenos Aires',
    }

    assert _norm_googlemaps_agency(item, 'La Plata') is None


# ── La guarda montada en el normalizador ──────────────────────────────────────

def test_lo_que_no_es_de_la_zona_no_llega_a_ser_una_agency() -> None:
    """Descartar en el normalizador es lo que impide que entre a la base: todo
    lo que sobreviva acá se persiste bajo `zona_norm` y vuelve del caché en
    cada búsqueda posterior, durante los 30 días del TTL."""
    fuera = {'title': 'Inmo Berisso', 'address': 'Av. Montevideo 820, Berisso'}

    assert _norm_googlemaps_agency(fuera, 'La Plata') is None


def test_lo_que_si_es_de_la_zona_se_normaliza_igual_que_antes() -> None:
    item = {
        'title': 'Inmobiliaria del Bosque',
        'address': 'Calle 50 456, La Plata, Buenos Aires',
        'phone': '+54 221 555-0100',
        'website': 'https://delbosque.com.ar',
        'totalScore': 4.6,
    }

    agency = _norm_googlemaps_agency(item, 'La Plata')

    assert agency is not None
    assert agency.nombre == 'Inmobiliaria del Bosque'
    assert agency.direccion == 'Calle 50 456, La Plata, Buenos Aires'
    assert agency.sitio_web == 'https://delbosque.com.ar'
    assert agency.calificacion == pytest.approx(4.6)
    assert agency.zona == 'La Plata'


def test_una_agency_sin_direccion_no_pasa_aunque_tenga_nombre() -> None:
    assert _norm_googlemaps_agency({'title': 'Inmobiliaria Sin Datos'}, 'La Plata') is None
