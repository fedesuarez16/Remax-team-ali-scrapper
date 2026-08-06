"""InmoBusqueda's search-results HTML is plain server-rendered PHP — no WAF,
no Apify actor, no client-side hydration — so `_parse_inmobusqueda_page` reads
the raw DOM straight from an httpx response.

Both fixtures below are real pages captured live, and they exist as two
fixtures for a reason found the hard way: THE SAME MARKUP CARRIES DIFFERENT
CONTENT depending on which listing URL served it.

  propiedades-{zona}.html            (untyped listing)
    div.resultadoTipo   → "{tipo} en {operación}"
    div.resultadoLocalidad → the address, ending in the zona

  {tipo}-{operacion}-{zona}.html     (typed listing)
    div.resultadoTipo   → the ADDRESS ("7  e/ 505 y 506")
    div.resultadoLocalidad → "{tipo} en {zona}" — no operation anywhere

Parsing positionally against one shape silently mislabels every card from the
other (verified live: every Gonnet result came back `tipo_propiedad='otro'`),
so the parser identifies the blocks by content, never by position.

Shared selectors:

  div.ResultadoCaja                     one card, id="contenidoPropiedad{ID}"
    div.resultadoPrecio                 "U$S 145.000" / "$ 350.000" / "Consultar"
    div.resultadoDescripcion            teaser copy
    div.rdBox                           detail chips: "2 Dorm", "50 mts",
                                        "Garage No", "IB-{id}", update date
    img.FotoBox                         cover photo (a placeholder when photoless)
    a[href]                             ficha link, sometimes wrapped in the
                                        `ficha.verdestacado.php` tracking
                                        redirect for promoted listings

The zona guard matters as much as the parsing: an unresolved slug renders the
same markup with nationwide results, so `_parse_inmobusqueda_page` keeps only
cards whose text mentions the searched zona.
"""
from app.models.property import ScrapingFilters
from app.services.apify import _parse_inmobusqueda_page

_PAGE = """
<html><head><title>Propiedades  La Plata (casco urbano), Buenos Aires - InmoBusqueda</title></head>
<body>
<div class="letra2 cajaPremiumResultados ResultadoCaja" id="contenidoPropiedad490898">
  <div class="resultadoContenedorFotoResultados"><div style="position:relative;">
    <a href="https://www.inmobusqueda.com.ar/ficha-490898">
      <img border="0" class="FotoBox" loading="lazy"
           src="https://www.inmobusqueda.com/fotos/200x150.490898.jpg"/></a>
  </div></div>
  <div class="resultadoContenedorDatosResultados">
    <div class="resultadoTipo">
      <a href="https://www.inmobusqueda.com.ar/ficha-490898">Departamento  en Venta </a></div>
    <div class="resultadoLocalidad"><div style="font-size:.9em;">
      Departamento en venta en 56 y 20   La Plata (Casco Urbano), Pdo. de La Plata  </div></div>
    <div class="resultadoPrecio">U$S 145.000 </div>
    <div class="resultadoDescripcion">Departamento de dos ambientes con balcon al frente.</div>
    <div class="resultadoDetalleResultados  contenedordetalles ">
      <div class="rdBox">2 amb</div>
      <div class="rdBox">37 mts </div>
      <div class="rdBox">Garage Si</div>
      <div class="rdBox codigo">IB-490898</div>
      <div class="rdBox actualizada">01-05-2026</div>
    </div>
  </div>
</div>

<div class="letra2 cajaPremiumResultados ResultadoCaja" id="contenidoPropiedad512001">
  <div class="resultadoContenedorFotoResultados">
    <a href="https://www.inmobusqueda.com.ar/ficha-512001">
      <img class="FotoBox" src="https://www.inmobusqueda.com/fotos/logo/sinfotos/200x150.4941.jpg"/></a>
  </div>
  <div class="resultadoContenedorDatosResultados">
    <div class="resultadoTipo">
      <a href="https://www.inmobusqueda.com.ar/ficha-512001">Casa  en Alquiler </a></div>
    <div class="resultadoLocalidad"><div>Casa en alquiler en calle 13   La Plata (Casco Urbano), Pdo. de La Plata</div></div>
    <div class="resultadoPrecio">$ 350.000 </div>
    <div class="resultadoDescripcion">Casa de tres dormitorios con patio.</div>
    <div class="resultadoDetalleResultados  contenedordetalles ">
      <div class="rdBox">4 amb</div>
      <div class="rdBox">120 mts </div>
      <div class="rdBox">Garage No</div>
      <div class="rdBox codigo">IB-512001</div>
    </div>
  </div>
</div>

<div class="letra2 cajaPremiumResultados ResultadoCaja" id="contenidoPropiedad777777">
  <div class="resultadoContenedorDatosResultados">
    <div class="resultadoTipo">
      <a href="https://www.inmobusqueda.com.ar/ficha-777777">Lote  en Venta </a></div>
    <div class="resultadoLocalidad"><div>Lote en venta en Cordoba Capital, Cordoba</div></div>
    <div class="resultadoPrecio">Consultar</div>
    <div class="resultadoDescripcion">Lote en zona norte.</div>
    <div class="resultadoDetalleResultados  contenedordetalles ">
      <div class="rdBox codigo">IB-777777</div>
    </div>
  </div>
</div>
</body></html>
"""

