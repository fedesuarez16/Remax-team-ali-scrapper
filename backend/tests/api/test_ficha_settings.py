"""Test-first for `GET/PATCH /api/v1/ficha-settings` — the editable texts that
every Ficha Propio shares (contact blurb, legal disclaimer, footer signature).

These are TEAM-WIDE, not per-property: editing them once changes every ficha,
old and new. That's the whole point — they were module constants in
`frontend/lib/ficha.ts` before, so a singleton row is the faithful translation.

Two invariants matter more than the CRUD itself:

1. GET ALWAYS returns usable texts. The public `/p/[id]` page renders them on
   every visit, so a missing row, an unconfigured Supabase, or a dead query
   must degrade to the built-in defaults — never to an empty footer.
2. PATCH is presence-checked and rejects blanks, so a half-filled form can't
   wipe the legal disclaimer off every published ficha at once.

Fake `app.state.supabase` mirrors the fluent `.table(name).select(...)/
.upsert(...).limit/.execute()` chain, same pattern as `test_saved_zones.py`.
"""
from __future__ import annotations

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

FIELDS = ('texto_seleccion', 'disclaimer_legal', 'firma', 'colegiatura', 'pie_publicacion')


class _Res:
    def __init__(self, data) -> None:
        self.data = data


class _FakeQuery:
    def __init__(self, store: list[dict], mode: str) -> None:
        self._store = store
        self._mode = mode  # 'select' | 'upsert'
        self._payload: dict | None = None

    def select(self, *_a, **_kw) -> '_FakeQuery':
        return self

    def upsert(self, payload: dict) -> '_FakeQuery':
        self._payload = payload
        return self

    def limit(self, _n: int) -> '_FakeQuery':
        return self

    async def execute(self) -> _Res:
        if self._mode == 'upsert':
            row = dict(self._payload or {})
            if self._store:
                self._store[0].update(row)
            else:
                self._store.append(row)
            return _Res([self._store[0]])
        return _Res(list(self._store))


class _FakeTable:
    def __init__(self, store: list[dict]) -> None:
        self._store = store

    def select(self, *a, **kw) -> _FakeQuery:
        return _FakeQuery(self._store, 'select').select(*a, **kw)

    def upsert(self, payload: dict) -> _FakeQuery:
        return _FakeQuery(self._store, 'upsert').upsert(payload)


class _FakeSupabase:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self._store: list[dict] = rows or []

    def table(self, name: str) -> _FakeTable:
        if name == 'ficha_settings':
            return _FakeTable(self._store)
        raise AssertionError(f'unexpected table {name}')


class _RaisingTable:
    def select(self, *_a, **_kw):
        raise RuntimeError('boom')

    def upsert(self, *_a, **_kw):
        raise RuntimeError('boom')


class _RaisingSupabase:
    def table(self, name: str) -> _RaisingTable:
        return _RaisingTable()


def _make_app(fake_sb) -> FastAPI:
    from app.api.v1 import ficha_settings

    app = FastAPI()
    app.include_router(ficha_settings.router, prefix='/ficha-settings')
    app.state.supabase = fake_sb
    return app


def _client(fake_sb) -> AsyncClient:
    # NOTE: base_url deliberately has no trailing path segment — see
    # test_search_history.py for why (avoids a 307 redirect on the bare route).
    transport = ASGITransport(app=_make_app(fake_sb))
    return AsyncClient(transport=transport, base_url='http://test')


def _row(**overrides) -> dict:
    from app.api.v1.ficha_settings import DEFAULT_TEXTOS

    return {'id': 1, **DEFAULT_TEXTOS, **overrides}


# -- GET -------------------------------------------------------------------


async def test_get_returns_stored_texts() -> None:
    fake_sb = _FakeSupabase([_row(firma='Nahir Alí | Diagonal II')])
    async with _client(fake_sb) as client:
        resp = await client.get('/ficha-settings')

    assert resp.status_code == 200
    settings = resp.json()['settings']
    assert settings['firma'] == 'Nahir Alí | Diagonal II'
    assert set(FIELDS) <= set(settings)


async def test_get_without_row_falls_back_to_defaults() -> None:
    """A fresh install has no row yet — the public ficha still needs its
    footer, so the built-in texts stand in."""
    from app.api.v1.ficha_settings import DEFAULT_TEXTOS

    async with _client(_FakeSupabase([])) as client:
        resp = await client.get('/ficha-settings')

    assert resp.status_code == 200
    assert resp.json()['settings'] == DEFAULT_TEXTOS


async def test_get_when_supabase_not_configured_falls_back_to_defaults() -> None:
    from app.api.v1.ficha_settings import DEFAULT_TEXTOS

    async with _client(None) as client:
        resp = await client.get('/ficha-settings')

    assert resp.status_code == 200
    assert resp.json()['settings'] == DEFAULT_TEXTOS


async def test_get_failure_falls_back_to_defaults_without_raising() -> None:
    """A dead query must never blank out a published ficha's footer."""
    from app.api.v1.ficha_settings import DEFAULT_TEXTOS

    async with _client(_RaisingSupabase()) as client:
        resp = await client.get('/ficha-settings')

    assert resp.status_code == 200
    data = resp.json()
    assert data['settings'] == DEFAULT_TEXTOS
    assert 'error' in data


