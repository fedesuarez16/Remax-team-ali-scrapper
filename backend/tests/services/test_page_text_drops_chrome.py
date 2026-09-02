"""El menú no puede comerse el presupuesto de texto de la página.

`parse_page` sacaba `script`, `style`, `nav`, `footer` y `header`, y ahí paraba.
Pero la mayoría de los sitios de inmobiliarias arma el menú con `<ul class="menu">`
o `<div id="navbar">` sueltos, fuera de un `<nav>` — así que el menú entero
sobrevivía al filtro y salía en el texto.

Y sale ARRIBA. `soup.get_text()` respeta el orden del documento y
`_extract_page_properties` corta con `text[:6000]` desde el principio, así que
en una página de listado grande los primeros 6000 caracteres podían ser casi
todos navegación: se paga la llamada completa y las propiedades quedaron del
otro lado del corte. No es un problema de costo, es pagar y no recibir.

Lo que este archivo fija:

- Se descartan los contenedores de chrome por CLASE e ID, no sólo por etiqueta.
- Los links de navegación pierden la URL. Un `[Contacto](https://sitio.com/contacto)`
  cuesta el doble que la palabra "Contacto" y nunca va a ser una ficha; el
  prompt pide las URLs para poder devolver `url_ficha`, y las de las fichas se
  conservan intactas.
- Las líneas repetidas consecutivas colapsan. Un menú que se renderiza dos
  veces (escritorio y mobile) aparece duplicado y pegado.

Y lo que NO se toca, que importa más: nada que pueda ser una propiedad. Un
precio, una dirección o un link a una ficha llegan enteros.
"""
import pytest

from app.services.apify import _clean_page_text


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.split('\n') if ln.strip()]


# ── Chrome por clase e id ─────────────────────────────────────────────────────

_HTML_CON_MENU = """
<html><body>
  <ul class="main-menu">
    <li><a href="/">Inicio</a></li>
    <li><a href="/contacto">Contacto</a></li>
  </ul>
  <div id="sidebar-widget">Seguinos en nuestras redes sociales</div>
  <div class="cookie-banner">Este sitio usa cookies para mejorar tu experiencia</div>
  <main>
    <h2>Departamento 3 ambientes</h2>
    <p>USD 120.000 — Calle 50 nº 456, La Plata</p>
    <a href="/ficha/depto-50-456">Ver ficha completa</a>
  </main>
</body></html>
"""


def test_el_menu_no_llega_al_texto() -> None:
    text = _clean_page_text(_HTML_CON_MENU, 'https://inmo.com/props')

    assert 'Inicio' not in text
    assert 'Contacto' not in text


def test_el_sidebar_y_el_cookie_banner_tampoco() -> None:
    text = _clean_page_text(_HTML_CON_MENU, 'https://inmo.com/props')

    assert 'redes sociales' not in text
    assert 'cookies' not in text


def test_la_propiedad_llega_entera() -> None:
    """Lo único que no se puede romper."""
    text = _clean_page_text(_HTML_CON_MENU, 'https://inmo.com/props')

    assert 'Departamento 3 ambientes' in text
    assert 'USD 120.000' in text
    assert 'Calle 50 nº 456, La Plata' in text


def test_el_link_a_la_ficha_conserva_su_url() -> None:
    """De esa URL salen las fotos: el system prompt lo dice explícito. Perderla
    es perder la galería de la propiedad."""
    text = _clean_page_text(_HTML_CON_MENU, 'https://inmo.com/props')

    assert '[Ver ficha completa](https://inmo.com/ficha/depto-50-456)' in text


# ── Links de navegación sin URL ───────────────────────────────────────────────

_HTML_LINKS = """
<html><body><main>
  <a href="/nosotros">Quiénes somos</a>
  <a href="/tasaciones">Tasaciones</a>
  <a href="/ficha/casa-city-bell">Casa en City Bell — USD 210.000</a>
</main></body></html>
"""


def test_un_link_institucional_pierde_la_url() -> None:
    """Cuesta el doble que su texto y jamás va a ser una ficha."""
    text = _clean_page_text(_HTML_LINKS, 'https://inmo.com/')

    assert '/nosotros' not in text
    assert '/tasaciones' not in text


def test_un_link_que_puede_ser_ficha_la_conserva() -> None:
    text = _clean_page_text(_HTML_LINKS, 'https://inmo.com/')

    assert '[Casa en City Bell — USD 210.000](https://inmo.com/ficha/casa-city-bell)' in text


# ── Repetidos pegados ─────────────────────────────────────────────────────────

def test_las_lineas_repetidas_consecutivas_colapsan() -> None:
    """El mismo bloque renderizado para escritorio y para mobile."""
    html = (
        '<html><body><main>'
        '<p>Inmobiliaria del Bosque</p><p>Inmobiliaria del Bosque</p>'
        '<p>Depto 2 amb — USD 90.000</p>'
        '</main></body></html>'
    )

    assert _lines(_clean_page_text(html, 'https://inmo.com/')).count('Inmobiliaria del Bosque') == 1


def test_dos_propiedades_del_mismo_precio_no_se_pisan() -> None:
    """El colapso es de líneas ADYACENTES iguales, no de todas las iguales del
    documento: dos propiedades distintas pueden costar lo mismo, y quedarnos
    con una sería perder la otra."""
    html = (
        '<html><body><main>'
        '<p>USD 120.000</p><p>Calle 50 nº 456</p>'
        '<p>USD 120.000</p><p>Calle 7 nº 890</p>'
        '</main></body></html>'
    )

    text = _clean_page_text(html, 'https://inmo.com/')

    assert _lines(text).count('USD 120.000') == 2


# ── Lo de antes sigue valiendo ────────────────────────────────────────────────

@pytest.mark.parametrize('tag', ['script', 'style', 'nav', 'footer', 'header'])
def test_las_etiquetas_que_ya_se_sacaban_se_siguen_sacando(tag: str) -> None:
    html = f'<html><body><{tag}>basura de {tag}</{tag}><main>USD 120.000</main></body></html>'

    text = _clean_page_text(html, 'https://inmo.com/')

    assert f'basura de {tag}' not in text
    assert 'USD 120.000' in text


def test_un_link_a_otro_dominio_no_se_marca_como_ficha() -> None:
    """Ya era así: sólo los del mismo dominio se vuelven markdown."""
    html = '<html><body><main><a href="https://facebook.com/inmo">Facebook</a>USD 1.000</main></body></html>'

    assert 'facebook.com/inmo' not in _clean_page_text(html, 'https://inmo.com/')
