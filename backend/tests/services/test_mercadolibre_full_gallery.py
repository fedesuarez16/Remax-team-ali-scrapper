"""La galería COMPLETA de MercadoLibre sale del HTML mobile, no del desktop.

Relevado en vivo sobre 7 avisos reales de inmuebles (misma URL, sólo cambia el
User-Agent):

    UA desktop (Chrome/Mac) → 5 fotos, y el alt dice "Imagen 1 de 28"
    UA mobile  (iPhone)     → 28 fotos

El VIP nuevo de inmuebles server-rendea un mosaico de 5 y trae el resto por JS
al abrir el visor. Verificado con Playwright: ni el click en el botón
"28 fotos", ni el scroll, ni las flechas disparan un XHR con las fotos que
faltan — headless no hidrata esa parte. El markup mobile, en cambio, ya trae el
carrusel entero en el HTML servido.

Por eso el arreglo NO es más browser ni más Apify: es un header. Es el escalón
más barato de toda la escalera.

El segundo agujero es de ruteo: `_fetch_full_gallery` despacha por `fuente`, y
las fichas de Ficha Propio se guardan con `fuente='manual'`, así que NUNCA
llegaban al parser de MercadoLibre — caían en el `return []` final. Se despacha
por HOST cuando la fuente no identifica al portal.
"""
from __future__ import annotations

import httpx
import pytest

from app.services import ficha, importer

_BASE = 'https://http2.mlstatic.com'
_ML_URL = 'https://casa.mercadolibre.com.ar/MLA-1674724321-casa-en-city-bell-_JM'


def _ml_html(n: int) -> str:
    imgs = ''.join(
        f'<img src="{_BASE}/D_NQ_NP_7413{i:02d}-MLA1070842503{i:02d}_022026-F-null.webp"/>'
        for i in range(n)
    )
    return f'<html><body>{imgs}</body></html>'


@pytest.fixture
def listing(monkeypatch):
    """Sirve 5 fotos a un UA de escritorio y 28 a uno de celular, como ML."""
    state: dict = {'headers': [], 'urls': []}

    class _Resp:
        def __init__(self, text: str) -> None:
            self.status_code = 200
            self.text = text

    class _Client:
        def __init__(self, *a, **kw) -> None:
            self._headers = kw.get('headers') or {}

        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None

        async def get(self, url, *a, **kw):
            ua = self._headers.get('User-Agent', '')
            state['headers'].append(self._headers)
            state['urls'].append(url)
            return _Resp(_ml_html(28 if 'iPhone' in ua else 5))

    monkeypatch.setattr(httpx, 'AsyncClient', _Client)
    return state


# ── El header ────────────────────────────────────────────────────────────────

class TestPideElHtmlMobile:
    async def test_usa_user_agent_de_celular(self, listing) -> None:
        await ficha._mercadolibre_gallery(_ML_URL)
        assert 'iPhone' in listing['headers'][0]['User-Agent']

    async def test_trae_la_galeria_entera(self, listing) -> None:
        """Regresión: con UA de escritorio se traía 5 de 28."""
        assert len(await ficha._mercadolibre_gallery(_ML_URL)) == 28

    async def test_sigue_pidiendo_la_misma_url(self, listing) -> None:
        """El truco es el header, no otra URL: `/p/MLA...` da 404 y
        `articulo.mercadolibre.com.ar` no trae fotos (verificado en vivo)."""
        await ficha._mercadolibre_gallery(_ML_URL)
        assert listing['urls'] == [_ML_URL]

    async def test_zonaprop_no_se_hace_pasar_por_celular(self, listing, monkeypatch) -> None:
        """El UA mobile es una decisión de MercadoLibre, no una global: el
        parser de ZonaProp busca un JSON que sólo está en el HTML de escritorio."""
        # El HTML de la fixture no tiene el JSON de ZonaProp, así que la escalera
        # querría subir de escalón; se cortan los dos de arriba para que el test
        # mire lo único que le importa: con qué UA salió el primer request.
        from app.services import apify

        async def _nada(*a, **kw): return None
        monkeypatch.setattr(apify, 'render_page_html', _nada)
        monkeypatch.setattr(apify, 'fetch_page_html_via_actor', _nada)

        await ficha._zonaprop_gallery('https://www.zonaprop.com.ar/propiedades/x.html')
        assert 'iPhone' not in listing['headers'][0]['User-Agent']


# ── El ruteo ─────────────────────────────────────────────────────────────────

