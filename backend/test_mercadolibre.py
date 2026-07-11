"""
Script de diagnóstico: encuentra qué actor de Apify funciona para MercadoLibre inmuebles.
Prueba múltiples actores y reporta cuál acepta el input y devuelve resultados.

Uso:
    cd backend
    uv run python test_mercadolibre.py
"""
import asyncio
import json
import os
import httpx

APIFY_BASE = 'https://api.apify.com/v2'
SEARCH_URL = 'https://inmuebles.mercadolibre.com.ar/departamentos/venta/palermo/'

CANDIDATES = [
    ('crawlerbros~mercadolibre-scraper',
        {'searchQuery': 'departamento venta palermo', 'maxResults': 10}),
    ('devcake~mercadolibre-scraper',
        {'queries': ['departamento venta palermo'], 'maxItems': 48}),
]


def _read_token() -> str:
    token = os.environ.get('APIFY_API_TOKEN', '')
    if token:
        return token
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    try:
        with open(env_path) as f:
            for line in f:
                if line.strip().startswith('APIFY_API_TOKEN='):
                    return line.split('=', 1)[1].strip().strip('"\'')
    except FileNotFoundError:
        pass
    return ''


async def try_actor(client: httpx.AsyncClient, token: str, actor_id: str, input_data: dict) -> str:
    """Intenta iniciar un run y retorna: 'ok:N_items' | 'rejected:STATUS' | 'error:MSG'"""
    try:
        resp = await client.post(
            f'{APIFY_BASE}/acts/{actor_id}/runs',
            params={'token': token},
            json=input_data,
            timeout=15,
        )
        if resp.status_code == 404:
            return 'no_existe'
        if resp.status_code not in (200, 201):
            return f'rejected:{resp.status_code} — {resp.json().get("error", {}).get("message", "")}'

        run_id = resp.json()['data']['id']

        # Esperar máximo 90s
        for _ in range(18):
            await asyncio.sleep(5)
            st = await client.get(f'{APIFY_BASE}/actor-runs/{run_id}', params={'token': token})
            status = st.json()['data']['status']
            if status == 'SUCCEEDED':
                break
            if status in ('FAILED', 'ABORTED', 'TIMED-OUT'):
                return f'failed:{status}'

        items_resp = await client.get(
            f'{APIFY_BASE}/actor-runs/{run_id}/dataset/items',
            params={'token': token, 'format': 'json'},
        )
        items = items_resp.json()

        if items:
            print(f'\n  === Primer item de {actor_id} ===')
            print(json.dumps(items[0], indent=2, ensure_ascii=False)[:1500])

        return f'ok:{len(items)}_items'
    except Exception as e:
        return f'error:{e}'


async def run() -> None:
    token = _read_token()
    if not token:
        print('ERROR: APIFY_API_TOKEN no encontrado')
        return

    print(f'Testeando {len(CANDIDATES)} actores contra:\n  {SEARCH_URL}\n')

    async with httpx.AsyncClient(timeout=120) as client:
        for actor_id, input_data in CANDIDATES:
            print(f'[{actor_id}]... ', end='', flush=True)
            result = await try_actor(client, token, actor_id, input_data)
            print(result)

    print('\nListo.')




if __name__ == '__main__':
    asyncio.run(run())
