"""
Test end-to-end de ZonaProp + Google Maps con Apify real.

Uso:
    cd backend
    uv run python test_zonaprop_gmaps.py
"""
import asyncio
import json
import os
import httpx

APIFY_BASE = 'https://api.apify.com/v2'
POLL_INTERVAL = 5
TIMEOUT = 300

ZONA = 'citybell'


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


async def run_actor(client: httpx.AsyncClient, token: str, actor_id: str, input_data: dict) -> list[dict]:
    resp = await client.post(
        f'{APIFY_BASE}/acts/{actor_id}/runs',
        params={'token': token},
        json=input_data,
        timeout=15,
    )
    if resp.status_code not in (200, 201):
        print(f'  ERROR {resp.status_code}: {resp.text[:200]}')
        return []

    run_id = resp.json()['data']['id']
    print(f'  Run ID: {run_id}')

    elapsed = 0
    while elapsed < TIMEOUT:
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        st = await client.get(f'{APIFY_BASE}/actor-runs/{run_id}', params={'token': token})
        status = st.json()['data']['status']
        print(f'  [{elapsed}s] {status}', end='\r')
        if status == 'SUCCEEDED':
            break
        if status in ('FAILED', 'ABORTED', 'TIMED-OUT'):
            print(f'\n  FAILED: {status}')
            return []

    print()
    items_resp = await client.get(
        f'{APIFY_BASE}/actor-runs/{run_id}/dataset/items',
        params={'token': token, 'format': 'json'},
    )
    return items_resp.json()


async def test_zonaprop(client: httpx.AsyncClient, token: str) -> None:
    print('\n' + '='*60)
    print('ZONAPROP')
    print('='*60)

    slugs = ['city-bell', 'la-plata']
    for slug in slugs:
        url = f'https://www.zonaprop.com.ar/departamentos-venta-{slug}.html'
        print(f'\nProbando: {url}')
        items = await run_actor(client, token, 'crawlerbros~zonaprop-scraper', {
            'searchUrl': url,
            'maxResults': 10,
        })
        print(f'Items devueltos: {len(items)}')
        if items:
            print('Primer item:')
            print(json.dumps(items[0], indent=2, ensure_ascii=False))
            print('Keys:', sorted(items[0].keys()))
            break
        else:
            print('Sin resultados.')


async def test_googlemaps(client: httpx.AsyncClient, token: str) -> None:
    print('\n' + '='*60)
    print('GOOGLE MAPS — Inmobiliarias')
    print('='*60)

    input_data = {
        'searchStringsArray': [
            f'inmobiliarias en {ZONA}',
            f'propiedades en venta {ZONA}',
            f'bienes raices {ZONA}',
        ],
        'maxCrawledPlacesPerSearch': 10,
        'language': 'es',
        'countryCode': 'ar',
        'includeWebResults': False,
    }
    print(f'Búsquedas: {input_data["searchStringsArray"]}')

    items = await run_actor(client, token, 'compass~crawler-google-places', input_data)

    print(f'Items devueltos: {len(items)}')
    if items:
        print('\nPrimer item:')
        print(json.dumps(items[0], indent=2, ensure_ascii=False))
        print('\nKeys disponibles:', sorted(items[0].keys()))

        # Mostrar resumen de los primeros 5
        print('\nResumen de inmobiliarias encontradas:')
        for i, item in enumerate(items[:5]):
            name = item.get('title') or item.get('name', 'Sin nombre')
            website = item.get('website', 'Sin web')
            rating = item.get('totalScore') or item.get('rating', '?')
            print(f'  {i+1}. {name} | web: {website} | rating: {rating}')


async def run() -> None:
    token = _read_token()
    if not token:
        print('ERROR: APIFY_API_TOKEN no encontrado')
        return

    async with httpx.AsyncClient(timeout=30) as client:
        await test_zonaprop(client, token)
        await test_googlemaps(client, token)

    print('\n' + '='*60)
    print('FIN')


if __name__ == '__main__':
    asyncio.run(run())
