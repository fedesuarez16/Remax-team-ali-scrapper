"""Test-first: que la galería de una ficha traiga SÓLO fotos de ESA propiedad.

El bug: `_extract_images_from_html` hacía `find_all('img')` sobre el documento
entero y después filtraba por nombre de archivo (`_IMG_JUNK`). Eso no tiene
ninguna noción de pertenencia, y una foto de "propiedades similares" es una
foto de propiedad legítima — ningún filtro por nombre la va a atrapar nunca.

Medido en vivo sobre argenprop.com/ph-en-venta-en-la-plata-...-9044111:
9 imágenes guardadas, 1 sola de la propiedad. Las otras 8 eran 4 propiedades
similares, el widget del formulario, el marcador del mapa, el sello de data
fiscal y el logo de AGEA.

Tres piezas, en este orden:

1. PODA — sacar del árbol los contenedores de contenido ajeno ANTES de
   recolectar. Genérico: sirve para cualquier portal.
2. BACKGROUND — recolectar también las fotos en `style="background: url(...)"`.
   Sin esto la ficha de Argenprop queda con UNA foto (la del og:image), porque
   su visor no usa tags `<img>`.
3. ANCLA (opt-in) — el `og:image` es, por estándar, la foto principal de ESTA
   página. Las fotos de la propiedad comparten su directorio; las ajenas no.
   Es opt-in porque en un LISTADO de inmobiliaria anclar sería destructivo.
"""
from __future__ import annotations

from app.services.apify import _extract_images_from_html

_BASE = 'https://www.argenprop.com/ph-en-venta-en-la-plata-3-ambientes--9044111'
_OWN = 'https://www.argenprop.com/static-content/1114409'
_OTHER = 'https://www.argenprop.com/static-content/2952698'


# ── 1. Poda de contenedores ajenos ────────────────────────────────────────────

def test_similar_properties_are_not_part_of_the_gallery() -> None:
    """El contenedor real que rompía la ficha: `ul.similar-properties__list`."""
    html = f'''
    <html><body>
      <div class="gallery"><img src="{_OWN}/propia.jpg"></div>
      <ul class="similar-properties__list">
        <li><a class="similar-properties__card">
          <div class="similar-properties__photo-container">
            <img src="{_OTHER}/ajena.jpg">
          </div></a></li>
      </ul>
    </body></html>'''
    imgs = _extract_images_from_html(html, _BASE)
    assert f'{_OWN}/propia.jpg' in imgs
    assert not any('ajena' in i for i in imgs)


def test_the_sidebar_form_widget_is_not_part_of_the_gallery() -> None:
    html = f'''
    <html><body>
      <div class="gallery"><img src="{_OWN}/propia.jpg"></div>
      <div class="sidebar-form-widget"><div class="form-widget">
        <div class="img-container"><img src="https://x.com/static/066581_a/widget.jpg"></div>
      </div></div>
    </body></html>'''
    imgs = _extract_images_from_html(html, _BASE)
    assert imgs == [f'{_OWN}/propia.jpg']


def test_related_containers_are_pruned_by_name_variants() -> None:
    """Cada portal le pone un nombre distinto a lo mismo."""
    for cls in (
        'related-properties', 'propiedades-relacionadas', 'recomendadas',
        'propiedades-sugeridas', 'otras-propiedades', 'similar_listings',
    ):
        html = f'''
        <html><body>
          <img src="{_OWN}/propia.jpg">
          <section class="{cls}"><img src="{_OTHER}/ajena.jpg"></section>
        </body></html>'''
        imgs = _extract_images_from_html(html, _BASE)
        assert not any('ajena' in i for i in imgs), f'no podó .{cls}'


def test_pruning_does_not_eat_the_real_gallery() -> None:
    """La poda tiene que ser quirúrgica: nada de llevarse la ficha puesta."""
    html = f'''
    <html><body><div class="property-gallery">
      <img src="{_OWN}/a.jpg"><img src="{_OWN}/b.jpg"><img src="{_OWN}/c.jpg">
    </div></body></html>'''
    imgs = _extract_images_from_html(html, _BASE)
    assert len(imgs) == 3