async def test_get_backfills_missing_columns_with_defaults() -> None:
    """A row written before a new text was introduced must not render as
    `null` in the footer — each absent field falls back individually."""
    from app.api.v1.ficha_settings import DEFAULT_TEXTOS

    fake_sb = _FakeSupabase([{'id': 1, 'firma': 'Solo firma'}])
    async with _client(fake_sb) as client:
        resp = await client.get('/ficha-settings')

    settings = resp.json()['settings']
    assert settings['firma'] == 'Solo firma'
    assert settings['disclaimer_legal'] == DEFAULT_TEXTOS['disclaimer_legal']


# -- PATCH -----------------------------------------------------------------


async def test_patch_updates_a_single_text_leaving_the_rest_intact() -> None:
    from app.api.v1.ficha_settings import DEFAULT_TEXTOS

    fake_sb = _FakeSupabase([_row()])
    async with _client(fake_sb) as client:
        resp = await client.patch('/ficha-settings', json={'firma': '  Nahir Alí | Diagonal II  '})

    settings = resp.json()['settings']
    assert settings['firma'] == 'Nahir Alí | Diagonal II'  # trimmed
    assert settings['texto_seleccion'] == DEFAULT_TEXTOS['texto_seleccion']


async def test_patch_preserves_previously_customised_texts() -> None:
    """Editing one field must not revert the OTHERS to their defaults.

    The write merges over the stored row, not over the built-in texts — a team
    that customised its disclaimer months ago would otherwise silently get the
    stock one back the next time someone fixed a typo in the signature.
    """
    fake_sb = _FakeSupabase([_row(disclaimer_legal='Descargo propio del equipo')])
    async with _client(fake_sb) as client:
        resp = await client.patch('/ficha-settings', json={'firma': 'Nahir Alí | Diagonal II'})

    settings = resp.json()['settings']
    assert settings['firma'] == 'Nahir Alí | Diagonal II'
    assert settings['disclaimer_legal'] == 'Descargo propio del equipo'


async def test_patch_creates_the_row_when_none_exists() -> None:
    """First edit on a fresh install writes the singleton, merging the change
    over the defaults so no column lands empty."""
    from app.api.v1.ficha_settings import DEFAULT_TEXTOS

    fake_sb = _FakeSupabase([])
    async with _client(fake_sb) as client:
        resp = await client.patch('/ficha-settings', json={'colegiatura': 'C.D. 1234'})

    settings = resp.json()['settings']
    assert settings['colegiatura'] == 'C.D. 1234'
    assert settings['disclaimer_legal'] == DEFAULT_TEXTOS['disclaimer_legal']
    assert len(fake_sb._store) == 1


async def test_patch_accepts_every_editable_text() -> None:
    fake_sb = _FakeSupabase([_row()])
    payload = {f: f'nuevo {f}' for f in FIELDS}
    async with _client(fake_sb) as client:
        resp = await client.patch('/ficha-settings', json=payload)

    settings = resp.json()['settings']
    for f in FIELDS:
        assert settings[f] == f'nuevo {f}'


async def test_patch_rejects_blank_text() -> None:
    """An empty disclaimer would publish every ficha without its legally
    required notice — refuse rather than persist it."""
    fake_sb = _FakeSupabase([_row()])
    async with _client(fake_sb) as client:
        resp = await client.patch('/ficha-settings', json={'disclaimer_legal': '   '})

    data = resp.json()
    assert data['settings'] is None
    assert 'error' in data
    assert fake_sb._store[0]['disclaimer_legal']  # untouched


async def test_patch_rejects_non_string_text() -> None:
    fake_sb = _FakeSupabase([_row()])
    async with _client(fake_sb) as client:
        resp = await client.patch('/ficha-settings', json={'firma': 42})

    data = resp.json()
    assert data['settings'] is None
    assert 'error' in data


async def test_patch_ignores_unknown_fields() -> None:
    """System-owned columns (id, updated_at) and typos are silently dropped,
    same convention as the properties PATCH whitelist."""
    fake_sb = _FakeSupabase([_row()])
    async with _client(fake_sb) as client:
        resp = await client.patch('/ficha-settings', json={
            'firma': 'Nueva firma', 'id': 99, 'columna_inventada': 'x',
        })

    assert resp.json()['settings']['firma'] == 'Nueva firma'
    assert fake_sb._store[0]['id'] == 1
    assert 'columna_inventada' not in fake_sb._store[0]


async def test_patch_with_nothing_editable_returns_error() -> None:
    fake_sb = _FakeSupabase([_row()])
    async with _client(fake_sb) as client:
        resp = await client.patch('/ficha-settings', json={'columna_inventada': 'x'})

    data = resp.json()
    assert data['settings'] is None
    assert 'error' in data


async def test_patch_when_supabase_not_configured_returns_error() -> None:
    """Unlike GET, a failed write must be visible — the user needs to know
    their edit did not persist."""
    async with _client(None) as client:
        resp = await client.patch('/ficha-settings', json={'firma': 'X'})

    data = resp.json()
    assert data['settings'] is None
    assert 'error' in data


async def test_patch_failure_returns_error_without_raising() -> None:
    async with _client(_RaisingSupabase()) as client:
        resp = await client.patch('/ficha-settings', json={'firma': 'X'})

    assert resp.status_code == 200
    data = resp.json()
    assert data['settings'] is None
    assert 'error' in data
