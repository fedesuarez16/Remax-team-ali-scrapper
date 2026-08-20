"""MercadoLibre via its public listing HTML, not the REST API.

`api.mercadolibre.com/sites/MLA/search` now answers **403 forbidden** without
OAuth — verified live, for every query, not just narrow ones. The scraper sent
no `Authorization` header and swallowed the failure in `except Exception:
break`, so MercadoLibre reported `0 props` on EVERY search and read as "nothing
matched" rather than "this source is broken".

The public listing HTML is still open (200, ~1.7 MB, 48 cards per page,
verified live), which is the same route argenprop/inmobusqueda/mudafy already
take. Markup relevado en vivo:

    li.ui-search-layout__item
      a.poly-component__title            → título + href (…/MLA-<id>-slug)
      span.poly-component__headline      → "Departamento en venta"
      .andes-money-amount__currency-symbol / __fraction → "US$" / "55.000"
      .poly-component__location          → "C. 56 720, La Plata, Centro, …"
      li.poly-attributes_list__item      → "3 ambs." · "1 baño" · "48 m² cubiertos"

The zona guard still matters: `manuel-b-gonnet` returns a listing from SANTA FE
among the Buenos Aires ones (verified live), so results are filtered against
`_guard_phrases` exactly like every other portal.
"""
import pytest

from app.models.property import ScrapingFilters
from app.services.apify import (
    _ML_HTML_PAGE_SIZE,
    _ml_search_urls,
    _parse_mercadolibre_page,
)


def _card(
    *, titulo='Departamento En Venta, 2 Dormitorios, Centro, La Plata',
    href='https://departamento.mercadolibre.com.ar/MLA-1982945981-depto-centro',
    simbolo='US$', monto='55.000',
    location='C. 56 720, La Plata, Centro, La Plata, Buenos Aires Interior',
    attrs=('3 ambs.', '1 baño', '48 m² cubiertos'),
    headline='Departamento en venta',
    img='https://http2.mlstatic.com/D_NQ_NP_2X_841535-MLA114698112302',
) -> str:
    items = ''.join(f'<li class="poly-attributes_list__item">{a}</li>' for a in attrs)
    return f'''
    <li class="ui-search-layout__item"><div class="poly-card">
      <a class="poly-component__title" href="{href}">{titulo}</a>
      <span class="poly-component__headline">{headline}</span>
      <span class="andes-money-amount__currency-symbol">{simbolo}</span>
      <span class="andes-money-amount__fraction">{monto}</span>
      <span class="poly-component__location">{location}</span>
      <ul class="poly-attributes_list">{items}</ul>
      <img data-src="{img}"/>
    </div></li>'''


def _page(*cards: str) -> str:
    return f'<html><body><ol class="ui-search-layout">{"".join(cards)}</ol></body></html>'


def _filters(**kw) -> ScrapingFilters:
    kw.setdefault('zona', 'La Plata')
    return ScrapingFilters(**kw)


class TestParsesACard:
    @pytest.fixture
    def prop(self):
        props = _parse_mercadolibre_page(_page(_card()), _filters())
        assert len(props) == 1
        return props[0]

    def test_fuente(self, prop):
        assert prop.fuente == 'mercadolibre'

    def test_precio_y_moneda(self, prop):
        """"US$ 55.000" — the dot is a thousands separator, not a decimal."""
        assert prop.precio == 55000.0
        assert prop.moneda == 'USD'

    def test_direccion(self, prop):
        assert prop.direccion.startswith('C. 56 720')

    def test_ambientes_banos_y_m2(self, prop):
        assert prop.ambientes == 3
        assert prop.banos == 1
        assert prop.m2_cubiertos == 48.0

    def test_url_de_origen(self, prop):
        assert 'MLA-1982945981' in prop.url_origen

    def test_imagen(self, prop):
        assert prop.imagenes and prop.imagenes[0].startswith('https://http2.mlstatic.com/')

    def test_tipo_y_operacion(self, prop):
        assert prop.tipo_propiedad == 'departamento'
        assert prop.tipo_operacion == 'venta'


class TestPreciosEnPesosYSinPrecio:
    def test_pesos_se_marcan_ars(self):
        html = _page(_card(simbolo='$', monto='450.000'))
        prop = _parse_mercadolibre_page(html, _filters())[0]
        assert (prop.precio, prop.moneda) == (450000.0, 'ARS')

    def test_card_sin_precio_se_descarta(self):
        """Sin precio no hay propiedad publicable — igual que en los otros portales."""
        html = _page(_card().replace('<span class="andes-money-amount__fraction">55.000</span>', ''))
        assert _parse_mercadolibre_page(html, _filters()) == []

    def test_card_sin_direccion_se_descarta(self):
        html = _page(_card(location=''))
        assert _parse_mercadolibre_page(html, _filters()) == []


