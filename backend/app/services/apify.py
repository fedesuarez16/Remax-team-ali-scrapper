from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod
from typing import Awaitable, Callable

from app.models.property import RawProperty, ScrapingFilters

ProgressCb = Callable[[str, str, int], Awaitable[None]]  # (source, status, count)

SOURCES = ('zonaprop', 'mercadolibre', 'googlemaps')

_BARRIOS = ['Palermo', 'Belgrano', 'Recoleta', 'Caballito', 'Villa Crespo',
            'Núñez', 'Colegiales', 'Almagro', 'San Telmo', 'Puerto Madero']
_CALLES = ['Av. Santa Fe', 'Thames', 'Gorriti', 'Av. Cabildo', 'Honduras',
           'Av. Córdoba', 'Juramento', 'Malabia', 'Av. del Libertador', 'Gurruchaga']
_AMENITIES = ['pileta', 'gimnasio', 'sum', 'cochera', 'parrilla', 'seguridad 24hs',
              'laundry', 'balcón', 'terraza']


class BaseApifyService(ABC):
    @abstractmethod
    async def scrape_source(
        self,
        source: str,
        filters: ScrapingFilters,
        on_progress: ProgressCb,
    ) -> list[RawProperty]:
        ...


class MockApifyService(BaseApifyService):
    DELAYS = {'zonaprop': 1.2, 'mercadolibre': 0.9, 'googlemaps': 1.5}
    COUNTS = {'zonaprop': (7, 10), 'mercadolibre': (5, 8), 'googlemaps': (5, 7)}

    async def scrape_source(self, source: str, filters: ScrapingFilters, on_progress: ProgressCb) -> list[RawProperty]:
        delay = self.DELAYS.get(source, 1.0)
        await on_progress(source, 'running', 0)
        await asyncio.sleep(delay * 0.4)
        total = random.randint(*self.COUNTS.get(source, (5, 8)))
        # emit a partial mid-progress tick
        await on_progress(source, 'running', total // 2)
        await asyncio.sleep(delay * 0.6)
        props = [self._fake(source, filters) for _ in range(total)]
        await on_progress(source, 'done', total)
        return props

    def _fake(self, source: str, f: ScrapingFilters) -> RawProperty:
        zona = f.zona or random.choice(_BARRIOS)
        calle = random.choice(_CALLES)
        altura = random.randint(100, 4500)
        op = f.tipo_operacion or random.choice(['venta', 'alquiler'])
        tipo = f.tipo_propiedad or random.choice(['departamento', 'casa', 'ph'])
        amb = random.randint(1, 5)
        m2 = round(random.uniform(28, 180), 1)
        if op == 'venta':
            precio = round(random.uniform(70_000, 480_000), 0)
        else:
            precio = round(random.uniform(350, 2200), 0)
        return RawProperty(
            fuente=source,  # type: ignore[arg-type]
            titulo=f'{(tipo or "Propiedad").capitalize()} {amb} amb en {zona}',
            direccion=f'{calle} {altura}, {zona}, CABA',
            precio=precio,
            moneda='USD',
            tipo_operacion=op,  # type: ignore[arg-type]
            tipo_propiedad=tipo,  # type: ignore[arg-type]
            ambientes=amb,
            m2_total=m2,
            m2_cubiertos=round(m2 * random.uniform(0.7, 1.0), 1),
            antiguedad=random.randint(0, 40),
            amenities=random.sample(_AMENITIES, k=random.randint(0, 4)),
            imagenes=[],  # mock has no real images -> frontend shows placeholder
            url_origen=f'https://{source}.com.ar/propiedad/{random.randint(10**6, 10**7)}',
        )


def get_apify_service() -> BaseApifyService:
    from app.core.config import settings
    if settings.APIFY_USE_MOCK:
        return MockApifyService()
    raise NotImplementedError('Real ApifyService lands in a later EP')
