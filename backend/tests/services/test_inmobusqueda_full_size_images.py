"""InmoBúsqueda sirve MINIATURAS; la ficha propia tiene que mostrar el original.

Medido en vivo (2026-09-17) contra `fib-lote-venta-nuevo-los-hornos-...-512291`
y contra el listado de La Plata:

    .../96030/512291/x100/512291_96030_ef9300.jpg   100x75    ~2 KB
    .../96030/512291/512291_96030_ef9300.jpg       1280x959  ~200 KB

    .../3485/412163/x200/3485.412163_86700c62.jpg   200x150
    .../3485/412163/3485.412163_86700c62.jpg        800x600

O sea: `/x<N>/` es un segmento de resize del CDN y el original vive en la misma
ruta sin él. Las dos variantes responden 200, así que subir de tamaño no es una
apuesta — es sacar un segmento de la URL.

Importa que la normalización pase por el harvest genérico y no por un parser
aparte, por dos razones que se ven abajo:

- el og:image YA apunta al original, así que sin normalizar la MISMA foto entra
  dos veces (grande por el meta, miniatura por el `<img>`);
- `_extract_images_from_html(anchor_to_og=True)` agrupa por directorio, y la
  miniatura cuelga de `.../512291/x100/` mientras el og:image cuelga de
  `.../512291/`. Al no coincidir, el anclaje se desactivaba y se colaban los
  íconos del sitio.
"""
from __future__ import annotations

import pytest

from app.services.apify import _extract_images_from_html, _full_size_image_url


_CDN = 'https://fotos55.inmobusqueda.com'


class TestFullSizeUrl:
    @pytest.mark.parametrize(('thumb', 'full'), [
        (f'{_CDN}/96030/512291/x100/512291_96030_ef9300.jpg',
         f'{_CDN}/96030/512291/512291_96030_ef9300.jpg'),
        (f'{_CDN}/3485/412163/x200/3485.412163_86700c62.jpg',
         f'{_CDN}/3485/412163/3485.412163_86700c62.jpg'),
    ])
    def test_the_resize_segment_is_dropped(self, thumb: str, full: str) -> None:
        assert _full_size_image_url(thumb) == full

    def test_an_original_is_left_alone(self) -> None:
        original = f'{_CDN}/96030/512291/512291_96030_ef9300.jpg'
        assert _full_size_image_url(original) == original

    def test_another_portal_is_not_touched(self) -> None:
        """La regla es del CDN de InmoBúsqueda, no una regla universal.

        `/x100/` en otro host puede ser un directorio real, y reescribirlo a
        ciegas daría un 404 — una ficha sin fotos es peor que una pixelada.
        """
        ajena = 'https://cdn.otroportal.com/fotos/x100/casa.jpg'
        assert _full_size_image_url(ajena) == ajena


# HTML recortado de la ficha real: el meta trae el original y la galería, las
# miniaturas de la MISMA foto más cinco más.
_FICHA_HTML = f"""
<html><head>
<meta property="og:image" content="{_CDN}/96030/512291/512291_96030_ef9300.jpg"/>
</head><body>
<img src="https://www.inmobusqueda.com.ar/imagenes/casita.home.jpg">
<img src="{_CDN}/96030/512291/x100/512291_96030_ef9300.jpg">
<img src="{_CDN}/96030/512291/x100/512291_96030_e637a7.jpg">
<img src="{_CDN}/96030/512291/x100/512291_96030_d3d768.jpg">
<img src="https://www.inmobusqueda.com.ar/imagenes/iconos/whatsapp.png">
</body></html>
"""


class TestFichaHarvest:
    def test_the_gallery_comes_back_at_full_size(self) -> None:
        imgs = _extract_images_from_html(
            _FICHA_HTML, 'https://www.inmobusqueda.com.ar/fib-lote-512291.html',
            anchor_to_og=True,
        )
        assert imgs, 'la ficha no puede quedarse sin galería'
        assert not any('/x100/' in i for i in imgs)
        assert f'{_CDN}/96030/512291/512291_96030_e637a7.jpg' in imgs

    def test_the_same_photo_is_not_stored_twice(self) -> None:
        """og:image (original) y su miniatura son LA MISMA foto.

        Sin normalizar entraban como dos entradas distintas y la ficha abría
        con la foto principal repetida.
        """
        imgs = _extract_images_from_html(
            _FICHA_HTML, 'https://www.inmobusqueda.com.ar/fib-lote-512291.html',
            anchor_to_og=True,
        )
        assert len(imgs) == len(set(imgs))
        assert imgs.count(f'{_CDN}/96030/512291/512291_96030_ef9300.jpg') == 1

    def test_normalizing_first_lets_the_og_anchor_prune_site_chrome(self) -> None:
        """Con las fotos ya en el directorio del og:image el anclaje agrupa bien.

        Antes las miniaturas colgaban de `/x100/`, no coincidían con el anclaje,
        el filtro se desactivaba y el logo y el ícono de WhatsApp terminaban
        guardados como fotos de la propiedad.
        """
        imgs = _extract_images_from_html(
            _FICHA_HTML, 'https://www.inmobusqueda.com.ar/fib-lote-512291.html',
            anchor_to_og=True,
        )
        assert all('fotos55.inmobusqueda.com' in i for i in imgs)
        assert not any('casita.home' in i or 'whatsapp' in i for i in imgs)


# El search path no pasa por `_extract_images_from_html`: arma la galería desde
# las tarjetas del listado (`img.FotoBox`), que vienen en `/x200/`. Sin esto la
# propiedad entraba al catálogo ya pixelada y la ficha heredaba la miniatura.
_LISTADO_HTML = f"""
<html><head><title>Propiedades  La Plata (casco urbano), Buenos Aires - InmoBusqueda</title></head>
<body>
<div class="letra2 cajaPremiumResultados ResultadoCaja" id="contenidoPropiedad412163">
  <div class="resultadoContenedorFotoResultados">
    <a href="https://www.inmobusqueda.com.ar/ficha-412163">
      <img class="FotoBox" src="{_CDN}/3485/412163/x200/3485.412163_86700c62.jpg"/></a>
    <a href="https://www.inmobusqueda.com.ar/ficha-412163">
      <img class="FotoBox" src="{_CDN}/3485/412163/x200/3485.412163_c0915ab3.jpg"/></a>
  </div>
  <div class="resultadoContenedorDatosResultados">
    <div class="resultadoTipo">
      <a href="https://www.inmobusqueda.com.ar/ficha-412163">Lote  en Venta </a></div>
    <div class="resultadoLocalidad"><div>Lote en venta en 80 y 159   La Plata (Casco Urbano), Pdo. de La Plata</div></div>
    <div class="resultadoPrecio">U$S 55.000 </div>
    <div class="resultadoDescripcion">Lote en Los Hornos.</div>
    <div class="resultadoDetalleResultados  contenedordetalles ">
      <div class="rdBox">300 mts </div>
      <div class="rdBox codigo">IB-412163</div>
    </div>
  </div>
</div>
</body></html>
"""


def test_the_search_path_also_stores_originals() -> None:
    from app.models.property import ScrapingFilters
    from app.services.apify import _parse_inmobusqueda_page

    props = _parse_inmobusqueda_page(_LISTADO_HTML, ScrapingFilters(zona='La Plata', zonas=['La Plata']))
    assert props, 'el listado tiene una tarjeta parseable'
    imagenes = props[0].imagenes
    assert imagenes, 'la tarjeta trae dos fotos'
    assert not any('/x200/' in i for i in imagenes)
    assert f'{_CDN}/3485/412163/3485.412163_86700c62.jpg' in imagenes
