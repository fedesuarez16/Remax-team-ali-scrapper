"""Una búsqueda tiene un techo de gasto en Apify.

El track de inmobiliarias arranca UN run de `googlemaps` y después abre un run
de `instagram` POR CADA inmobiliaria con handle, en paralelo. Ambos actores se
pagan por resultado y ambos corren con su cap en `0` (= sin tope), así que una
zona con muchas inmobiliarias no tenía ningún freno: el gasto lo fijaba el
tamaño de la zona, no una decisión nuestra.

El ledger que ya existía (`use_cost_ledger` / `record_run_cost`) sabía cuánto
se llevaba gastado pero sólo lo REPORTABA al cerrar el job — o sea, cuando ya
no servía para nada. Este tope lo convierte en algo que decide: antes de
arrancar cada run se consulta el acumulado y, si ya se alcanzó el techo, ese
run no se arranca.

Es un tope BLANDO a propósito: un run que ya está corriendo termina. Frenar en
seco requeriría abortarlo en Apify y quedarse con el dataset parcial; acá lo
que se corta es el gasto NUEVO. La consecuencia es que el total puede pasarse
del techo por lo que cueste el run en vuelo.

Y lo que NO hace: fallar la búsqueda. El nodo que pidió el run se queda con lo
que ya tenía y sigue — por eso `ApifyBudgetExceeded` es una excepción propia y
no un `RuntimeError` genérico, que los nodos ya tratan como error rojo.
"""
from typing import Any

import pytest

from app.services.apify import (
    ApifyBudgetExceeded,
    ApifyService,
    use_cost_ledger,
)


@pytest.fixture()
def service() -> ApifyService:
    return ApifyService(api_token='apify_api_TEST')


class _Resp:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


@pytest.fixture()
def transport(service: ApifyService, monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Fake de Apify que registra CADA request, para poder afirmar que un run
    rechazado no llegó a tocar la API — el tope tiene que evitar el gasto, no
    descartarlo después de pagarlo."""
    calls: dict[str, list[str]] = {'post': [], 'get': []}

    async def fake_post(url: str, **kw: Any) -> _Resp:
        calls['post'].append(url)
        return _Resp({'data': {'id': 'run-1'}})

    async def fake_get(url: str, **kw: Any) -> _Resp:
        calls['get'].append(url)
        if 'dataset' in url:
            return _Resp([{'placeId': 'p1'}])
        return _Resp({'data': {'status': 'SUCCEEDED', 'usageTotalUsd': 0.25}})

    monkeypatch.setattr(service._client, 'post', fake_post)
    monkeypatch.setattr(service._client, 'get', fake_get)
    monkeypatch.setattr('app.services.apify._POLL_INTERVAL', 0.0)
    return calls


@pytest.fixture()
def cap_one_usd(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, 'APIFY_MAX_USD_PER_SEARCH', 1.0)


async def test_un_run_arranca_mientras_haya_presupuesto(
    service: ApifyService, transport: dict[str, list[str]], cap_one_usd: None,
) -> None:
    ledger: dict[str, dict[str, Any]] = {'googlemaps': {'usd': 0.4, 'runs': 1}}

    with use_cost_ledger(ledger):
        items = await service._run_actor('instagram', 'actor~x', {})

    assert items == [{'placeId': 'p1'}]
    assert transport['post'], 'el run tenía presupuesto y no arrancó'


async def test_alcanzado_el_techo_el_run_no_arranca(
    service: ApifyService, transport: dict[str, list[str]], cap_one_usd: None,
) -> None:
    ledger: dict[str, dict[str, Any]] = {'googlemaps': {'usd': 1.0, 'runs': 1}}

    with use_cost_ledger(ledger), pytest.raises(ApifyBudgetExceeded):
        await service._run_actor('instagram', 'actor~x', {})

    assert transport['post'] == [], 'se arrancó un run después de agotar el tope'


async def test_el_run_rechazado_no_ensucia_el_ledger(
    service: ApifyService, transport: dict[str, list[str]], cap_one_usd: None,
) -> None:
    """No arrancó, no gastó: ni un run de más ni un centavo de más en el tally
    que después se escribe en la fila del job."""
    ledger: dict[str, dict[str, Any]] = {'googlemaps': {'usd': 1.2, 'runs': 3}}

    with use_cost_ledger(ledger), pytest.raises(ApifyBudgetExceeded):
        await service._run_actor('instagram', 'actor~x', {})

    assert ledger == {'googlemaps': {'usd': 1.2, 'runs': 3}}


async def test_el_mensaje_dice_cuanto_y_contra_que_tope(
    service: ApifyService, transport: dict[str, list[str]], cap_one_usd: None,
) -> None:
    """El aviso llega al usuario por SSE. "Presupuesto agotado" sin números no
    le dice si subir el tope o achicar la zona."""
    ledger: dict[str, dict[str, Any]] = {'googlemaps': {'usd': 1.05, 'runs': 2}}

    with use_cost_ledger(ledger), pytest.raises(ApifyBudgetExceeded) as exc:
        await service._run_actor('instagram', 'actor~x', {})

    assert '1.05' in str(exc.value)
    assert '1.0' in str(exc.value)


async def test_un_tope_en_cero_significa_sin_tope(
    service: ApifyService, transport: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Misma convención que el resto de los knobs de este archivo
    (`MAX_WEBSITE_URLS`, `GOOGLEMAPS_MAX_PLACES`): `0` = sin tope."""
    from app.core.config import settings
    monkeypatch.setattr(settings, 'APIFY_MAX_USD_PER_SEARCH', 0.0)
    ledger: dict[str, dict[str, Any]] = {'googlemaps': {'usd': 99.0, 'runs': 40}}

    with use_cost_ledger(ledger):
        await service._run_actor('instagram', 'actor~x', {})

    assert transport['post'], 'un tope de 0 frenó un run'


async def test_fuera_de_una_busqueda_no_hay_tope(
    service: ApifyService, transport: dict[str, list[str]], cap_one_usd: None,
) -> None:
    """Sin ledger instalado no estamos en una búsqueda: son los caminos de
    ficha e importer, que corren sueltos y no tienen contra qué acumular.
    `record_run_cost` ya es no-op ahí; el tope tiene que serlo también, o un
    ledger inexistente pasaría a leerse como presupuesto cero."""
    await service._run_actor('instagram', 'actor~x', {})

    assert transport['post'], 'el tope frenó un run fuera de una búsqueda'


async def test_el_tope_mira_el_total_de_todas_las_fuentes(
    service: ApifyService, transport: dict[str, list[str]], cap_one_usd: None,
) -> None:
    """El presupuesto es de la BÚSQUEDA, no de cada actor: googlemaps e
    instagram comen del mismo dólar."""
    ledger: dict[str, dict[str, Any]] = {
        'googlemaps': {'usd': 0.6, 'runs': 1},
        'instagram': {'usd': 0.5, 'runs': 4},
    }

    with use_cost_ledger(ledger), pytest.raises(ApifyBudgetExceeded):
        await service._run_actor('instagram', 'actor~x', {})

    assert transport['post'] == []
