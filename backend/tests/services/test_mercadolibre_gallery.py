"""La galería de una ficha de MercadoLibre sale del HTML del aviso, no de la API.

`api.mercadolibre.com/items/{id}` responde **403** — verificado en vivo con un
token de aplicación REAL (client_credentials, HTTP 200, scope `read`):

    {"message":"At least one policy returned UNAUTHORIZED.",
     "blocked_by":"PolicyAgent","code":"PA_UNAUTHORIZED_RESULT_FROM_POLICIES"}

No es falta de scope ni de permiso funcional: el DevCenter no ofrece ninguno de
catálogo. ML cerró el catálogo público a las apps de terceros y hasta
`/sites/MLA` da 403. `_mercadolibre_gallery` seguía pegándole a ese endpoint, así
que TODA ficha de MercadoLibre se quedaba con la única foto del feed.

La página del aviso sí abre con headers de browser (200, ~663 KB, verificado en
vivo) y trae la galería como `<img>` de mlstatic:

    https://http2.mlstatic.com/D_NQ_NP_<id>-MLA<n>_<fecha>-F-null.webp

Se normaliza al original `-O.jpg` (200 image/jpeg, verificado): es la convención
que ya usa el repo y un JPEG plano es más seguro que un WebP para el render de
la ficha.
"""
from __future__ import annotations

import httpx
import pytest

from app.services.ficha import _mercadolibre_gallery, _parse_mercadolibre_pictures

_BASE = 'https://http2.mlstatic.com'
_URL = 'https://departamento.mercadolibre.com.ar/MLA-1991007593-depto-palermo-_JM'


def _html(*names: str) -> str:
    imgs = ''.join(f'<img class="ui-pdp-image" src="{_BASE}/{n}"/>' for n in names)
    return f'<html><body><figure class="ui-pdp-gallery__figure">{imgs}</figure></body></html>'


class TestParseaLaGaleria:
    def test_saca_las_fotos_del_aviso(self):
        html = _html(
            'D_NQ_NP_633169-MLA114810511344_082026-F-null.webp',
            'D_NQ_NP_632873-MLA114810511346_082026-F-null.webp',
        )
        assert _parse_mercadolibre_pictures(html) == [
            f'{_BASE}/D_NQ_NP_633169-MLA114810511344_082026-O.jpg',
            f'{_BASE}/D_NQ_NP_632873-MLA114810511346_082026-O.jpg',
        ]

    def test_normaliza_cualquier_sufijo_al_original(self):
        """El feed guarda `-E.webp` (thumbnail) y el detalle `-F-null.webp`; las
        dos apuntan a la misma foto y el original es `-O.jpg`."""
        html = _html('D_NQ_NP_2X_633169-MLA114810511344_082026-E.webp')
        assert _parse_mercadolibre_pictures(html) == [
            f'{_BASE}/D_NQ_NP_2X_633169-MLA114810511344_082026-O.jpg',
        ]

    def test_deduplica(self):
        """La misma foto aparece en la galería y en el visor ampliado."""
        n = 'D_NQ_NP_633169-MLA114810511344_082026'
        html = _html(f'{n}-F-null.webp', f'{n}-O.jpg', f'{n}-V.webp')
        assert _parse_mercadolibre_pictures(html) == [f'{_BASE}/{n}-O.jpg']

    def test_ignora_lo_que_no_es_foto_del_aviso(self):
        """`D_NQ_871042-MLA96631608403_102025-OO.webp` (sin el `NP_`) vino en la
        misma página relevada en vivo y no es una foto de la propiedad."""
        html = _html(
            'D_NQ_871042-MLA96631608403_102025-OO.webp',
            'D_NQ_NP_633169-MLA114810511344_082026-F-null.webp',
        )
        assert _parse_mercadolibre_pictures(html) == [
            f'{_BASE}/D_NQ_NP_633169-MLA114810511344_082026-O.jpg',
        ]

    def test_pagina_sin_fotos(self):
        assert _parse_mercadolibre_pictures('<html><body></body></html>') == []


class TestBajaElAviso:
    @pytest.fixture
    def fetched(self, monkeypatch):
        state: dict = {'urls': [], 'status': 200, 'text': _html(
            'D_NQ_NP_633169-MLA114810511344_082026-F-null.webp',
        )}

        class _Resp:
            def __init__(self) -> None:
                self.status_code = state['status']
                self.text = state['text']

        class _Client:
            def __init__(self, *a, **kw) -> None: pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None

            async def get(self, url, *a, **kw):
                state['urls'].append(url)
                return _Resp()

        monkeypatch.setattr(httpx, 'AsyncClient', _Client)
        return state

    async def test_pide_la_pagina_del_aviso(self, fetched):
        await _mercadolibre_gallery(_URL)
        assert fetched['urls'] == [_URL]

    async def test_nunca_toca_la_api_muerta(self, fetched):
        """Regresión: `api.mercadolibre.com` responde 403 a todo."""
        await _mercadolibre_gallery(_URL)
        assert not any('api.mercadolibre.com' in u for u in fetched['urls'])

    async def test_devuelve_las_fotos(self, fetched):
        assert await _mercadolibre_gallery(_URL) == [
            f'{_BASE}/D_NQ_NP_633169-MLA114810511344_082026-O.jpg',
        ]

    async def test_un_aviso_caido_no_rompe_la_ficha(self, fetched):
        """Un aviso dado de baja responde 404 — la ficha conserva lo que tenía."""
        fetched['status'] = 404
        assert await _mercadolibre_gallery(_URL) == []

    async def test_sin_url_no_hace_request(self, fetched):
        assert await _mercadolibre_gallery('') == []
        assert fetched['urls'] == []