class TestOperacionYTipo:
    def test_alquiler_se_detecta_del_headline(self):
        html = _page(_card(headline='Departamento en alquiler'))
        assert _parse_mercadolibre_page(html, _filters())[0].tipo_operacion == 'alquiler'

    def test_casa_se_detecta_del_headline(self):
        html = _page(_card(headline='Casa en venta'))
        assert _parse_mercadolibre_page(html, _filters())[0].tipo_propiedad == 'casa'


class TestGuardDeZona:
    def test_otra_provincia_se_rechaza(self):
        """`manuel-b-gonnet` devuelve un aviso de Santa Fe entre los de Buenos
        Aires — verificado en vivo."""
        html = _page(_card(location='Av. Siempreviva 100, Rosario, Santa Fe'))
        assert _parse_mercadolibre_page(html, _filters(zona='Gonnet, La Plata')) == []

    def test_la_zona_pedida_se_acepta(self):
        html = _page(_card(location='C. 7 505, Manuel B Gonnet, La Plata, Buenos Aires'))
        assert len(_parse_mercadolibre_page(html, _filters(zona='Gonnet, La Plata'))) == 1

    def test_zona_vacia_no_filtra(self):
        assert len(_parse_mercadolibre_page(_page(_card()), _filters(zona=''))) == 1


class TestDeduplicaYCuenta:
    def test_varias_cards(self):
        html = _page(_card(), _card(href='https://x.mercadolibre.com.ar/MLA-2-b', monto='60.000'))
        assert len(_parse_mercadolibre_page(html, _filters())) == 2

    def test_pagina_vacia(self):
        assert _parse_mercadolibre_page('<html><body></body></html>', _filters()) == []


class TestUrlsDeBusqueda:
    def test_primera_pagina_sin_sufijo(self):
        urls = list(_ml_search_urls(_filters(tipos_propiedad=['departamento'],
                                             tipo_operacion='venta'), 1))
        assert urls == ['https://inmuebles.mercadolibre.com.ar/departamentos/venta/la-plata']

    def test_paginacion_usa_desde(self):
        """El sitio pagina con `_Desde_49`, `_Desde_97` — de a 48."""
        urls = list(_ml_search_urls(_filters(tipos_propiedad=['departamento'],
                                             tipo_operacion='venta'), 3))
        assert urls[1].endswith(f'/_Desde_{_ML_HTML_PAGE_SIZE + 1}')
        assert urls[2].endswith(f'/_Desde_{2 * _ML_HTML_PAGE_SIZE + 1}')

    def test_alquiler_y_tipo_en_el_path(self):
        urls = list(_ml_search_urls(_filters(tipos_propiedad=['casa'],
                                             tipo_operacion='alquiler'), 1))
        assert urls[0].endswith('/casas/alquiler/la-plata')

    def test_varios_tipos_caen_a_inmuebles(self):
        urls = list(_ml_search_urls(_filters(tipos_propiedad=['casa', 'ph'],
                                             tipo_operacion='venta'), 1))
        assert '/inmuebles/venta/' in urls[0]

    def test_localidad_gana_sobre_zona(self):
        urls = list(_ml_search_urls(
            _filters(zona='Gonnet, La Plata', localidades=['Gonnet, La Plata'],
                     tipos_propiedad=['departamento'], tipo_operacion='venta'), 1))
        assert urls[0].endswith('/gonnet-la-plata')


class TestSlugsDeZona:
    """Un slug compuesto suele ser desconocido: `gonnet-la-plata` da 404
    mientras `gonnet` a secas sirve los avisos reales de Gonnet (verificado en
    vivo). Sin ese respaldo la zona entera degradaba a la localidad y una
    búsqueda en Gonnet respondía con el centro de La Plata."""

    def test_compuesto_primero_despues_el_head(self):
        from app.services.apify import _ml_zona_slugs
        assert _ml_zona_slugs(_filters(zona='Gonnet, La Plata')) == ['gonnet-la-plata', 'gonnet']

    def test_zona_simple_no_duplica(self):
        from app.services.apify import _ml_zona_slugs
        assert _ml_zona_slugs(_filters(zona='La Plata')) == ['la-plata']

    def test_el_guard_sigue_usando_la_zona_compuesta(self):
        """La URL puede caer a `gonnet`, pero el aviso igual tiene que nombrar
        La Plata — así el homónimo de otra provincia sigue afuera."""
        html = _page(_card(location='Av. Siempreviva 100, Gonnet, Santa Fe'))
        assert _parse_mercadolibre_page(html, _filters(zona='Gonnet, La Plata')) == []