# Real page from https://www.inmobusqueda.com.ar/departamento-venta-manuel-b-gonnet.html
# — the TYPED listing, where `resultadoTipo` holds the address and the
# operation appears nowhere in the card.
_TYPED_PAGE = """
<html><head><title>Departamento en Venta  Manuel B Gonnet, Partido de  La Plata, Buenos Aires</title></head>
<body>
<div class="letra2 cajaPremiumResultados ResultadoCaja" id="contenidoPropiedad426173">
  <div class="resultadoContenedorFotoResultados">
    <a href="https://www.inmobusqueda.com.ar/ficha-426173">
      <img class="FotoBox" src="https://www.inmobusqueda.com/fotos/200x150.426173.jpg"/></a>
  </div>
  <div class="resultadoContenedorDatosResultados">
    <div class="resultadoTipo">
      <a href="https://www.inmobusqueda.com.ar/ficha-426173">7  e/ 505 y 506</a></div>
    <div class="resultadoLocalidad"><div>Departamento en   Manuel B Gonnet, Pdo. de La Plata</div></div>
    <div class="resultadoPrecio">U$S 36.000 </div>
    <div class="resultadoDescripcion">Departamento en planta alta.</div>
    <div class="resultadoDetalleResultados  contenedordetalles ">
      <div class="rdBox">2 Dorm</div>
      <div class="rdBox">50 mts</div>
      <div class="rdBox">Garage No</div>
      <div class="rdBox codigo">IB-426173</div>
      <div class="rdBox actualizada">07-05-2026</div>
      <div class="rdBox"></div>
    </div>
  </div>
</div>

<div class="letra2 cajaPremiumResultados ResultadoCaja" id="contenidoPropiedad443273">
  <div class="resultadoContenedorDatosResultados">
    <div class="resultadoTipo">
      <a href="https://www.inmobusqueda.com.ar/ficha.verdestacado.php?id=443273&amp;pos=2&amp;ppv=500&amp;lp=0&amp;hash=2da7cbde1dee&amp;rd=281327252">27 al 100</a></div>
    <div class="resultadoLocalidad"><div>PH en   Manuel B Gonnet, Pdo. de La Plata</div></div>
    <div class="resultadoPrecio">U$S 37.000 </div>
    <div class="resultadoDescripcion">PH al frente.</div>
    <div class="resultadoDetalleResultados  contenedordetalles ">
      <div class="rdBox">Monoamb</div>
      <div class="rdBox">300.00 mts</div>
      <div class="rdBox">Garage No</div>
      <div class="rdBox codigo">IB-443273</div>
    </div>
  </div>
</div>
</body></html>
"""

_FILTERS = ScrapingFilters(zona='La Plata', zonas=['La Plata'])
_GONNET = ScrapingFilters(zona='Manuel B Gonnet', zonas=['Manuel B Gonnet'],
                          tipo_operacion='venta', tipos_propiedad=['departamento'])


def _parse(html: str = _PAGE, filters: ScrapingFilters = _FILTERS):
    return _parse_inmobusqueda_page(html, filters)


