"""El casco de La Plata sí se puede acotar: es una grilla numerada.

Una dirección postal prueba la LOCALIDAD, no el barrio, así que pedir "el
casco" devolvía las 550 inmobiliarias del partido de La Plata. Salvo que la
localidad sea La Plata, donde el barrio SÍ está codificado en la dirección: el
casco fundacional es un cuadrado de 38×38 manzanas delimitado por las Avenidas
32, 122, 72 y 31 (es.wikipedia.org/wiki/La_Plata), y toda dirección de adentro
nombra dos calles de ese rango.

Los dos ejes de la grilla:

  - 32 a 72   — las que corren en un sentido
  - 1 a 31, y 115 a 122 — las del otro (después de la 1 la numeración salta a
    la 115, y la Av. 122 cierra el cuadrado)
  - las diagonales 73, 74, 77, 78, 79 y 80 son internas

Y la altura codifica la calle transversal: `C. 49 857` es la 49 entre 8 y 9,
así que `857 // 100 = 8` da la otra coordenada. Con las dos adentro del rango,
la dirección está en el casco.

Esto es específico de La Plata a propósito. No hay una regla general: es la
ciudad la que tiene una grilla numerada y un perímetro documentado.
"""
import pytest

from app.services.apify import agency_matches_zona, es_casco_la_plata

# Direcciones REALES de la base, de agencias que están en el casco.
_ADENTRO = [
    'La Plata Buenos Aires AR, C. 49 857, B1900 AQI, Argentina',
    '11 N° 809 entre, Diag. 74 y 48, B1900 La Plata, Provincia de Buenos Aires',
    'ATS, C. 16 902, B1900 La Plata, Provincia de Buenos Aires, Argentina',
    'Av. 19 770, B1900 La Plata, Provincia de Buenos Aires, Argentina',
    'C. 8 1285 esq. 59, B1900 La Plata, Provincia de Buenos Aires',
    '51 Nro 835 entre 11 y 12, B1900 La Plata, Provincia de Buenos Aires',
    'Calle 39 y 7 nº 602, B1900 La Plata, Provincia de Buenos Aires',
    'Av. 44 949 Piso 18, B1900DVB La Plata, Provincia de Buenos Aires',
    'C. 10 602, La Plata, Provincia de Buenos Aires, Argentina',
]

# Reales, de agencias del partido pero FUERA del casco.
_AFUERA = [
    'C. 14 709, B1896 City Bell, Buenos Aires',
    '467 esquina 19 n 1295, B1896 City Bell, Buenos Aires',
    'Cno. Gral. Belgrano y 493 Loc 2, B1897 Gonnet, Provincia de Buenos Aires',
    'C. 472 1009, B1896 City Bell, Provincia de Buenos Aires',
]


@pytest.mark.parametrize('direccion', _ADENTRO)
def test_una_direccion_del_casco_se_reconoce(direccion: str) -> None:
    assert es_casco_la_plata(direccion)


@pytest.mark.parametrize('direccion', _AFUERA)
def test_una_del_partido_pero_fuera_del_casco_no(direccion: str) -> None:
    assert not es_casco_la_plata(direccion)


def test_las_avenidas_del_perimetro_cuentan_como_casco() -> None:
    """El borde es parte del casco: la Av. 32 y la 72 lo delimitan, no lo
    excluyen."""
    assert es_casco_la_plata('Av. 32 1250, B1900 La Plata')
    assert es_casco_la_plata('Av. 72 y 25, B1900 La Plata')


def test_una_calle_de_tres_digitos_queda_afuera() -> None:
    """Las calles 400+ y 500+ son de las localidades del norte del partido."""
    assert not es_casco_la_plata('C. 467 1295, B1896 City Bell')
    assert not es_casco_la_plata('C. 520 y 21, B1900 Tolosa')


def test_sin_numeros_de_calle_no_se_afirma_que_es_casco() -> None:
    """Sin las dos coordenadas no hay evidencia. Afirmar que sí sería descartar
    lo contrario sin fundamento."""
    assert not es_casco_la_plata('Camino Centenario km 12, La Plata')
    assert not es_casco_la_plata('')
    assert not es_casco_la_plata(None)


# ── Montado en la guarda ──────────────────────────────────────────────────────

def test_pedir_el_casco_ahora_acota() -> None:
    """Lo que motivó todo: 550 inmobiliarias del partido para una búsqueda del
    casco."""
    assert agency_matches_zona('C. 16 902, B1900 La Plata', 'casco urbano, La Plata')
    assert not agency_matches_zona('C. 14 709, B1896 City Bell', 'casco urbano, La Plata')


def test_pedir_la_plata_entera_sigue_trayendo_todo_el_partido() -> None:
    """El casco es un pedido MÁS ANGOSTO que el partido. Quien pide La Plata a
    secas no pidió el casco."""
    assert agency_matches_zona('C. 14 709, B1896 City Bell', 'City Bell')
    assert agency_matches_zona('C. 16 902, B1900 La Plata', 'La Plata')


def test_el_casco_de_otra_ciudad_no_usa_esta_regla() -> None:
    """La grilla numerada es de La Plata. En otra ciudad "casco" sigue siendo
    un descriptor que no se puede exigir."""
    assert agency_matches_zona('Av. Colón 1200, Mar del Plata', 'casco urbano, Mar del Plata')