def test_chrome_tags_are_pruned_like_in_visible_text() -> None:
    """`_visible_text` ya podaba header/footer/nav; esta función no, y por eso se
    colaban el logo del portal, el sello de data fiscal y el QR de registro.

    Medido en vivo: `qr-registro.png` de ZonaProp vive dentro de `<footer>`.
    """
    html = f'''
    <html><body>
      <header><img src="https://x.com/portal-brand.jpg"></header>
      <nav><img src="https://x.com/nav-thumb.jpg"></nav>
      <div class="gallery"><img src="{_OWN}/propia.jpg"></div>
      <footer><a class="copyright-datafiscal"><img src="https://img.com/qr-registro.png"></a></footer>
    </body></html>'''
    imgs = _extract_images_from_html(html, _BASE)
    assert imgs == [f'{_OWN}/propia.jpg']


def test_logo_containers_are_pruned() -> None:
    """InmoBusqueda mete su isotipo en `div.logoresultados` — y es un .jpg, así
    que ningún filtro por extensión lo iba a distinguir de una foto."""
    html = f'''
    <html><body>
      <div class="headerresultados"><div class="logoresultados">
        <img src="https://www.inmobusqueda.com.ar/imagenes/casita.home.jpg">
      </div></div>
      <img src="{_OWN}/propia.jpg">
    </body></html>'''
    imgs = _extract_images_from_html(html, _BASE)
    assert imgs == [f'{_OWN}/propia.jpg']


def test_map_containers_are_pruned() -> None:
    """El mapa de la ficha no es una foto de la propiedad.

    Medido en ZonaProp: `no-location-map.png` en `div.static-map-container`,
    que NO está en el footer — por eso hace falta el patrón propio.
    """
    html = f'''
    <html><body>
      <img src="{_OWN}/propia.jpg">
      <div class="article-map article-map-property"><div class="static-map-container">
        <img src="https://img10.naventcdn.com/ficha/images/no-location-map.png">
      </div></div>
    </body></html>'''
    imgs = _extract_images_from_html(html, _BASE)
    assert imgs == [f'{_OWN}/propia.jpg']


def test_pruning_keeps_a_gallery_that_merely_says_header() -> None:
    """La poda mira TAGS chrome y clases de logo/mapa, no cualquier 'header'.

    Un `div.gallery-header` es parte de la ficha: llevárselo puesto sería
    cambiar un bug por otro peor.
    """
    html = f'''
    <html><body><div class="gallery-header property-header">
      <img src="{_OWN}/a.jpg"><img src="{_OWN}/b.jpg">
    </div></body></html>'''
    imgs = _extract_images_from_html(html, _BASE)
    assert len(imgs) == 2


# ── 2. Fotos en background inline ─────────────────────────────────────────────

def test_background_url_photos_are_collected() -> None:
    """El visor de Argenprop no usa <img>: son divs con `background: url(...)`.

    Verificado en vivo — las 5 fotos de la ficha viven así y se perdían todas.
    """
    html = f'''
    <html><body>
      <div data-open-gallery="0" style="background: center url({_OWN}/f1.jpg), center url(/content/images/photo-placeholder.png)"></div>
      <div data-open-gallery="1" style="background: center url({_OWN}/f2.jpg), center url(/content/images/photo-placeholder.png)"></div>
    </body></html>'''
    imgs = _extract_images_from_html(html, _BASE)
    assert f'{_OWN}/f1.jpg' in imgs
    assert f'{_OWN}/f2.jpg' in imgs


def test_background_placeholders_are_still_junk() -> None:
    html = f'''<html><body>
      <div style="background-image: url({_OWN}/real.jpg)"></div>
      <div style="background-image: url(/content/images/photo-placeholder.png)"></div>
    </body></html>'''
    imgs = _extract_images_from_html(html, _BASE)
    assert imgs == [f'{_OWN}/real.jpg']


# ── 3. Ancla en el og:image (opt-in) ──────────────────────────────────────────