def _parse_typed():
    return _parse_inmobusqueda_page(_TYPED_PAGE, _GONNET)


# ── The typed listing: same selectors, swapped content ───────────────────────

def test_typed_listing_reads_the_property_type_from_the_localidad_block():
    """Regression: parsing positionally made every Gonnet card come back
    `otro`, because on this page `resultadoTipo` is the street address."""
    depto, ph = _parse_typed()
    assert depto.tipo_propiedad == 'departamento'
    assert ph.tipo_propiedad == 'ph'


def test_typed_listing_uses_the_tipo_block_as_the_address():
    depto, _ = _parse_typed()
    assert depto.direccion.startswith('7 e/ 505 y 506')
    assert 'Manuel B Gonnet' in depto.direccion


def test_typed_listing_falls_back_to_the_searched_operation():
    """The card names no operation, so the search's own filter decides."""
    assert [p.tipo_operacion for p in _parse_typed()] == ['venta', 'venta']


def test_dorm_chip_feeds_ambientes():
    """The site writes "2 Dorm", not "2 amb" — same mapping Argenprop uses."""
    depto, ph = _parse_typed()
    assert depto.ambientes == 2
    assert ph.ambientes == 1          # "Monoamb"


def test_decimal_surface_chip_is_read():
    _, ph = _parse_typed()
    assert ph.m2_total == 300.0


def test_promoted_listing_url_is_normalised_to_the_canonical_ficha():
    """`ficha.verdestacado.php` is a tracking redirect carrying a volatile
    `hash`/`rd`; storing it would defeat dedup and rot on the next crawl."""
    _, ph = _parse_typed()
    assert ph.url_origen == 'https://www.inmobusqueda.com.ar/ficha-443273'


def test_empty_detail_chips_are_ignored():
    depto, _ = _parse_typed()
    assert depto.cocheras == 0


def test_parses_every_card_matching_the_zona():
    """The Córdoba card is nationwide noise from an unresolved slug — dropped."""
    props = _parse()
    assert len(props) == 2
    assert [p.url_origen for p in props] == [
        'https://www.inmobusqueda.com.ar/ficha-490898',
        'https://www.inmobusqueda.com.ar/ficha-512001',
    ]


def test_every_property_is_tagged_with_the_portal():
    assert {p.fuente for p in _parse()} == {'inmobusqueda'}


def test_reads_price_and_currency():
    venta, alquiler = _parse()
    assert (venta.precio, venta.moneda) == (145000.0, 'USD')
    assert (alquiler.precio, alquiler.moneda) == (350000.0, 'ARS')


def test_reads_operation_and_property_type():
    venta, alquiler = _parse()
    assert (venta.tipo_operacion, venta.tipo_propiedad) == ('venta', 'departamento')
    assert (alquiler.tipo_operacion, alquiler.tipo_propiedad) == ('alquiler', 'casa')


def test_reads_the_address_from_the_localidad_block():
    venta, _ = _parse()
    assert '56 y 20' in venta.direccion
    assert 'La Plata' in venta.direccion


def test_reads_the_detail_chips():
    venta, alquiler = _parse()
    assert (venta.ambientes, venta.m2_total, venta.cocheras) == (2, 37.0, 1)
    assert (alquiler.ambientes, alquiler.m2_total, alquiler.cocheras) == (4, 120.0, 0)


def test_reads_description_and_title():
    venta, _ = _parse()
    assert venta.descripcion is not None and 'balcon' in venta.descripcion
    assert venta.titulo


def test_keeps_the_cover_photo_but_not_the_no_photo_placeholder():
    venta, alquiler = _parse()
    assert venta.imagenes == ['https://www.inmobusqueda.com/fotos/200x150.490898.jpg']
    assert alquiler.imagenes == []


def test_price_on_request_yields_no_price_instead_of_dropping_the_card():
    """"Consultar" is a real listing without a public price — keep it."""
    props = _parse(filters=ScrapingFilters(zona='Cordoba', zonas=['Cordoba']))
    assert len(props) == 1
    assert props[0].precio is None


def test_a_page_with_no_cards_yields_nothing():
    assert _parse('<html><body><p>Sin resultados</p></body></html>') == []