class TestDespachaPorHost:
    async def test_una_url_de_mercadolibre_usa_su_parser(self, monkeypatch) -> None:
        llamadas: list[str] = []

        async def fake(url: str) -> list[str]:
            llamadas.append(url)
            return [f'{_BASE}/foto-O.jpg']

        monkeypatch.setattr(ficha, '_mercadolibre_gallery', fake)
        assert await ficha.portal_gallery_from_url(_ML_URL) == [f'{_BASE}/foto-O.jpg']
        assert llamadas == [_ML_URL]

    async def test_una_url_de_zonaprop_usa_su_parser(self, monkeypatch) -> None:
        async def fake(url: str) -> list[str]:
            return ['https://img.zonapropcdn.com/a.jpg']

        monkeypatch.setattr(ficha, '_zonaprop_gallery', fake)
        url = 'https://www.zonaprop.com.ar/propiedades/casa-12345.html'
        assert await ficha.portal_gallery_from_url(url) == ['https://img.zonapropcdn.com/a.jpg']

    async def test_un_portal_sin_parser_propio_no_inventa_nada(self) -> None:
        assert await ficha.portal_gallery_from_url('https://inmobiliaria-x.com.ar/ficha/9') == []

    async def test_una_url_vacia_no_rompe(self) -> None:
        assert await ficha.portal_gallery_from_url('') == []


class TestFichaPropioLlegaAlParser:
    async def test_fuente_manual_despacha_por_host(self, monkeypatch) -> None:
        """Regresión: `fuente='manual'` caía en el `return []` y toda ficha
        propia de MercadoLibre se quedaba con las fotos del HTML de escritorio."""
        async def fake(url: str) -> list[str]:
            return [f'{_BASE}/completa-O.jpg']

        monkeypatch.setattr(ficha, '_mercadolibre_gallery', fake)
        prop = {'fuente': 'manual', 'url_origen': _ML_URL}
        assert await ficha._fetch_full_gallery(prop) == [f'{_BASE}/completa-O.jpg']

    async def test_instagram_sigue_sin_recuperar_galeria(self) -> None:
        """No es un portal con ficha por propiedad: re-harvestear trae posts ajenos."""
        prop = {'fuente': 'instagram', 'url_origen': 'https://instagram.com/p/abc'}
        assert await ficha._fetch_full_gallery(prop) == []


# ── El import ────────────────────────────────────────────────────────────────

class TestElImportGuardaLaGaleriaEntera:
    async def test_el_import_pide_la_galeria_del_portal(self, monkeypatch) -> None:
        """El import sólo consultaba a RE/MAX, así que una ficha propia de
        MercadoLibre nacía con lo que hubiera en el HTML de escritorio."""
        vistas: list[str] = []

        async def fake_portal(url: str) -> list[str]:
            vistas.append(url)
            return [f'{_BASE}/f{i}-O.jpg' for i in range(28)]

        guardado = _stub_import(monkeypatch, portal_gallery=fake_portal, html_images=['x.jpg'])
        await importer.import_property_from_url(guardado['sb'], _ML_URL)

        assert vistas == [_ML_URL]
        assert len(guardado['rows'][0]['imagenes']) == 28

    async def test_no_recorta_una_galeria_grande(self, monkeypatch) -> None:
        """El tope viejo era 20: un aviso de 28 fotos perdía 8 en el insert."""
        async def fake_portal(url: str) -> list[str]:
            return [f'{_BASE}/f{i}-O.jpg' for i in range(28)]

        guardado = _stub_import(monkeypatch, portal_gallery=fake_portal, html_images=[])
        await importer.import_property_from_url(guardado['sb'], _ML_URL)
        assert len(guardado['rows'][0]['imagenes']) == 28


def _stub_import(monkeypatch, *, portal_gallery, html_images: list[str]) -> dict:
    """Corta todo lo que no se está probando: red, LLM y Supabase."""
    rows: list[dict] = []

    async def fake_fetch_page(url: str):
        return 'Casa en City Bell con pileta y parque. ' * 20, list(html_images)

    async def fake_llm(url: str, text: str):
        return {'encontrada': True, 'titulo': 'Casa', 'precio': 120000}, None

    async def fake_record(*a, **kw): return None
    async def fake_harvest(urls): return {}

    monkeypatch.setattr(importer, '_fetch_page', fake_fetch_page)
    monkeypatch.setattr(importer, '_extract_llm', fake_llm)
    monkeypatch.setattr(importer, 'record_llm_usage', fake_record)
    monkeypatch.setattr(importer, 'harvest_page_images', fake_harvest)
    monkeypatch.setattr(importer, 'portal_gallery_from_url', portal_gallery)

    class _Res:
        def __init__(self, data): self.data = data

    class _Q:
        def select(self, *a, **kw): return self
        def eq(self, *a, **kw): return self
        def limit(self, *a, **kw): return self
        def insert(self, payload):
            rows.append(payload)
            self._payload = {**payload, 'id': 'prop-1'}
            return self
        async def execute(self):
            return _Res([self._payload] if hasattr(self, '_payload') else [])

    class _Sb:
        def table(self, name): return _Q()

    return {'sb': _Sb(), 'rows': rows}


