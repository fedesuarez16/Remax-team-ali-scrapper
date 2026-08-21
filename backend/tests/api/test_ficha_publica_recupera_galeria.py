"""La ficha pública `/p/{id}` tiene que curar su propia galería.

`GET /api/v1/properties/{id}` es el ÚNICO endpoint por el que pasa la ficha
compartible: la página pública sólo lee y renderiza `imagenes`, nunca llama al
enrich. Por eso una ficha guardada con la galería parcial (importada antes de
que el import consultara al parser del portal) se veía con 5 fotos de 20 y no
había forma de que se arreglara sola — abrir el link no disparaba nada.

Curarla acá es barato y se paga UNA vez: el gate `_gallery_looks_incomplete`
sólo deja pasar las sospechosas, el primer escalón de la escalera es un GET
gratis, y una vez persistidas las 20 el gate devuelve False para siempre.

Lo que NO puede pasar es que la ficha deje de responder porque el portal está
caído: la recuperación es un extra y cualquier fallo tiene que devolver la
propiedad tal como está guardada.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1 import properties


def _prop(**kw: Any) -> dict[str, Any]:
    base = {
        'id': 'p1',
        'fuente': 'manual',
        'url_origen': 'https://www.zonaprop.com.ar/propiedades/clasificado/x-58183410.html',
        'imagenes': [f'vieja{i}.jpg' for i in range(5)],
        'ficha_enriched': True,
    }
    base.update(kw)
    return base


class _Res:
    def __init__(self, data): self.data = data


def _sb(row: dict | None):
    updates: list[dict] = []

    class _Q:
        def select(self, *a, **kw): return self
        def eq(self, *a, **kw): return self
        def limit(self, *a, **kw): return self
        def update(self, payload):
            updates.append(payload)
            return self
        async def execute(self): return _Res([row] if row else [])

    class _Sb:
        rows_updated = updates
        def table(self, name): return _Q()

    return _Sb()


def _client(sb) -> AsyncClient:
    app = FastAPI()
    app.include_router(properties.router, prefix='/properties')
    app.state.supabase = sb
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


@pytest.fixture
def galeria_completa(monkeypatch):
    """El portal contesta con las 20 fotos reales."""
    llamadas: list[dict] = []

    async def fake(prop: dict, allow_escalation: bool = True) -> list[str]:
        llamadas.append(prop)
        return [f'https://imgar.zonapropcdn.com/{i}.jpg' for i in range(20)]

    monkeypatch.setattr(properties, '_fetch_full_gallery', fake)
    return llamadas


async def test_una_ficha_con_galeria_parcial_se_completa(galeria_completa) -> None:
    sb = _sb(_prop())
    async with _client(sb) as client:
        resp = await client.get('/properties/p1')

    assert resp.status_code == 200
    assert len(resp.json()['property']['imagenes']) == 20


async def test_la_galeria_recuperada_se_persiste(galeria_completa) -> None:
    """Si no se guarda, cada visita a la ficha vuelve a pegarle al portal."""
    sb = _sb(_prop())
    async with _client(sb) as client:
        await client.get('/properties/p1')

    assert len(sb.rows_updated) == 1
    assert len(sb.rows_updated[0]['imagenes']) == 20


async def test_una_ficha_sana_no_toca_el_portal(galeria_completa) -> None:
    """20 fotos ya guardadas: pedirle al portal otra vez es gasto puro."""
    sb = _sb(_prop(imagenes=[f'ok{i}.jpg' for i in range(20)]))
    async with _client(sb) as client:
        resp = await client.get('/properties/p1')

    assert galeria_completa == []
    assert len(resp.json()['property']['imagenes']) == 20


async def test_un_portal_caido_devuelve_la_ficha_igual(monkeypatch) -> None:
    """La galería es un extra: si el portal explota, la ficha sigue abriendo."""
    async def boom(prop: dict, allow_escalation: bool = True) -> list[str]:
        raise RuntimeError('portal caído')

    monkeypatch.setattr(properties, '_fetch_full_gallery', boom)
    sb = _sb(_prop())
    async with _client(sb) as client:
        resp = await client.get('/properties/p1')

    assert resp.status_code == 200
    assert len(resp.json()['property']['imagenes']) == 5


async def test_una_ficha_inexistente_sigue_dando_404(galeria_completa) -> None:
    sb = _sb(None)
    async with _client(sb) as client:
        resp = await client.get('/properties/p1')

    assert resp.status_code == 404


# ── El presupuesto de tiempo ─────────────────────────────────────────────────
#
# REGRESIÓN REAL, medida contra producción: la ficha pública se quedaba
# cargando para siempre (>90s sin respuesta) mientras el listado contestaba en
# 0.97s. La causa: recuperar la galería corría la escalera COMPLETA dentro del
# request — rung 2 levanta un Chromium headless y rung 3 dispara un actor de
# Apify, que tarda minutos. I/O sin techo en el camino caliente de una página
# que ya está compartida con un cliente.
#
# La ficha pública tiene UNA obligación: responder. La galería es un extra.


async def test_la_ficha_responde_aunque_el_portal_tarde_una_eternidad(monkeypatch) -> None:
    import asyncio

    async def eterno(prop, **kw):
        await asyncio.sleep(30)
        return ['nunca-llega.jpg']

    monkeypatch.setattr(properties, '_fetch_full_gallery', eterno)
    monkeypatch.setattr(properties, '_GALERIA_TIMEOUT', 0.05)

    sb = _sb(_prop())
    async with _client(sb) as client:
        resp = await asyncio.wait_for(client.get('/properties/p1'), timeout=5)

    assert resp.status_code == 200
    assert len(resp.json()['property']['imagenes']) == 5, 'devuelve lo guardado'
    assert sb.rows_updated == [], 'un intento cortado no escribe nada'


async def test_la_ficha_publica_no_paga_browser_ni_actor(monkeypatch) -> None:
    """Rung 2 (Chromium) y rung 3 (actor de Apify) están prohibidos acá.

    Un GET de httpx alcanza para los tres portales con parser propio. Escalar
    en el camino caliente es esperar minutos y, en el rung 3, pagar plata — por
    cada visita a una ficha compartida.
    """
    visto: dict = {}

    async def espia(prop, allow_escalation=True):
        visto['allow_escalation'] = allow_escalation
        return [f'ok{i}.jpg' for i in range(20)]

    monkeypatch.setattr(properties, '_fetch_full_gallery', espia)

    sb = _sb(_prop())
    async with _client(sb) as client:
        await client.get('/properties/p1')

    assert visto['allow_escalation'] is False


# ── Cuando el escalón barato no alcanza: Apify, pero FUERA del request ───────
#
# Medido contra producción con el fix del proxy ya deployado:
#   MercadoLibre  5 → 17 fotos   ✅ el escalón barato alcanza
#   ZonaProp      5 →  5 fotos   ❌ DataDome le sirve el muro igual
#   Argenprop     5 →  5 fotos   ❌ AWS WAF, mismo caso
#
# O sea: la recuperación CORRE, pero para ZonaProp/Argenprop el rung barato
# vuelve vacío. El único fetcher verificado contra esos WAF es el actor de
# Apify — y tarda minutos, así que NO puede correr dentro del request (esa
# lección ya costó una ficha colgada 90s).
#
# Solución: responder YA con lo guardado y disparar la escalera completa en
# background. La próxima visita ve la galería entera.
#
# El gasto tiene freno: un id sólo se agenda UNA vez por proceso. Sin eso, cada
# visita a una ficha compartida dispararía un run pago.


async def test_si_el_rung_barato_no_alcanza_agenda_la_escalera_completa(monkeypatch) -> None:
    properties._GALERIA_AGENDADAS.clear()
    agendadas: list[dict] = []

    async def barato_vacio(prop, allow_escalation=True):
        return []

    async def espia_lento(prop, sb):
        agendadas.append(prop)

    monkeypatch.setattr(properties, '_fetch_full_gallery', barato_vacio)
    monkeypatch.setattr(properties, '_recuperar_galeria_lenta', espia_lento)

    sb = _sb(_prop())
    async with _client(sb) as client:
        resp = await client.get('/properties/p1')

    assert resp.status_code == 200
    assert len(resp.json()['property']['imagenes']) == 5, 'responde ya, con lo que hay'
    assert len(agendadas) == 1


async def test_no_agenda_dos_veces_el_mismo_aviso(monkeypatch) -> None:
    """Cada agendada puede terminar en un run PAGO de Apify."""
    properties._GALERIA_AGENDADAS.clear()
    agendadas: list[dict] = []

    async def barato_vacio(prop, allow_escalation=True):
        return []

    async def espia_lento(prop, sb):
        agendadas.append(prop)

    monkeypatch.setattr(properties, '_fetch_full_gallery', barato_vacio)
    monkeypatch.setattr(properties, '_recuperar_galeria_lenta', espia_lento)

    sb = _sb(_prop())
    async with _client(sb) as client:
        await client.get('/properties/p1')
        await client.get('/properties/p1')
        await client.get('/properties/p1')

    assert len(agendadas) == 1


async def test_si_el_rung_barato_alcanza_no_agenda_nada(monkeypatch) -> None:
    """MercadoLibre se resuelve con el GET gratis: pagar Apify sería tirar plata."""
    properties._GALERIA_AGENDADAS.clear()
    agendadas: list[dict] = []

    async def barato_ok(prop, allow_escalation=True):
        return [f'ok{i}.jpg' for i in range(17)]

    async def espia_lento(prop, sb):
        agendadas.append(prop)

    monkeypatch.setattr(properties, '_fetch_full_gallery', barato_ok)
    monkeypatch.setattr(properties, '_recuperar_galeria_lenta', espia_lento)

    sb = _sb(_prop())
    async with _client(sb) as client:
        resp = await client.get('/properties/p1')

    assert len(resp.json()['property']['imagenes']) == 17
    assert agendadas == []


async def test_una_ficha_sana_ni_intenta_ni_agenda(monkeypatch) -> None:
    properties._GALERIA_AGENDADAS.clear()
    tocado: list = []

    async def no_deberia(prop, allow_escalation=True):
        tocado.append(prop)
        return []

    async def espia_lento(prop, sb):
        tocado.append(prop)

    monkeypatch.setattr(properties, '_fetch_full_gallery', no_deberia)
    monkeypatch.setattr(properties, '_recuperar_galeria_lenta', espia_lento)

    sb = _sb(_prop(imagenes=[f'ok{i}.jpg' for i in range(20)]))
    async with _client(sb) as client:
        await client.get('/properties/p1')

    assert tocado == []


async def test_la_escalera_lenta_si_usa_apify_y_persiste(monkeypatch) -> None:
    """El trabajo en background sí tiene permiso de escalar: ese es su sentido."""
    properties._GALERIA_AGENDADAS.clear()
    visto: dict = {}

    async def full(prop, allow_escalation=True):
        visto['allow_escalation'] = allow_escalation
        return [f'apify{i}.jpg' for i in range(20)]

    monkeypatch.setattr(properties, '_fetch_full_gallery', full)

    sb = _sb(_prop())
    await properties._recuperar_galeria_lenta(_prop(), sb)

    assert visto['allow_escalation'] is True
    assert len(sb.rows_updated) == 1
    assert len(sb.rows_updated[0]['imagenes']) == 20
