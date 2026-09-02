"""No se le paga a Haiku para que diga que no había nada.

`_scrape_website_direct` trae la home de cada inmobiliaria más hasta 5
sub-páginas. Con 260 inmobiliarias eso son ~1500 páginas, y TODAS iban al LLM
a precio completo — incluidas las de "quiénes somos", contacto, tasaciones y
formularios. El propio system prompt lo admite:

    "Si no hay propiedades en la página, devolvé propiedades=[]"

Esa respuesta vacía cuesta lo mismo que una con veinte propiedades: se paga el
texto de entrada igual. Y el texto de entrada es el 77% del costo de la llamada
(~1500 tokens de página contra ~442 de prompt), así que una página inútil no es
un redondeo: es una llamada entera tirada.

El filtro es deliberadamente PERMISIVO. Un falso positivo cuesta una llamada —
exactamente lo que se paga hoy, así que no empeora nada. Un falso negativo
pierde propiedades reales, que es el fracaso que importa. Ante la duda, se
manda.

Por eso alcanza con cualquiera de estas dos señales:
  - un precio (toda ficha publicada tiene uno), o
  - dos términos inmobiliarios distintos (para las que dicen "consultar precio")
"""
import pytest

from app.graphs.extraction.nodes import page_is_worth_extracting


# ── Páginas que SÍ hay que analizar ───────────────────────────────────────────

_COLA = ' Coordiná tu visita con nuestros asesores, de lunes a viernes.'

_CON_PRECIO = [
    'Departamento 3 ambientes en La Plata, luminoso y al frente. USD 120.000.' + _COLA,
    'Casa en venta en City Bell — U$S 95.000 — 4 dormitorios, cochera.' + _COLA,
    'PH reciclado a nuevo en el casco, $ 850.000 por mes, apto crédito.' + _COLA,
    'Lote de 300 metros cuadrados, US$ 45.000, escritura inmediata.' + _COLA,
]


@pytest.mark.parametrize('text', _CON_PRECIO)
def test_una_pagina_con_precio_se_manda(text: str) -> None:
    assert page_is_worth_extracting(text)


def test_una_ficha_sin_precio_pero_con_datos_se_manda() -> None:
    """"Consultar precio" es habitual y no puede costarle al usuario la
    propiedad. Dos términos inmobiliarios alcanzan."""
    text = (
        'Hermoso departamento de 3 ambientes con cochera y balcón aterrazado. '
        'Precio: consultar. Contactanos para coordinar una visita.'
    )

    assert page_is_worth_extracting(text)


def test_un_listado_largo_se_manda() -> None:
    text = ' '.join(f'Depto {i} — 2 ambientes — USD {90 + i}.000' for i in range(30))

    assert page_is_worth_extracting(text)


# ── Páginas que NO hay que pagar ──────────────────────────────────────────────

_SIN_PROPIEDADES = [
    (
        'Quiénes somos. Somos una inmobiliaria con más de 30 años de trayectoria '
        'en la ciudad. Nuestro equipo de profesionales matriculados te acompaña '
        'en cada paso. Confianza y transparencia desde 1994.'
    ),
    (
        'Contacto. Escribinos y te respondemos a la brevedad. '
        'Teléfono: 221 456 7890. Email: info@ejemplo.com.ar. '
        'Horario de atención: lunes a viernes de 9 a 18.'
    ),
    (
        'Política de privacidad. Este sitio utiliza cookies propias y de terceros '
        'para mejorar la experiencia de navegación. Al continuar navegando '
        'aceptás nuestros términos y condiciones de uso.'
    ),
    (
        'Tasaciones. Solicitá la tasación de tu inmueble sin cargo. '
        'Completá el formulario y un asesor se comunicará con vos. '
        'Nombre, apellido, teléfono, correo electrónico.'
    ),
]


@pytest.mark.parametrize('text', _SIN_PROPIEDADES)
def test_una_pagina_institucional_no_se_paga(text: str) -> None:
    assert not page_is_worth_extracting(text)


def test_una_pagina_vacia_o_minima_no_se_paga() -> None:
    """El guard que ya existía adentro de `_extract_page_properties`, ahora
    acá: si va a devolver [] sin llamar, mejor no ocupar un lugar del semáforo."""
    for vacio in ('', '   ', 'x' * 99, None):
        assert not page_is_worth_extracting(vacio)  # type: ignore[arg-type]


# ── El sesgo, explícito ───────────────────────────────────────────────────────

def test_ante_la_duda_se_manda() -> None:
    """Una página que menciona propiedades de refilón entra igual. Cuesta una
    llamada — lo mismo que hoy — y no arriesga perder nada."""
    text = (
        'Novedades del mercado inmobiliario. El precio del metro cuadrado en '
        'departamentos usados se movió este trimestre. USD 1.800 promedio.'
    ) + ' relleno' * 20

    assert page_is_worth_extracting(text)


def test_una_sola_mencion_suelta_no_alcanza() -> None:
    """Un término solo aparece en cualquier pie de página ("departamentos,
    casas y terrenos" en un menú). Exigir dos distintos es lo que separa un
    menú de una ficha."""
    text = (
        'Nuestra empresa trabaja con departamentos en toda la región. '
        'Más de tres décadas acompañando a las familias de la ciudad en '
        'cada operación, con la seriedad que nos caracteriza desde el principio.'
    )

    assert not page_is_worth_extracting(text)
