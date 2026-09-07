"""A cached feed's eight 360px thumbnails must become the full detail gallery."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import properties
from app.services import ficha, importer

URL = 'https://www.zonaprop.com.ar/propiedades/clasificado/casa-12345678.html'
CDN = 'https://imgar.zonapropcdn.com/avisos'
PATH = '1/00/12/34/56/78'
THUMBS = [f'{CDN}/{PATH}/360x266/{i}.jpg' for i in range(19)]
FULL = [f'{CDN}/resize/{PATH}/1200x1200/{i}.jpg' for i in range(19)]
# Sanitized structure of the detail page: the visible mosaic has only a few
# thumbnails, while the embedded pictures array has all 19 at several sizes.
HTML = (
    '<html><body>'
    + ''.join(f'<img src="{u}">' for u in THUMBS[:5])
    + '<script>var posting = {\'pictures\': '
    + json.dumps([
        {'multimediaTypeId': 2, 'order': i, 'height': 1200, 'width': 1600,
         'resizeUrl1200x1200': full, 'url360x266': thumb}
        for i, (full, thumb) in enumerate(zip(FULL, THUMBS, strict=True))
    ]) + '};</script></body></html>'
)


def cached_property(source='zonaprop'):
    return {'id': 'p1', 'fuente': source, 'url_origen': URL,
            'imagenes': THUMBS[:8], 'ficha_enriched': True}


class Database:
    def __init__(self, row):
        self.row = row
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
        self.row.update(patch)
        return self

    async def execute(self):
        return SimpleNamespace(data=[self.row.copy()])


@pytest.mark.parametrize('source', ['manual', 'zonaprop'])
@pytest.mark.parametrize('count', [8, 19])
def test_thumbnails_need_recovery_even_above_the_count_threshold(source, count):
    prop = {**cached_property(source), 'imagenes': THUMBS[:count]}
    assert ficha._gallery_looks_incomplete(prop)


@pytest.mark.parametrize('size', ['100x75', '215x159', '360x266', '720x532'])
@pytest.mark.parametrize('resize', ['', 'resize/'])
def test_thumbnail_detection_uses_the_cdn_size(size, resize):
    prop = {**cached_property(), 'imagenes': [
        f'{CDN}/{resize}{PATH}/{size}/{i}.jpg?isFirstImage=true' for i in range(8)
    ]}
    assert ficha._gallery_looks_incomplete(prop)


@pytest.mark.parametrize('count', [8, 19])
def test_a_real_high_resolution_gallery_is_not_refetched(count):
    prop = {**cached_property(), 'imagenes': FULL[:count]}
    assert not ficha._gallery_looks_incomplete(prop)


@pytest.mark.parametrize('host', [
    'example.com', 'imgar.zonapropcdn.com.example.com', 'zonapropcdn.com.evil.test',
])
def test_unrelated_image_hosts_are_not_treated_as_zonaprop_thumbnails(host):
    prop = {**cached_property(), 'imagenes': [
        f'https://{host}/avisos/{PATH}/360x266/{i}.jpg' for i in range(8)
    ]}
    assert not ficha._gallery_looks_incomplete(prop)


@pytest.mark.parametrize('source', ['manual', 'zonaprop'])
async def test_reusing_a_cached_import_recovers_all_19_large_photos_once(monkeypatch, source):
    db = Database(cached_property(source))
    fetch = AsyncMock(return_value=(False, HTML))
    monkeypatch.setattr(ficha, '_fetch_listing_html', fetch)
    redundant = AsyncMock(side_effect=AssertionError('Cached import must not run extraction'))
    monkeypatch.setattr(importer, '_fetch_page', redundant)
    monkeypatch.setattr(importer, '_extract_llm', redundant)

    result = await importer.import_property_from_url(db, URL)
    assert result['created'] is False
    enriched = await ficha.enrich_ficha(result['property'], db)
    assert enriched['imagenes'] == FULL
    assert len(db.updates) == 1
    assert db.row['imagenes'] == FULL
    assert not any('/360x266/' in image for image in enriched['imagenes'])
    fetch.assert_awaited_once_with(URL, None)
    redundant.assert_not_called()

    # Next generation reuses the repaired row without a second download.
    repeated = await importer.import_property_from_url(db, URL)
    await ficha.enrich_ficha(repeated['property'], db)
    assert fetch.await_count == 1
    assert len(db.updates) == 1


async def test_opening_the_public_ficha_also_repairs_the_cached_thumbnails(monkeypatch):
    db = Database(cached_property())
    fetch = AsyncMock(return_value=(False, HTML))
    monkeypatch.setattr(ficha, '_fetch_listing_html', fetch)
    app = FastAPI()
    app.include_router(properties.router, prefix='/properties')
    app.state.supabase = db

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as c:
        response = await c.get('/properties/p1')
        assert response.status_code == 200
        assert response.json()['property']['imagenes'] == FULL
        await c.get('/properties/p1')

    assert fetch.await_count == 1
    assert db.updates == [{'imagenes': FULL}]
