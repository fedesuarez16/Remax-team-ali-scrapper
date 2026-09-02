"""El fan-out de Instagram tiene que hacer fila, igual que el de sitios web.

`route_after_review` emite un `Send('run_instagram_scraper', ...)` por CADA
inmobiliaria con handle. El track de sitios web pasa por
`WEBSITE_SCRAPE_CONCURRENCY`; el de Instagram no tenía nada, así que 390
agencias abrían 390 runs de Apify a la vez, cada uno polleando hasta
`_TIMEOUT` (300 s). La búsqueda parecía colgada — estaba esperando a todos.

Es el mismo bug que ya se había arreglado del lado de los sitios web ("sin tope
una búsqueda con 260 inmobiliarias abría ~1500 requests de una y el proceso se
quedaba sin sockets"). El track de Instagram quedó afuera de esa lección.

El tope NO descarta perfiles: los pone en fila. Ningún handle se pierde.
"""
import asyncio
from typing import Any

import pytest

from app.core.config import settings
from app.graphs.extraction import nodes
from app.graphs.extraction.nodes import run_instagram_scraper

_CONFIG: Any = {'configurable': {'supabase': None}}


@pytest.fixture(autouse=True)
def _clean():
    nodes._instagram_semaphore = None
    nodes._budget_notified.clear()
    yield
    nodes._instagram_semaphore = None
    nodes._budget_notified.clear()


@pytest.fixture(autouse=True)
def _silence(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(*_a: Any, **_kw: Any) -> None:
        return None
    monkeypatch.setattr(nodes, 'adispatch_custom_event', _noop)


def test_el_default_no_deja_el_fanout_suelto() -> None:
    """`0` acá no puede significar "sin tope" como en los knobs de paginado:
    sin tope, esto son cientos de runs de Apify simultáneos."""
    from app.core.config import Settings
    assert Settings.model_fields['INSTAGRAM_SCRAPE_CONCURRENCY'].default > 0


async def test_no_corren_mas_perfiles_a_la_vez_que_el_tope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, 'INSTAGRAM_SCRAPE_CONCURRENCY', 3)
    vivos = 0
    pico = 0

    class _Service:
        async def scrape_instagram_profile(self, handle: str, on_progress: Any) -> list[Any]:
            nonlocal vivos, pico
            vivos += 1
            pico = max(pico, vivos)
            await asyncio.sleep(0.01)
            vivos -= 1
            return []

    monkeypatch.setattr(nodes, 'get_apify_service', lambda: _Service())

    await asyncio.gather(*(
        run_instagram_scraper({'handle': f'h{i}', 'nombre': f'N{i}', 'job_id': 'job-1'}, _CONFIG)
        for i in range(12)
    ))

    assert pico <= 3, f'corrieron {pico} runs de Apify a la vez'


async def test_ningun_handle_se_pierde(monkeypatch: pytest.MonkeyPatch) -> None:
    """El tope pone en fila, no descarta. Un perfil que no entra ahora entra
    después."""
    monkeypatch.setattr(settings, 'INSTAGRAM_SCRAPE_CONCURRENCY', 2)
    vistos: list[str] = []

    class _Service:
        async def scrape_instagram_profile(self, handle: str, on_progress: Any) -> list[Any]:
            vistos.append(handle)
            return []

    monkeypatch.setattr(nodes, 'get_apify_service', lambda: _Service())

    await asyncio.gather(*(
        run_instagram_scraper({'handle': f'h{i}', 'nombre': f'N{i}', 'job_id': 'job-1'}, _CONFIG)
        for i in range(10)
    ))

    assert sorted(vistos) == sorted(f'h{i}' for i in range(10))


async def test_un_perfil_que_explota_libera_su_lugar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si el semáforo no se liberara ante una excepción, un perfil caído
    consumiría un lugar para siempre y el fan-out se iría estrangulando hasta
    frenarse del todo."""
    monkeypatch.setattr(settings, 'INSTAGRAM_SCRAPE_CONCURRENCY', 1)

    class _Service:
        async def scrape_instagram_profile(self, handle: str, on_progress: Any) -> list[Any]:
            raise RuntimeError('perfil privado')

    monkeypatch.setattr(nodes, 'get_apify_service', lambda: _Service())

    await asyncio.wait_for(
        asyncio.gather(*(
            run_instagram_scraper({'handle': f'h{i}', 'nombre': f'N{i}', 'job_id': 'job-1'}, _CONFIG)
            for i in range(5)
        )),
        timeout=5,
    )

    sem = nodes._get_instagram_semaphore()
    assert not sem.locked(), 'quedó un lugar tomado por un perfil que falló'
