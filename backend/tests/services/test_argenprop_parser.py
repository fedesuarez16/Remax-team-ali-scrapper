"""Argenprop's search-results HTML is server-rendered (verified against the
real site) but sits behind AWS WAF Bot Control, so it's crawled via Apify's
generic `website-content-crawler` actor with `saveHtml: true` instead of a
direct request.

IMPORTANT: `saveHtml: true` does NOT return raw DOM — the actor runs the page
through Readability (reader-mode extraction) first, which strips every
`class` attribute site-wide (verified with a real actor run against
https://www.argenprop.com/departamentos/venta/palermo). What survives per
card — confirmed against that real run — is the bare (non-`data-`) attributes
on the card's `<a idaviso=... montonormalizado=... dormitorios=...>` link,
a couple of `data-*` attributes, and stable sibling ordering of the
`<p>`/`<ul>`/`<h2>` content underneath it. `_parse_argenprop_page` is built
against THAT shape, not the raw pre-Readability DOM — this fixture mirrors it
exactly (trimmed from the real captured output).
"""
from app.services.apify import _parse_argenprop_page
from app.models.property import ScrapingFilters

_READABILITY_PAGE = """
<div id="readability-content"><div id="readability-page-1">
<div>
  <div id="18799423">
    <a href="https://www.argenprop.com/departamento-en-venta-en-palermo-chico-5-ambientes--18799423"
       target="_blank" idaviso="18799423" idtipopropiedad="1" idtipooperacion="1"
       dormitorios="3" ambientes="" idmoneda="2" montonormalizado="2400000" montooperacion="2400000">
      <div data-photos="">
        <p><span data-current-photo="">1</span>/46</p>
        <p><span data-visited="18799423">Visto</span></p>
        <p>61.700</p>
      </div>
      <div>
        <div>
          <div>
            <p><span>USD</span> 2.400.000
              <span title="$2.200.000 expensas">+ $2.200.000 expensas</span>
            </p>
            <p data-card-direccion="">Jerónimo Salguero 2700</p>
            <p>Departamento en Venta en Palermo Chico, Palermo</p>
          </div>
          <p><img alt="LOPEZ CASTROMIL PROPIEDADES" src="https://www.argenprop.com/static-content/agente.jpg"></p>
        </div>
        <ul>
          <li><span>300 m² cubie.</span></li>
          <li><span>3 dorm.</span></li>
          <li><span>17 años</span></li>
        </ul>
        <h2>TORRE BELLINI! Piso alto de revista!</h2>
        <p>Impecable piso muy alto! Full amenities!</p>
      </div>
    </a>
  </div>
  <div id="18831782">
    <a href="https://www.argenprop.com/casa-en-venta-en-palermo-hollywood-3-ambientes--18831782"
       target="_blank" idaviso="18831782" idtipopropiedad="9" idtipooperacion="1"
       dormitorios="2" ambientes="" idmoneda="2" montonormalizado="150000000" montooperacion="150000000">
      <div data-photos="">
        <p><span data-current-photo="">1</span>/12</p>
      </div>
      <div>
        <div>
          <div>
            <p><span>$</span> 150.000.000</p>
            <p data-card-direccion="">Bonpland 2200</p>
            <p>Casa en Venta en Palermo Hollywood, Palermo</p>
          </div>
          <p><img alt="Otra Inmobiliaria" src="https://www.argenprop.com/static-content/otro.jpg"></p>
        </div>
        <ul>
          <li><span>120 m² cubie.</span></li>
          <li><span>2 dorm.</span></li>
        </ul>
        <h2>Casa reciclada a nuevo</h2>
        <p>Excelente ubicación, luminosa.</p>
      </div>
    </a>
  </div>
</div>
</div></div>
"""


def _filters() -> ScrapingFilters:
    return ScrapingFilters(zona='Palermo', tipo_operacion='venta')


def test_parses_price_from_montonormalizado_and_address() -> None:
    props = _parse_argenprop_page(_READABILITY_PAGE, _filters())
    assert len(props) == 2
    first = props[0]
    assert first.precio == 2_400_000
    assert first.moneda == 'USD'
    assert first.direccion == 'Jerónimo Salguero 2700'


def test_parses_ars_price_without_expenses() -> None:
    props = _parse_argenprop_page(_READABILITY_PAGE, _filters())
    second = props[1]
    assert second.precio == 150_000_000
    assert second.moneda == 'ARS'


def test_parses_surface_and_rooms_from_features() -> None:
    props = _parse_argenprop_page(_READABILITY_PAGE, _filters())
    first = props[0]
    assert first.m2_cubiertos == 300
    assert first.ambientes == 3
    assert first.antiguedad == 17


