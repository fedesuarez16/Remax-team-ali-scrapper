"""POST /import itself must return every ZonaProp photo, including cached URLs."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import properties
from app.services import ficha, importer

URL = 'https://www.zonaprop.com.ar/propiedades/clasificado/casa-12345678.html'
CDN = 'https://imgar.zonapropcdn.com/avisos/resize/1/00/12/34/56/78'


def gallery_html(count):
    photos = [f'{CDN}/1200x1200/{i}.jpg' for i in range(count)]
    html = '<html><body><p>' + 'Casa con patio, cochera y dos dormitorios. ' * 40 + '</p>'
    html += '<script>var posting = {"pictures": '
    html += json.dumps([{'resizeUrl1200x1200': u} for u in photos])
    return html + '};</script></body></html>', photos


class Database:
    def __init__(self, cached):
        self.rows = [{
            'id': 'p1', 'fuente': 'zonaprop', 'url_origen': URL,
            'imagenes': [f'{CDN}/720x532/{i}.jpg' for i in range(8)],
            'ficha_enriched': True, 'titulo': 'Título editado',
        }] if cached else []
        self.inserts = []
        self.updates = []

    def table(self, name):
        assert name == 'properties'
        return self

    def select(self, *args):
        return self

    def eq(self, *args):
        return self

    def limit(self, *args):
        return self

    def update(self, patch):
        self.updates.append(patch)
        self.rows[0].update(patch)
        return self

    def insert(self, prop):
        self.inserts.append(prop)
        self.rows.append({**prop, 'id': 'p1'})
        return self

    async def execute(self):
        return SimpleNamespace(data=[row.copy() for row in self.rows])


def client(db):
    app = FastAPI()
    app.include_router(properties.router, prefix='/properties')
    app.state.supabase = db
    return httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test')


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('count', [23, 47])
async def test_import_returns_every_large_photo_before_enrich(monkeypatch, cached, count):
    html, photos = gallery_html(count)
    fetch = AsyncMock(return_value=html)
    monkeypatch.setattr(importer, '_fetch_html_httpx', fetch)
    llm = AsyncMock(return_value=({'titulo': 'Casa de prueba', 'direccion': 'Calle 123'}, None))
    monkeypatch.setattr(importer, '_extract_llm', llm)
    monkeypatch.setattr(importer, 'record_llm_usage', AsyncMock())
    optional = AsyncMock(side_effect=AssertionError('Import cannot rely on optional enrichment'))
    monkeypatch.setattr(ficha, '_enrich_gallery', optional)
    db = Database(cached)

    async with client(db) as c:
        response = await c.post('/properties/import', json={'urls': [URL]})
    result = response.json()['results'][0]
    assert result['status'] == 'ok'
    assert result['created'] is not cached
    assert result['gallery_complete'] is True
    assert result['property']['imagenes'] == photos
    assert db.rows[0]['imagenes'] == photos
    assert len(db.rows) == 1
    if cached:
        assert result['property']['titulo'] == 'Título editado'
        llm.assert_not_called()
        assert db.inserts == []
    fetch.assert_awaited_once_with(URL)
    optional.assert_not_called()


async def test_gallery_gets_the_import_budget_instead_of_optional_enrich_budget(monkeypatch):
    html, photos = gallery_html(23)
    async def slower_than_optional_recovery(url):
        await asyncio.sleep(0.04)
        return html

    monkeypatch.setattr(ficha, '_ZONAPROP_GALLERY_TIMEOUT', 0.01)
    monkeypatch.setattr(importer, '_ZONAPROP_FETCH_TIMEOUT', 0.5)
    monkeypatch.setattr(importer, '_fetch_html_httpx', slower_than_optional_recovery)
    async with client(Database(True)) as c:
        response = await asyncio.wait_for(c.post('/properties/import', json={'urls': [URL]}), 1)
    assert response.json()['results'][0]['property']['imagenes'] == photos


@pytest.mark.parametrize('cached', [False, True])
async def test_missing_native_gallery_is_an_error_not_a_success_with_thumbnails(monkeypatch, cached):
    # Plenty of visible text and eight real thumbnails still do not prove that
    # the native gallery was read. This used to silently return a partial ficha.
    html = '<html><body><p>' + 'Casa con patio y cochera. ' * 40 + '</p>'
    html += ''.join(f'<img src="{CDN}/360x266/{i}.jpg">' for i in range(8))
    html += '</body></html>'
    monkeypatch.setattr(importer, '_fetch_html_httpx', AsyncMock(return_value=html))
    llm = AsyncMock(side_effect=AssertionError('Incomplete gallery must fail before AI'))
    monkeypatch.setattr(importer, '_extract_llm', llm)
    db = Database(cached)
    async with client(db) as c:
        response = await c.post('/properties/import', json={'urls': [URL]})
    result = response.json()['results'][0]
    assert result['status'] == 'error'
    assert 'galería completa' in result['error']
    assert 'property' not in result
    assert db.updates == []
    assert db.inserts == []
    llm.assert_not_called()


async def test_failed_recovery_cannot_report_the_old_eight_photos_as_success(monkeypatch):
    monkeypatch.setattr(importer, '_fetch_html_httpx', AsyncMock(side_effect=importer.PortalBlocked))
    monkeypatch.setattr(importer, 'render_page_html', AsyncMock(return_value=None))
    actor = AsyncMock(side_effect=AssertionError('No paid actor in a ficha import'))
    monkeypatch.setattr(importer, 'fetch_page_html_via_actor', actor)
    db = Database(True)
    async with client(db) as c:
        response = await c.post('/properties/import', json={'urls': [URL]})
    result = response.json()['results'][0]
    assert result['status'] == 'error'
    assert 'property' not in result
    assert db.updates == []
    assert len(db.rows[0]['imagenes']) == 8
    actor.assert_not_called()
