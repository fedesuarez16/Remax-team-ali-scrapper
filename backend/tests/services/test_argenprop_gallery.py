"""Argenprop photo galleries, parsed from RAW (pre-Readability) card HTML.

Argenprop server-renders the whole carousel into the search-results HTML — no
JS needed. Verified with a plain request to
https://www.argenprop.com/departamentos/venta/palermo: 20 cards, each carrying
up to 8 photos as `ul.card__photos[data-carousel] > li > img`, where the FIRST
image has a real `src` (eager, `fetchpriority="high"`) and the rest are lazy
with the URL on `data-src`.

Those photos never reached us because the `website-content-crawler` run used
the actor's default Readability transform, which strips the carousel entirely.
`_scrape_argenprop` now asks for `htmlTransformer: 'none'` so the raw DOM
survives, and this fixture mirrors that raw shape (trimmed from the real
capture).

Two raw-shape traps this pins down:

1. The card's FIRST `<ul>` is the photo carousel, NOT `ul.card__main-features`
   — a positional `card.find('ul')` silently loses m²/dorms/antigüedad.
2. The agency logo `<img>` lives in `div.card__agent`, a sibling of the
   carousel, and must never leak into the gallery.

Card assets are served at `_u_small`; the ficha serves the same asset ids at
`_u_medium` (verified live: `200 image/webp`, ~3x the bytes), so the parser
upgrades the suffix.
"""
from app.models.property import ScrapingFilters
from app.services.apify import _parse_argenprop_page

_STATIC = 'https://www.argenprop.com/static-content/19357002'

_RAW_PAGE = f"""
<div id="id-card-1" class="card " data-item-card="20075391">
  <a href="/departamento-en-venta-en-palermo-hollywood-3-ambientes--20075391"
     target="_blank" idaviso="20075391" idtipopropiedad="1" idtipooperacion="1"
     dormitorios="2" ambientes="" idmoneda="2" montonormalizado="340000">
    <div class="card__photos-box" data-photos>
      <div class="card__carousel simple-carousel">
        <span data-prev><i class="basico1-icon-angle_left_bold"></i></span>
        <span data-next><i class="basico1-icon-angle_right_bold"></i></span>
        <ul class="card__photos" data-carousel>
          <li data-lazy-loader>
            <img fetchpriority="high" alt="Duplex 90m2"
                 onerror="this.onerror=null;this.src='/content/images/photo_placeholder.svg'"
                 src="{_STATIC}/02a17aff-11be-409e-9cb8-8d530838c005_u_small.jpg" />
          </li>
          <li data-lazy-loader>
            <img alt="Ar&#xE9;valo 1900, Piso 7"
                 data-src="{_STATIC}/78f70a8d-d894-4a55-ae22-e6601857b3f8_u_small.jpg"
                 decoding="async" data-lazy />
          </li>
          <li data-lazy-loader>
            <img alt="Departamento en Venta de 3 ambientes"
                 data-src="{_STATIC}/f1616b55-f379-4e1b-9c03-5432ba70a139_u_small.jpg"
                 decoding="async" data-lazy />
          </li>
        </ul>
      </div>
    </div>
    <div class="card__details-box">
      <div class="card__details-box-top">
        <div class="card__monetary-values">
          <p class="card__price">
            <span class="card__currency">USD</span> 340.000
            <span class="card__expenses" title="$320.000 expensas">+ $320.000 expensas</span>
          </p>
          <p class="card__address" data-card-direccion>Ar&#xE9;valo 1900, Piso 7</p>
          <p class="card__title--primary">Departamento en Venta en Palermo Hollywood, Palermo</p>
        </div>
        <div class="card__agent">
          <img data-lazy data-src="{_STATIC}/43f2bcb6-27d8-45f3-9f5c-73b38963caa0_small.jpg"
               class="img-responsive" alt="Tripputi Propiedades" width="100" height="75" />
        </div>
      </div>
      <ul class="card__main-features">
        <li><i class="basico1-icon-superficie_cubierta"></i><span> 90  m&#xB2; cubie. </span></li>
        <li><i class="basico1-icon-cantidad_dormitorios"></i><span> 2 dorm. </span></li>
        <li><i class="basico1-icon-antiguedad"></i><span> 17 a&#xF1;os </span></li>
      </ul>
      <h2 class="card__title">Duplex 90m2 Palermo Hollywood C/ Balc&#xF3;n Terraza</h2>
      <p class="card__info">Duplex 3 ambientes 90m2 c/ cochera, 2 ba&#xF1;os.</p>
    </div>
  </a>
</div>
<div id="id-card-2" class="card " data-item-card="20075392">
  <a href="/casa-en-venta-en-palermo-hollywood-3-ambientes--20075392"
     target="_blank" idaviso="20075392" dormitorios="3" montonormalizado="150000000">
    <div class="card__photos-box card__photos-box-empty" data-photos>
      <div class="card__carousel simple-carousel">
        <ul class="card__photos" data-carousel>
          <li data-lazy-loader>
            <img alt="Sin fotos" src="/content/images/photo_placeholder.svg" />
          </li>
        </ul>
      </div>
    </div>
    <div class="card__details-box">
      <div class="card__details-box-top">
        <div class="card__monetary-values">
          <p class="card__price"><span class="card__currency">$</span> 150.000.000</p>
          <p class="card__address" data-card-direccion>Bonpland 2200</p>
          <p class="card__title--primary">Casa en Venta en Palermo Hollywood, Palermo</p>
        </div>
        <div class="card__agent">
          <img data-lazy data-src="{_STATIC}/otra-agencia_small.jpg" alt="Otra Inmobiliaria" />
        </div>
      </div>
      <ul class="card__main-features">
        <li><i class="basico1-icon-superficie_cubierta"></i><span> 120  m&#xB2; cubie. </span></li>
      </ul>
      <h2 class="card__title">Casa reciclada a nuevo</h2>
      <p class="card__info">Excelente ubicaci&#xF3;n, luminosa.</p>
    </div>
  </a>
</div>
"""


