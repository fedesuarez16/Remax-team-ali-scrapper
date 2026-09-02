"""Una zona que nadie escribe en un sobre no puede filtrar direcciones.

Medido sobre la base real (2026-09-02): pidiendo `"casco urbano, La Plata"`,
**0 de 12** inmobiliarias del casco pasaban la guarda. Sus direcciones dicen
`"C. 10 602, La Plata"` o `"Av. 44 949, B1900DVB La Plata"` — correctas, y sin
la frase "casco urbano" por ningún lado.

El motivo: `agency_matches_zona` exige TODAS las partes de una frase compuesta,
y "casco urbano" es un nombre COLOQUIAL de zona, no un componente de dirección
postal. Nadie lo escribe en un sobre. Exigirlo descarta el 100% de una zona que
sí existe.

Para localidades de verdad la regla estaba bien y no se toca — medido en la
misma corrida: City Bell 19 de 20, La Plata 3 de 3. Lo que cambia es que los
descriptores coloquiales se sacan de la frase ANTES de comparar, así
`"casco urbano, La Plata"` pasa a exigir sólo `"la plata"` — lo único que una
dirección puede probar.
"""
import pytest

from app.services.apify import agency_matches_zona

# Direcciones REALES de la base, de agencias fichadas bajo "casco urbano".
_CASCO = [
    'C. 10 602, La Plata, Provincia de Buenos Aires, Argentina',
    'Av. 44 949 Piso 18, B1900DVB La Plata, Provincia de Buenos Aires',
    '11 N° 809 entre, Diag. 74 y 48, B1900 La Plata, Provincia de Buenos Aires',
    'Calle 48 n°884 e/ 12 y 13, La Plata, Provincia de Buenos Aires',
]


@pytest.mark.parametrize('direccion', _CASCO)
def test_el_casco_ya_no_descarta_a_todos(direccion: str) -> None:
    """El caso medido: 0 de 12 pasaban."""
    assert agency_matches_zona(direccion, 'casco urbano, La Plata')


@pytest.mark.parametrize('descriptor', [
    'casco urbano', 'casco', 'centro', 'microcentro', 'zona norte', 'zona sur',
])
def test_otros_descriptores_coloquiales_tambien(descriptor: str) -> None:
    assert agency_matches_zona('C. 10 602, La Plata, Buenos Aires', f'{descriptor}, La Plata')


def test_el_partido_sigue_mandando() -> None:
    """Sacar el descriptor no puede volver la guarda inútil: una agencia de Mar
    del Plata no es del casco de La Plata."""
    assert not agency_matches_zona('Av. Colón 1200, Mar del Plata', 'casco urbano, La Plata')


# ── Lo que NO cambia ──────────────────────────────────────────────────────────

def test_una_localidad_de_verdad_sigue_siendo_exigida() -> None:
    """City Bell SÍ aparece en las direcciones (19 de 20 en la medición), así
    que sigue discriminando: una del casco no es de City Bell."""
    assert agency_matches_zona('C. 14 709, B1896 City Bell, Buenos Aires', 'City Bell, La Plata')
    assert not agency_matches_zona('C. 10 602, La Plata, Buenos Aires', 'City Bell, La Plata')


def test_una_zona_simple_no_se_toca() -> None:
    assert agency_matches_zona('C. 10 602, La Plata', 'La Plata')
    assert not agency_matches_zona('Av. Colón 1200, Mar del Plata', 'La Plata')


def test_una_zona_que_es_solo_descriptor_no_filtra_nada() -> None:
    """`"casco urbano"` sin partido no puede probar nada contra una dirección;
    dejar pasar todo es preferible a descartar todo."""
    assert agency_matches_zona('C. 10 602, La Plata', 'casco urbano')
    assert agency_matches_zona('Av. Colón 1200, Mar del Plata', 'casco urbano')


def test_sin_direccion_sigue_sin_pasar() -> None:
    assert not agency_matches_zona('', 'casco urbano, La Plata')