def test_falls_back_to_dormitorios_attribute_when_no_feature_list() -> None:
    # Card 2 has no "N dorm." feature line — must fall back to the `dormitorios`
    # attribute on the card link itself.
    props = _parse_argenprop_page(_READABILITY_PAGE, _filters())
    assert props[1].ambientes == 2
    assert props[1].m2_cubiertos == 120


# Verified live: an unknown zona slug (e.g. /departamentos/venta/gonnet)
# 301-redirects to the bare nationwide listing (/departamentos/venta), which
# even includes Uruguay (Punta del Este). Same failure mode ZonaProp already
# guards against with `_item_matches_zona` — the parser must apply the same
# zona guard so a bad slug yields 0 results instead of country-wide garbage.
_OFF_ZONE_PAGE = """
<div>
  <div id="19896539">
    <a href="https://www.argenprop.com/departamento-en-venta-en-punta-del-este-4-ambientes--19896539"
       idaviso="19896539" dormitorios="3" montonormalizado="480000">
      <div>
        <div>
          <div>
            <p><span>USD</span> 480.000</p>
            <p data-card-direccion="">Rambla Lorenzo Batlle 2200</p>
            <p>Departamento en Venta en Punta del Este</p>
          </div>
        </div>
        <h2>Frente al mar en Playa Brava</h2>
        <p>Vista plena al mar.</p>
      </div>
    </a>
  </div>
  <div id="20000001">
    <a href="https://www.argenprop.com/casa-en-venta-en-manuel-b-gonnet-4-ambientes--20000001"
       idaviso="20000001" dormitorios="3" montonormalizado="320000">
      <div>
        <div>
          <div>
            <p><span>USD</span> 320.000</p>
            <p data-card-direccion="">Calle 502 1600, Gonnet</p>
            <p>Casa en Venta en Manuel B. Gonnet, La Plata</p>
          </div>
        </div>
        <h2>Casa con parque en Gonnet</h2>
        <p>Sobre lote de 900m2.</p>
      </div>
    </a>
  </div>
</div>
"""


def test_zona_guard_rejects_off_zone_cards_after_nationwide_redirect() -> None:
    filters = ScrapingFilters(zona='Gonnet', tipo_operacion='venta')
    props = _parse_argenprop_page(_OFF_ZONE_PAGE, filters)
    assert len(props) == 1
    assert 'Gonnet' in props[0].direccion


def test_zona_guard_uses_localidades_phrase_set_on_map_path() -> None:
    # Map path: phrases = barrios ∪ localidades ∪ zona (ADR-1) — the localidad
    # alone must be enough to keep the card.
    filters = ScrapingFilters(
        zona='Manuel B. Gonnet', zonas=['Gonnet'],
        localidades=['Manuel B. Gonnet'], tipo_operacion='venta',
    )
    props = _parse_argenprop_page(_OFF_ZONE_PAGE, filters)
    assert len(props) == 1
    assert props[0].url_origen.endswith('--20000001')


def test_zona_guard_empty_zona_keeps_everything() -> None:
    filters = ScrapingFilters(tipo_operacion='venta')
    props = _parse_argenprop_page(_OFF_ZONE_PAGE, filters)
    assert len(props) == 2


def test_infers_tipo_propiedad_from_title_text_not_numeric_id() -> None:
    props = _parse_argenprop_page(_READABILITY_PAGE, _filters())
    assert props[0].tipo_propiedad == 'departamento'
    assert props[1].tipo_propiedad == 'casa'


def test_tipo_operacion_comes_from_filters_not_page() -> None:
    props = _parse_argenprop_page(_READABILITY_PAGE, _filters())
    assert all(p.tipo_operacion == 'venta' for p in props)


def test_url_origen_and_fuente() -> None:
    props = _parse_argenprop_page(_READABILITY_PAGE, _filters())
    assert props[0].url_origen == (
        'https://www.argenprop.com'
        '/departamento-en-venta-en-palermo-chico-5-ambientes--18799423'
    )
    assert props[0].fuente == 'argenprop'


def test_titulo_and_descripcion() -> None:
    props = _parse_argenprop_page(_READABILITY_PAGE, _filters())
    assert 'TORRE BELLINI' in props[0].titulo
    assert 'Full amenities' in (props[0].descripcion or '')


def test_no_photo_gallery_extracted_ficha_harvester_fills_it_in_later() -> None:
    # Readability strips the entire photo carousel — only the agency logo
    # <img> survives, which must NOT leak into `imagenes`.
    props = _parse_argenprop_page(_READABILITY_PAGE, _filters())
    assert props[0].imagenes == []


def test_cards_without_montonormalizado_are_skipped() -> None:
    html = '<a href="https://x.com/y" idaviso="1"></a>'
    assert _parse_argenprop_page(html, _filters()) == []


def test_empty_html_returns_no_properties() -> None:
    assert _parse_argenprop_page('', _filters()) == []
    assert _parse_argenprop_page('<html></html>', _filters()) == []