def _filters() -> ScrapingFilters:
    return ScrapingFilters(zona='Palermo', tipo_operacion='venta')


def test_extracts_carousel_photos_from_both_src_and_data_src() -> None:
    props = _parse_argenprop_page(_RAW_PAGE, _filters())
    assert len(props) == 2
    assert props[0].imagenes == [
        f'{_STATIC}/02a17aff-11be-409e-9cb8-8d530838c005_u_medium.jpg',
        f'{_STATIC}/78f70a8d-d894-4a55-ae22-e6601857b3f8_u_medium.jpg',
        f'{_STATIC}/f1616b55-f379-4e1b-9c03-5432ba70a139_u_medium.jpg',
    ]


def test_upgrades_card_thumbnail_suffix_to_ficha_resolution() -> None:
    props = _parse_argenprop_page(_RAW_PAGE, _filters())
    assert all('_u_small' not in url for url in props[0].imagenes)
    assert all('_u_medium.jpg' in url for url in props[0].imagenes)


def test_agency_logo_never_leaks_into_the_gallery() -> None:
    props = _parse_argenprop_page(_RAW_PAGE, _filters())
    assert all('43f2bcb6' not in url for url in props[0].imagenes)
    assert all('otra-agencia' not in url for url in props[1].imagenes)


def test_placeholder_only_card_yields_empty_gallery() -> None:
    props = _parse_argenprop_page(_RAW_PAGE, _filters())
    assert props[1].imagenes == []


def test_raw_shape_still_parses_features_despite_carousel_being_first_ul() -> None:
    # Regression guard: in raw HTML the card's first <ul> is the photo
    # carousel. A positional lookup finds no "m²"/"dorm." <span> in it and
    # silently drops surface + antiguedad.
    props = _parse_argenprop_page(_RAW_PAGE, _filters())
    assert props[0].m2_cubiertos == 90
    assert props[0].ambientes == 2
    assert props[0].antiguedad == 17
    assert props[1].m2_cubiertos == 120


def test_raw_shape_still_parses_price_currency_and_address() -> None:
    props = _parse_argenprop_page(_RAW_PAGE, _filters())
    assert props[0].precio == 340_000
    assert props[0].moneda == 'USD'
    assert props[0].direccion == 'Arévalo 1900, Piso 7'
    assert props[1].moneda == 'ARS'


def test_raw_shape_still_parses_title_description_and_tipo() -> None:
    props = _parse_argenprop_page(_RAW_PAGE, _filters())
    assert 'Duplex 90m2' in props[0].titulo
    assert 'cochera' in (props[0].descripcion or '')
    assert props[0].tipo_propiedad == 'departamento'
    assert props[1].tipo_propiedad == 'casa'


def test_gallery_is_deduplicated_and_capped_at_twenty() -> None:
    photo = f'<li><img data-src="{_STATIC}/dup_u_small.jpg" /></li>'
    many = ''.join(
        f'<li><img data-src="{_STATIC}/p{i}_u_small.jpg" /></li>' for i in range(30)
    )
    html = f"""
    <a href="/x--1" idaviso="1" montonormalizado="1000" dormitorios="1">
      <ul class="card__photos" data-carousel>{photo}{photo}{many}</ul>
      <div>
        <p><span>USD</span> 1.000</p>
        <p data-card-direccion>Palermo 100</p>
        <p>Departamento en Venta en Palermo</p>
      </div>
      <ul class="card__main-features"><li><span>50 m² cubie.</span></li></ul>
      <h2>T</h2>
    </a>
    """
    props = _parse_argenprop_page(html, _filters())
    assert len(props) == 1
    assert props[0].imagenes.count(f'{_STATIC}/dup_u_medium.jpg') == 1
    assert len(props[0].imagenes) == 20