def test_anchor_keeps_only_photos_from_the_og_image_directory() -> None:
    html = f'''
    <html><head><meta property="og:image" content="{_OWN}/portada.jpg"></head>
    <body>
      <div style="background: url({_OWN}/f1.jpg)"></div>
      <div style="background: url({_OWN}/f2.jpg)"></div>
      <div class="carousel"><img src="{_OTHER}/ajena.jpg"></div>
    </body></html>'''
    imgs = _extract_images_from_html(html, _BASE, anchor_to_og=True)
    assert all(i.startswith(_OWN) for i in imgs), imgs
    assert len(imgs) == 3


def test_anchor_is_opt_in_so_listings_keep_every_property() -> None:
    """Una web de inmobiliaria es un LISTADO: anclar ahí borra el catálogo.

    Este es el contrato que protege a `_scrape_website_direct`.
    """
    html = f'''
    <html><head><meta property="og:image" content="{_OWN}/portada.jpg"></head>
    <body><img src="{_OWN}/a.jpg"><img src="{_OTHER}/b.jpg"></body></html>'''
    imgs = _extract_images_from_html(html, _BASE)   # sin anchor
    assert len(imgs) == 3


def test_anchor_degrades_safely_when_the_cdn_does_not_group_by_directory() -> None:
    """Muchos CDNs sirven cada foto desde un hash distinto.

    Ahí el ancla no tiene evidencia de agrupación y NO debe filtrar: perder
    fotos buenas es peor que dejar pasar alguna ajena.
    """
    html = '''
    <html><head><meta property="og:image" content="https://cdn.com/aaa/portada.jpg"></head>
    <body>
      <img src="https://cdn.com/bbb/f1.jpg">
      <img src="https://cdn.com/ccc/f2.jpg">
    </body></html>'''
    imgs = _extract_images_from_html(html, _BASE, anchor_to_og=True)
    assert len(imgs) == 3, 'el ancla se comió fotos legítimas'


def test_anchor_without_og_image_is_a_no_op() -> None:
    html = f'<html><body><img src="{_OWN}/a.jpg"><img src="{_OTHER}/b.jpg"></body></html>'
    imgs = _extract_images_from_html(html, _BASE, anchor_to_og=True)
    assert len(imgs) == 2


# ── Regresión: el caso real, de punta a punta ─────────────────────────────────

def test_the_real_argenprop_ficha_comes_back_clean() -> None:
    """Reproduce la estructura medida en vivo: 9 imágenes, 1 buena → 5 buenas."""
    html = f'''
    <html><head>
      <meta property="og:image" content="{_OWN}/b4a565d1_u_medium.jpg">
    </head><body>
      <header class="property"><a class="header__logo argenprop"><img src="/content/images/logo.png"></a></header>
      <div data-open-gallery="0" style="background: center url({_OWN}/b4a565d1_u_medium.jpg), center url(/content/images/photo-placeholder.png)"></div>
      <div data-open-gallery="1" style="background: center url({_OWN}/c68549c7_u_medium.jpg), center url(/content/images/photo-placeholder.png)"></div>
      <div data-open-gallery="2" style="background: center url({_OWN}/8f88c670_u_medium.jpg), center url(/content/images/photo-placeholder.png)"></div>
      <div data-open-gallery="3" style="background: center url({_OWN}/51cfaf0a_u_medium.jpg), center url(/content/images/photo-placeholder.png)"></div>
      <div data-open-gallery="4" style="background: center url({_OWN}/5ac5f483_u_medium.jpg), center url(/content/images/photo-placeholder.png)"></div>
      <div class="leaflet-marker-pane"><img src="{_BASE}/content/images/simple-marker.png"></div>
      <div class="sidebar-form-widget"><div class="img-container"><img src="https://x.com/static-content/066581_a/w.jpg"></div></div>
      <ul class="similar-properties__list">
        <li><img src="{_OTHER}/s1.jpg"></li>
        <li><img src="https://www.argenprop.com/static-content/8362698/s2.jpg"></li>
        <li><img src="https://www.argenprop.com/static-content/3862698/s3.jpg"></li>
        <li><img src="https://www.argenprop.com/static-content/3033698/s4.jpg"></li>
      </ul>
      <footer><img src="{_BASE}/content/images/agea.png"></footer>
    </body></html>'''
    imgs = _extract_images_from_html(html, _BASE, anchor_to_og=True)
    assert len(imgs) == 5, imgs
    assert all(i.startswith(_OWN) for i in imgs), imgs
