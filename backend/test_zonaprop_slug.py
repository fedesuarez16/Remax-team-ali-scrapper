"""
Verifica qué slug de categoría usa ZonaProp para "todos los tipos".

Prueba varias palabras candidatas contra una zona conocida (la-plata) y
reporta cuántos items devuelve cada una. La que trae > 0 es la válida.

Uso:
    cd backend
    uv run python test_zonaprop_slug.py
"""
import asyncio
import os

import httpx

APIFY_BASE = 'https://api.apify.com/v2'
ACTOR = 'crawlerbros~zonaprop-scraper'
POLL_INTERVAL = 5
TIMEOUT = 180

ZONA = 'la-plata'                       # zona que con seguridad existe
CANDIDATES = ['inmuebles', 'propiedades', 'casas']   # 'casas' = control conocido


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


async def run_actor(client: httpx.AsyncClient, token: str, input_data: dict) -> int:
    resp = await client.post(
        f'{APIFY_BASE}/acts/{ACTOR}/runs',
        params={'token': token}, json=input_data, timeout=15,
    )
    if resp.status_code not in (200, 201):
        print(f'    ERROR {resp.status_code}: {resp.text[:160]}')
        return -1

    run_id = resp.json()['data']['id']
    elapsed = 0
    while elapsed < TIMEOUT:
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        st = await client.get(f'{APIFY_BASE}/actor-runs/{run_id}', params={'token': token})
        status = st.json()['data']['status']
        print(f'    [{elapsed}s] {status}   ', end='\r')
        if status == 'SUCCEEDED':
            break
        if status in ('FAILED', 'ABORTED', 'TIMED-OUT'):
            print(f'\n    run {status}')
            return -1

    items = await client.get(
        f'{APIFY_BASE}/actor-runs/{run_id}/dataset/items',
        params={'token': token, 'format': 'json'},
    )
    return len(items.json())


async def run() -> None:
    token = _read_token()
    if not token:
        print('ERROR: APIFY_API_TOKEN no encontrado (env o backend/.env)')
        return

    print(f'Verificando slug de categoría ZonaProp en "{ZONA}"\n')
    results: dict[str, int] = {}
    async with httpx.AsyncClient(timeout=30) as client:
        for word in CANDIDATES:
            url = f'https://www.zonaprop.com.ar/{word}-venta-{ZONA}.html'
            print(f'  {word:<12} → {url}')
            count = await run_actor(client, token, {'searchUrl': url, 'maxResults': 5})
            results[word] = count
            print(f'    items: {count}\n')

    print('=' * 50)
    print('RESULTADO')
    for word, count in results.items():
        verdict = 'VÁLIDA ✅' if count > 0 else ('error/bloqueo' if count < 0 else 'vacía ❌')
        print(f'  {word:<12} {count:>4} items  → {verdict}')


if __name__ == '__main__':
    asyncio.run(run())
