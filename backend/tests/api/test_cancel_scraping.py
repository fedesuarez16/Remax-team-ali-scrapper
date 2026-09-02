"""Detener una búsqueda la cierra bien; no la rompe.

Una búsqueda con inmobiliarias son minutos largos y el operador no siempre
quiere esperarlos: ya vio lo que necesitaba, o se dio cuenta de que la zona
estaba mal. Hasta ahora la única salida era cerrar la pestaña — que NO frena
nada, porque `_run_graph_into_queue` corre en su propia tarea justamente para
que un cliente que se desconecta no cancele el grafo. La búsqueda seguía
gastando Apify y tokens para nadie.

Lo que fija este archivo:

- El registro de tareas se puede direccionar POR JOB. Era un `set` sin llaves,
  y sobre un `set` no hay forma de cancelar una búsqueda en particular.
- Cancelar cierra el stream por la puerta de adelante: un evento `done` con
  `cancelled: true`, no un `error`. El frontend ya sabe rendir `done` y traer
  las propiedades; una búsqueda detenida a propósito no es una que falló.
- La fila del job queda en `cancelled`, que es un hecho distinto de `done` y de
  `error` — el historial tiene que poder decir cuál fue.
- Cancelar un job que no existe (ya terminó, otro proceso) no es un error del
  servidor: no hay nada que frenar y eso ya es el estado deseado.
"""
import asyncio
from typing import Any

import pytest

from app.api.v1 import scraping as api


@pytest.fixture(autouse=True)
def _clean_registry():
    api._graph_tasks.clear()
    yield
    for task in list(api._graph_tasks.values()):
        task.cancel()
    api._graph_tasks.clear()


class _CapturingSupabase:
    """Registra los payloads escritos en `scraping_jobs`."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def table(self, _name: str) -> '_CapturingSupabase':
        return self

    def update(self, payload: dict[str, Any]) -> '_CapturingSupabase':
        self.payloads.append(payload)
        return self

    def eq(self, *_a: Any, **_kw: Any) -> '_CapturingSupabase':
        return self

    async def execute(self) -> Any:
        return type('_Res', (), {'data': []})()


# ── El registro direccionable ─────────────────────────────────────────────────

async def test_una_tarea_se_registra_bajo_su_job() -> None:
    async def _forever() -> None:
        await asyncio.Event().wait()

    api._spawn_graph_task('job-1', _forever())

    assert 'job-1' in api._graph_tasks


async def test_la_tarea_se_desregistra_sola_al_terminar() -> None:
    """Sin esto el registro crece por cada búsqueda del proceso."""
    async def _quick() -> None:
        return None

    api._spawn_graph_task('job-1', _quick())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert 'job-1' not in api._graph_tasks


# ── Cancelar ──────────────────────────────────────────────────────────────────

async def test_cancelar_frena_la_tarea_de_ese_job() -> None:
    started = asyncio.Event()

    async def _forever() -> None:
        started.set()
        await asyncio.Event().wait()

    api._spawn_graph_task('job-1', _forever())
    await started.wait()

    assert await api._cancel_graph_task('job-1') is True
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert 'job-1' not in api._graph_tasks


async def test_cancelar_no_toca_las_otras_busquedas() -> None:
    """El bug que un `set` sin llaves garantizaba: parar una y parar todas eran
    la misma operación."""
    async def _forever() -> None:
        await asyncio.Event().wait()

    api._spawn_graph_task('job-1', _forever())
    api._spawn_graph_task('job-2', _forever())

    await api._cancel_graph_task('job-1')
    await asyncio.sleep(0)

    assert 'job-2' in api._graph_tasks
    assert not api._graph_tasks['job-2'].cancelled()


async def test_cancelar_un_job_desconocido_no_explota() -> None:
    """Ya terminó, o corre en otro worker: no hay nada que frenar, y eso ya es
    el estado que el usuario pidió."""
    assert await api._cancel_graph_task('job-inexistente') is False


# ── Cómo se cierra el stream ──────────────────────────────────────────────────

async def test_el_corte_sale_como_done_y_no_como_error() -> None:
    """Una búsqueda detenida a propósito no falló. Mandarla por `error` haría
    que el cliente escriba 'Error: ...' y no traiga las propiedades."""
    queue: asyncio.Queue[Any] = asyncio.Queue()
    await queue.put(('cancelled', None))
    sb = _CapturingSupabase()

    frames = [f async for f in api._stream_graph_events(queue, sb, 'job-1', {})]

    assert len(frames) == 1
    assert 'event: done' in frames[0]
    assert '"cancelled": true' in frames[0]


async def test_el_corte_deja_la_fila_en_cancelled() -> None:
    """`cancelled` es un hecho distinto de `done` y de `error`; el historial
    tiene que poder decir cuál de los tres fue."""
    queue: asyncio.Queue[Any] = asyncio.Queue()
    await queue.put(('cancelled', None))
    sb = _CapturingSupabase()

    [f async for f in api._stream_graph_events(queue, sb, 'job-1', {})]

    assert sb.payloads[-1]['estado'] == 'cancelled'


async def test_el_corte_tambien_informa_el_gasto() -> None:
    """Detener no perdona lo ya gastado. El operador necesita ver el número de
    una búsqueda cortada tanto como el de una completa — más, incluso, porque
    cortar suele ser una decisión de costo."""
    queue: asyncio.Queue[Any] = asyncio.Queue()
    await queue.put(('cancelled', None))
    sb = _CapturingSupabase()
    ledger = {'googlemaps': {'usd': 0.3, 'runs': 1}}

    frames = [f async for f in api._stream_graph_events(queue, sb, 'job-1', ledger)]

    assert '"apify_cost_usd": 0.3' in frames[0]
    assert sb.payloads[-1]['apify_cost_usd'] == 0.3


async def test_una_tarea_cancelada_avisa_por_la_cola() -> None:
    """El eslabón que hace que todo lo de arriba se dispare: `CancelledError`
    hereda de `BaseException`, así que el `except Exception` del runner NO la
    atrapa. Sin un `except asyncio.CancelledError` explícito la cola nunca
    recibe nada y el stream queda mandando keepalives para siempre."""
    queue: asyncio.Queue[Any] = asyncio.Queue()

    class _Graph:
        async def astream_events(self, *_a: Any, **_kw: Any) -> Any:
            await asyncio.Event().wait()
            yield {}  # pragma: no cover

    task = asyncio.ensure_future(
        api._run_graph_into_queue(_Graph(), {}, {}, queue, None, 'job-1', {}),
    )
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert queue.get_nowait() == ('cancelled', None)
