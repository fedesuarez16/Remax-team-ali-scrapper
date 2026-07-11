"""
Test del flujo completo de scraping sin levantar el servidor HTTP.
Invoca el grafo directamente, selecciona todas las agencias automáticamente.

Uso:
    cd backend
    uv run python test_full_flow.py
"""
import asyncio
import json

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.graphs.extraction.graph import build_graph

QUERY = (
    'Casa o PH 2 o más dormitorios con parque verde y cochera en lo posible, '
    'zonas Casco de La Plata, Los Hornos, La Cumbre, San Carlos, Tolosa. '
    'Presupuesto entre 80.000 y 100.000 USD'
)
JOB_ID = 'test-001'


async def run() -> None:
    checkpointer = MemorySaver()
    graph = build_graph(checkpointer=checkpointer)
    config = {'configurable': {'thread_id': JOB_ID, 'supabase': None}}
    inputs = {'query': QUERY, 'job_id': JOB_ID}

    print(f'Query: {QUERY}\n')
    print('=' * 60)

    agencies_found: list[dict] = []
    interrupted = False

    # ── Fase 1: correr hasta el interrupt ─────────────────────────────────────
    async for ev in graph.astream_events(inputs, config, version='v2'):
        if ev['event'] != 'on_custom_event':
            continue
        name = ev['name']
        data = ev['data']

        if name == 'progress':
            src = data.get('source', '')
            status = data.get('status', '')
            count = data.get('count', 0)
            msg = data.get('message', '')
            print(f'[{src}] {status} — {msg or count}')

        elif name == 'property_batch':
            src = data.get('source', '')
            count = data.get('count', 0)
            print(f'\n✓ {count} propiedades de {src}')
            props = data.get('properties', [])
            for p in props[:2]:
                print(f'  · {p.get("titulo", "")[:60]} | {p.get("precio")} {p.get("moneda")}')
            if len(props) > 2:
                print(f'  ... y {len(props)-2} más')

        elif name == 'agencies_review':
            agencies_found = data.get('agencies', [])
            print(f'\n── INTERRUPT: {len(agencies_found)} inmobiliarias encontradas ──')
            for a in agencies_found[:5]:
                print(f'  · {a["nombre"]} | web: {a.get("sitio_web") or "—"} | ig: @{a.get("instagram_handle") or "—"}')
            if len(agencies_found) > 5:
                print(f'  ... y {len(agencies_found)-5} más')
            interrupted = True
            break

        elif name == 'done':
            total = data.get('total_count', 0)
            print(f'\n✓ DONE — {total} propiedades totales')
            return

        elif name == 'error':
            print(f'\n✗ ERROR: {data.get("message")}')

        elif name == 'clarification':
            print(f'\n? Clarification needed: {data.get("message")}')
            return

    if not interrupted:
        print('\nNo hubo interrupt — el flujo terminó sin pasar por agency review.')
        return

    # ── Fase 2: resumir seleccionando todas las agencias con web o instagram ──
    selected_ids = [
        a['id'] for a in agencies_found
        if a.get('sitio_web') or a.get('instagram_handle')
    ]
    print(f'\nSeleccionando {len(selected_ids)} agencias con web o instagram...')
    print('=' * 60)

    async for ev in graph.astream_events(
        Command(resume=selected_ids), config, version='v2'
    ):
        if ev['event'] != 'on_custom_event':
            continue
        name = ev['name']
        data = ev['data']

        if name == 'progress':
            src = data.get('source', '')
            msg = data.get('message', '')
            count = data.get('count', 0)
            print(f'[{src}] {msg or count}')

        elif name == 'property_batch':
            src = data.get('source', '')
            count = data.get('count', 0)
            print(f'\n✓ {count} propiedades de {src}')
            props = data.get('properties', [])
            for p in props[:2]:
                print(f'  · {p.get("titulo", "")[:60]} | {p.get("precio")} {p.get("moneda")}')
            if len(props) > 2:
                print(f'  ... y {len(props)-2} más')

        elif name == 'done':
            total = data.get('total_count', 0)
            sources = data.get('sources', [])
            print(f'\n✓ DONE — {total} propiedades totales de {sources}')

        elif name == 'error':
            src = data.get('source', '')
            msg = data.get('message', '')
            print(f'\n✗ [{src}] {msg}')

        elif name == 'agent_message':
            print(f'\n→ {data.get("message")}')


if __name__ == '__main__':
    asyncio.run(run())