# ── Las fichas que ya se guardaron con 5 fotos ───────────────────────────────


class TestLasFichasViejasSeCuran:
    """Una ficha importada ANTES del arreglo tiene 5 fotos y `ficha_enriched`.

    El gate de recuperación era `<= 1 imagen`, así que esas fichas nunca
    volvían a pedir la galería: quedaban clavadas en 5 de 28 para siempre y la
    única salida era borrarlas y reimportarlas. El mosaico de escritorio de
    MercadoLibre trae SIEMPRE 5, así que "ficha de ML con 5 o menos fotos" es la
    huella exacta de ese bug y vale un reintento.
    """

    def test_una_ficha_de_ml_con_el_mosaico_de_5_se_reintenta(self) -> None:
        prop = {'url_origen': _ML_URL, 'imagenes': [f'{_BASE}/f{i}.jpg' for i in range(5)]}
        assert ficha._gallery_looks_incomplete(prop) is True

    def test_una_ficha_de_ml_ya_completa_no_se_reintenta(self) -> None:
        """Si ya tiene la galería entera, reintentar sería gasto puro."""
        prop = {'url_origen': _ML_URL, 'imagenes': [f'{_BASE}/f{i}.jpg' for i in range(28)]}
        assert ficha._gallery_looks_incomplete(prop) is False

    def test_otro_portal_con_5_fotos_esta_sano(self) -> None:
        """La huella es de MercadoLibre. 5 fotos en ZonaProp es una galería real."""
        prop = {
            'url_origen': 'https://www.zonaprop.com.ar/propiedades/casa-1.html',
            'imagenes': [f'z{i}.jpg' for i in range(5)],
        }
        assert ficha._gallery_looks_incomplete(prop) is False

    def test_la_regla_vieja_sigue_valiendo_para_todos(self) -> None:
        assert ficha._gallery_looks_incomplete({'imagenes': []}) is True
        assert ficha._gallery_looks_incomplete({'imagenes': ['a']}) is True
        assert ficha._gallery_looks_incomplete({'imagenes': ['a', 'b']}) is False

    async def test_reintentar_y_encontrar_lo_mismo_no_escribe_en_la_base(
        self, monkeypatch
    ) -> None:
        """Un aviso de ML que de verdad tiene 5 fotos: el reintento cuesta UN
        GET gratis y no toca la base, así que no se paga dos veces."""
        async def fake(url: str) -> list[str]:
            return [f'{_BASE}/f{i}.jpg' for i in range(5)]

        monkeypatch.setattr(ficha, '_mercadolibre_gallery', fake)

        updates: list[dict] = []

        class _Q:
            def update(self, payload):
                updates.append(payload)
                return self
            def eq(self, *a): return self
            async def execute(self): return None

        class _Sb:
            def table(self, name): return _Q()

        prop = {
            'id': 'p1', 'fuente': 'manual', 'url_origen': _ML_URL,
            'imagenes': [f'{_BASE}/f{i}.jpg' for i in range(5)],
        }
        await ficha._enrich_gallery(prop, _Sb())
        assert updates == []

    async def test_reintentar_y_encontrar_las_28_las_persiste(self, monkeypatch) -> None:
        async def fake(url: str) -> list[str]:
            return [f'{_BASE}/f{i}.jpg' for i in range(28)]

        monkeypatch.setattr(ficha, '_mercadolibre_gallery', fake)

        updates: list[dict] = []

        class _Q:
            def update(self, payload):
                updates.append(payload)
                return self
            def eq(self, *a): return self
            async def execute(self): return None

        class _Sb:
            def table(self, name): return _Q()

        prop = {
            'id': 'p1', 'fuente': 'manual', 'url_origen': _ML_URL,
            'imagenes': [f'{_BASE}/f{i}.jpg' for i in range(5)],
        }
        await ficha._enrich_gallery(prop, _Sb())
        assert len(prop['imagenes']) == 28
        assert len(updates) == 1 and len(updates[0]['imagenes']) == 28
