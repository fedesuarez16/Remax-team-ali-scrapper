"""Test-first for `POST /api/v1/scraping/start` accepting an optional
`source_selection` — the user picks WHERE to scrape before the search runs
(portales inmobiliarios and/or inmobiliarias, and for inmobiliarias, a single
manually-curated zona or all of them).

Written BEFORE `StartScrapingRequest` gains the field, so every
`source_selection` assertion MUST fail until the router lands it.

Contract (mirrors the `polygon`/`localidades` precedent: persisted on the job
row, later injected into the graph's `inputs` by `stream_scraping`):

- omitted            → persisted as the explicit default (all portales + all
                       inmobiliarias), so today's behaviour is unchanged.
- `portales: []`     → all portales (an empty subset means "no restriction").
- unknown portal id  → 400 (typo protection; the portal catalog is fixed code).
- nothing selected   → 400 (a search with no source would silently return 0).

Same lightweight fake `app.state.supabase` as
`test_scraping_start_polygon.py`, capturing the `.insert(...)` payload.
"""
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


class _FakeInsertQuery:
    def __init__(self, captured: list[dict], payload: dict) -> None:
        self._captured = captured
        self._payload = payload

    async def execute(self):
        self._captured.append(self._payload)
        return type('Res', (), {'data': [self._payload]})()


class _FakeTable:
    def __init__(self, captured: list[dict]) -> None:
        self._captured = captured

    def insert(self, payload: dict) -> _FakeInsertQuery:
        return _FakeInsertQuery(self._captured, payload)


class _FakeSupabase:
    def __init__(self) -> None:
        self.captured_inserts: list[dict] = []

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(self.captured_inserts)


def _make_app(fake_sb: _FakeSupabase) -> FastAPI:
    from app.api.v1 import scraping

    app = FastAPI()
    app.include_router(scraping.router)
    app.state.supabase = fake_sb
    return app


def _client(fake_sb: _FakeSupabase) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=_make_app(fake_sb)), base_url='http://test')


# ── default (omitted) ─────────────────────────────────────────────────────────


async def test_start_without_source_selection_persists_search_everything_default() -> None:
    fake_sb = _FakeSupabase()
    async with _client(fake_sb) as client:
        resp = await client.post('/start', json={'query': 'Casa 3 dormitorios en City Bell'})

    assert resp.status_code == 200
    assert len(fake_sb.captured_inserts) == 1
    assert fake_sb.captured_inserts[0]['source_selection'] == {
        'buscar_portales': True,
        'portales': [],
        'buscar_inmobiliarias': True,
        'zona_inmobiliarias': None,
        # Descubrir con Google Maps sigue prendido por defecto: apagarlo es una
        # decisión del operador, no algo que le pase por no elegir nada.
        'solo_fuentes_cargadas': False,
    }


# ── portales only ─────────────────────────────────────────────────────────────


async def test_start_with_portal_subset_persists_it() -> None:
    fake_sb = _FakeSupabase()
    async with _client(fake_sb) as client:
        resp = await client.post('/start', json={
            'query': 'Casa 3 dormitorios en City Bell',
            'source_selection': {
                'buscar_portales': True,
                'portales': ['zonaprop', 'argenprop'],
                'buscar_inmobiliarias': False,
            },
        })

    assert resp.status_code == 200
    persisted = fake_sb.captured_inserts[0]['source_selection']
    assert persisted['portales'] == ['zonaprop', 'argenprop']
    assert persisted['buscar_portales'] is True
    assert persisted['buscar_inmobiliarias'] is False
    assert persisted['zona_inmobiliarias'] is None


async def test_start_rejects_unknown_portal_id() -> None:
    fake_sb = _FakeSupabase()
    async with _client(fake_sb) as client:
        resp = await client.post('/start', json={
            'query': 'Casa en City Bell',
            'source_selection': {'buscar_portales': True, 'portales': ['inventado']},
        })

    assert resp.status_code == 400
    assert fake_sb.captured_inserts == []


# ── inmobiliarias (+ zona) ────────────────────────────────────────────────────


async def test_start_with_zona_inmobiliarias_persists_it() -> None:
    fake_sb = _FakeSupabase()
    async with _client(fake_sb) as client:
        resp = await client.post('/start', json={
            'query': 'Casa 3 dormitorios en City Bell',
            'source_selection': {
                'buscar_portales': False,
                'buscar_inmobiliarias': True,
                'zona_inmobiliarias': 'City Bell',
            },
        })

    assert resp.status_code == 200
    persisted = fake_sb.captured_inserts[0]['source_selection']
    assert persisted['buscar_portales'] is False
    assert persisted['buscar_inmobiliarias'] is True
    assert persisted['zona_inmobiliarias'] == 'City Bell'


async def test_start_blank_zona_inmobiliarias_means_all_zonas() -> None:
    fake_sb = _FakeSupabase()
    async with _client(fake_sb) as client:
        resp = await client.post('/start', json={
            'query': 'Casa en City Bell',
            'source_selection': {'buscar_inmobiliarias': True, 'zona_inmobiliarias': '   '},
        })

    assert resp.status_code == 200
    assert fake_sb.captured_inserts[0]['source_selection']['zona_inmobiliarias'] is None


# ── nothing selected ──────────────────────────────────────────────────────────


async def test_start_with_no_source_selected_is_rejected() -> None:
    fake_sb = _FakeSupabase()
    async with _client(fake_sb) as client:
        resp = await client.post('/start', json={
            'query': 'Casa en City Bell',
            'source_selection': {'buscar_portales': False, 'buscar_inmobiliarias': False},
        })

    assert resp.status_code == 400
    assert fake_sb.captured_inserts == []


async def test_start_portales_on_but_empty_subset_is_valid() -> None:
    """`buscar_portales=True` + `portales=[]` means "todos los portales", the
    same shape the default persists — it must NOT be read as "none selected"."""
    fake_sb = _FakeSupabase()
    async with _client(fake_sb) as client:
        resp = await client.post('/start', json={
            'query': 'Casa en City Bell',
            'source_selection': {
                'buscar_portales': True, 'portales': [], 'buscar_inmobiliarias': False,
            },
        })

    assert resp.status_code == 200
    assert fake_sb.captured_inserts[0]['source_selection']['portales'] == []


# ── coexistence with polygon/localidades (map flow) ───────────────────────────


async def test_start_source_selection_coexists_with_polygon_and_localidades() -> None:
    fake_sb = _FakeSupabase()
    polygon = [[-34.58, -58.43], [-34.59, -58.44], [-34.60, -58.42]]
    async with _client(fake_sb) as client:
        resp = await client.post('/start', json={
            'query': 'Propiedades en City Bell',
            'polygon': polygon,
            'localidades': ['La Plata'],
            'source_selection': {'buscar_portales': True, 'buscar_inmobiliarias': False},
        })

    assert resp.status_code == 200
    inserted = fake_sb.captured_inserts[0]
    assert inserted['polygon'] == polygon
    assert inserted['localidades'] == ['La Plata']
    assert inserted['source_selection']['buscar_inmobiliarias'] is False
