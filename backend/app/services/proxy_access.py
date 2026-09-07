"""Diagnose Apify CONNECT failures without mistaking them for portal blocks."""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse

import httpx


class ProxyAccessError(RuntimeError):
    """The proxy account cannot currently accept requests."""


async def check_apify_proxy_limit(proxy_url: str | None) -> None:
    """Raise on a confirmed account limit; unknown failures keep normal handling.

    Apify rejects HTTPS CONNECT with a bare 403 when the monthly account limit
    is exhausted. Its HTTP status endpoint exposes the actual reason. Query it
    only after a ProxyError, through the SAME proxy credentials that failed.
    Never put credentials or an untrusted provider message in the public error.
    """
    if not proxy_url or urlparse(proxy_url).hostname != 'proxy.apify.com':
        return
    try:
        async with asyncio.timeout(2):
            async with httpx.AsyncClient(
                proxy=proxy_url, trust_env=False, timeout=2,
            ) as client:
                response = await client.get('http://proxy.apify.com/?format=json')
            response.raise_for_status()
            status = response.json()
    except (httpx.HTTPError, ValueError, TimeoutError):
        return
    if (
        isinstance(status, dict)
        and status.get('connected') is False
        and 'monthly usage hard limit exceeded' in str(status.get('connectionError', '')).lower()
    ):
        raise ProxyAccessError(
            'Apify bloqueó el proxy porque se alcanzó el límite mensual de consumo. '
            'Revisá el límite de gasto en Apify para volver a generar fichas.'
        )
