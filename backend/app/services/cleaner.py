"""BOT LIMPIADOR — mantiene la base viva.

Entra a cada `url_origen` de las propiedades scrapeadas, verifica si el aviso
sigue publicado y borra la propiedad ENTERA cuando el aviso murió: se vendió,
lo bajaron del portal, el link quedó roto o la ficha ya no existe.

## La regla que ordena todo el módulo

El veredicto es TERNARIO, nunca booleano:

- ``alive``   — el aviso sigue publicado.
- ``dead``    — probamos que no está: 404/410, la página dice "este aviso ya no
                está publicado" / "publicación finalizada" / "fue vendida", o el
                portal nos rebotó a su home.
- ``unknown`` — NO pudimos saber: timeout, 429, 403, 5xx, error de red.

Sólo ``dead`` borra. ``unknown`` no toca nada. Esa distinción es la única cosa
que separa "limpiar la base" de "vaciar la base el día que un portal nos
bloquea": los portales rate-limitean y devuelven 403 a los bots todo el tiempo,
y un booleano tratando ese 403 como "no existe" borraría todo en una corrida.

## Programación automática

La cadencia ("cada 7 días", "cada 30 días", "cada X días") vive en la tabla
`cleanup_schedule`, no en memoria: un restart del backend no pierde la
configuración ni vuelve a disparar una limpieza que ya corrió. El loop en
proceso sólo se pregunta, cada tick, si `is_due`.
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Literal
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

Verdict = Literal['alive', 'dead', 'unknown']


@dataclass(frozen=True)
class CheckResult:
    """Veredicto de una URL más el motivo legible que se guarda en la auditoría."""
    verdict: Verdict
    reason: str


Checker = Callable[..., Awaitable[CheckResult]]

# ── verificación de una URL ──────────────────────────────────────────────────

# Sólo estos códigos prueban que el aviso no existe. 403 (bot-block), 429
# (throttle), 401 y 5xx hablan del portal, no de la propiedad → `unknown`.
_DEAD_STATUS = {404, 410}

# Frases que SÓLO aparecen en una ficha muerta (varios portales sirven el
# "no existe" con status 200, así que el status no alcanza). Guardadas ya
# normalizadas: minúsculas y sin acentos.
#
# Criterio para agregar una: tiene que ser una frase entera imposible de
# encontrar en un aviso vivo. Por eso NO está "propiedad vendida" suelta — la
# web de una inmobiliaria que se jacta de sus "propiedades vendidas" es un
# aviso perfectamente vivo, y ese marcador borraría la base.
_DEAD_MARKERS: tuple[str, ...] = (
    # ZonaProp
    'este aviso ya no esta publicado',
    'el aviso que buscas ya no esta disponible',
    'esta propiedad ya no esta publicada',
    'este aviso no esta disponible',
    'aviso no disponible',
    'aviso vencido',
    # Argenprop
    'la propiedad que buscas ya no esta disponible',
    'la propiedad que buscas no esta disponible',
    'esta propiedad no esta disponible',
    'la propiedad no se encuentra disponible',
    'propiedad no encontrada',
    # MercadoLibre
    'publicacion finalizada',
    'publicacion pausada',
    'esta publicacion fue finalizada',
    'esta publicacion no esta disponible',
    # Vendidas
    'esta propiedad fue vendida',
    'esta propiedad ya fue vendida',
    'propiedad ya vendida',
    # RE/MAX y sitios en inglés
    'listing not found',
    'property not found',
    'no longer available',
    # Soft-404 (404 servido con status 200)
    'pagina no encontrada',
    'page not found',
    'error 404',
)

_WHITESPACE_RE = re.compile(r'\s+')
_REQUEST_TIMEOUT = 20
_USER_AGENT = 'Mozilla/5.0 (compatible; PropSearchCleanerBot/1.0)'


def _normalize(text: str) -> str:
    """Minúsculas, sin acentos, espacios colapsados — para comparar marcadores."""
    plain = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode().lower()
    return _WHITESPACE_RE.sub(' ', plain)


def _visible_text(html: str) -> str:
    """Texto que el usuario realmente ve.

    Descartar `<script>` no es cosmético: los portales embeben TODOS sus
    strings de i18n en el bundle de JS, incluido el "este aviso ya no está
    publicado". Buscar el marcador en el HTML crudo daría positivo en cada
    ficha viva del portal y borraría la base entera.
    """
    from bs4 import BeautifulSoup  # type: ignore[import-untyped]

    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'noscript', 'template']):
        tag.decompose()
    return soup.get_text(separator=' ')


def _match_dead_marker(html: str) -> str | None:
    """Devuelve el marcador de aviso muerto encontrado en la página, o None."""
    if not html:
        return None
    text = _normalize(_visible_text(html))
    return next((marker for marker in _DEAD_MARKERS if marker in text), None)


def _is_root(path: str) -> bool:
    return path.strip('/') == ''


def _redirected_away(original: str, final: str) -> bool:
    """True si el portal nos rebotó de la ficha a su home.

    Es cómo ZonaProp y Argenprop resuelven un aviso dado de baja. Un redirect
    a OTRA ficha (cambio de slug, URL canónica) no cuenta: sigue habiendo aviso.
    """
    if not final or final == original:
        return False
    return _is_root(urlsplit(final).path) and not _is_root(urlsplit(original).path)


async def check_url(url: str, *, client: Any) -> CheckResult:
    """Visita una ficha y dictamina si sigue publicada.

    Nunca levanta: cualquier error se traduce a ``unknown``, que no borra.
    """
    target = (url or '').strip()
    if not target.startswith(('http://', 'https://')):
        return CheckResult('unknown', 'sin URL válida')

    try:
        resp = await client.get(target)
    except Exception as exc:
        # Timeout, DNS, conexión cortada: el aviso puede seguir vivo.
        return CheckResult('unknown', f'sin respuesta ({type(exc).__name__})')

    status = int(getattr(resp, 'status_code', 0) or 0)
    if status in _DEAD_STATUS:
        return CheckResult('dead', f'HTTP {status} — el aviso ya no existe')
    if status < 200 or status >= 400:
        # 403/429/5xx: el portal nos bloqueó o se cayó. No sabemos nada.
        return CheckResult('unknown', f'HTTP {status} — no se pudo verificar')

    final_url = str(getattr(resp, 'url', '') or '')
    if _redirected_away(target, final_url):
        return CheckResult('dead', f'el portal redirigió a la home ({final_url})')

    marker = _match_dead_marker(getattr(resp, 'text', '') or '')
    if marker:
        return CheckResult('dead', f'la página dice "{marker}"')

    return CheckResult('alive', f'HTTP {status} — publicado')


# ── estado de la corrida ─────────────────────────────────────────────────────

DEFAULT_LIMIT = 500
DEFAULT_CONCURRENCY = 5
_PROPERTY_COLUMNS = 'id,titulo,direccion,url_origen,fuente,ultima_verificacion'

_lock = asyncio.Lock()


def _blank_state() -> dict[str, Any]:
    return {
        'running': False,
        'origen': None,
        'dry_run': False,
        'total': 0,
        'checked': 0,
        'alive': 0,
        'dead': 0,
        'unknown': 0,
        'deleted': 0,
        'error': None,
        'started_at': None,
        'finished_at': None,
    }


_state: dict[str, Any] = _blank_state()


def cleanup_state() -> dict[str, Any]:
    """Copia del estado de la limpieza en curso (o de la última)."""
    return dict(_state)


def reset_state() -> None:
    """Vuelve el estado a cero — usado por los tests entre casos."""
    _state.clear()
    _state.update(_blank_state())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── corrida completa ─────────────────────────────────────────────────────────


async def _select_pending(sb: Any, limit: int) -> list[dict[str, Any]]:
    """Propiedades a revisar: primero las que nunca se verificaron, después las
    verificadas hace más tiempo.

    Se hace en dos queries en vez de un ``order(nulls first)`` porque el orden
    de nulos de PostgREST depende de la versión del cliente, y acá el orden ES
    la garantía de que una base grande termina cubriéndose entera a lo largo de
    varias corridas en vez de re-verificar siempre las mismas filas.
    """
    rows: list[dict[str, Any]] = []

    never = await (
        sb.table('properties')
        .select(_PROPERTY_COLUMNS)
        .not_.is_('url_origen', 'null')
        .is_('ultima_verificacion', 'null')
        .limit(limit)
        .execute()
    )
    rows.extend(never.data or [])

    remaining = limit - len(rows)
    if remaining > 0:
        stale = await (
            sb.table('properties')
            .select(_PROPERTY_COLUMNS)
            .not_.is_('url_origen', 'null')
            .not_.is_('ultima_verificacion', 'null')
            .order('ultima_verificacion', desc=False)
            .limit(remaining)
            .execute()
        )
        rows.extend(stale.data or [])

    return rows


async def _process_row(
    sb: Any,
    row: dict[str, Any],
    *,
    check: Checker,
    client: Any,
    sem: asyncio.Semaphore,
    dry_run: bool,
    deleted: list[dict[str, Any]],
) -> None:
    url = (row.get('url_origen') or '').strip()

    async with sem:
        try:
            result = await check(url, client=client)
        except Exception as exc:
            # Un checker que explota es exactamente el caso "no sabemos":
            # jamás puede escalar a borrado.
            result = CheckResult('unknown', f'error al verificar ({type(exc).__name__})')

    _state['checked'] += 1
    _state[result.verdict] += 1

    if result.verdict == 'dead':
        snapshot = {
            'id': row.get('id'),
            'titulo': row.get('titulo'),
            'direccion': row.get('direccion'),
            'fuente': row.get('fuente'),
            'url_origen': url,
            'motivo': result.reason,
        }
        if dry_run:
            # En simulación igual se registra: el sentido de la simulación es
            # ver QUÉ se borraría y por qué, no sólo cuántas.
            deleted.append(snapshot)
            return
        try:
            await sb.table('properties').delete().eq('id', row['id']).execute()
        except Exception as exc:
            logger.warning('cleaner: no se pudo borrar %s: %s', row.get('id'), exc)
            return
        _state['deleted'] += 1
        deleted.append(snapshot)
        return

    if dry_run:
        return

    # Viva o indeterminada: se sella la verificación para que la próxima
    # corrida priorice las filas que hace más que no miramos.
    try:
        await (
            sb.table('properties')
            .update({'ultima_verificacion': _now().isoformat()})
            .eq('id', row['id'])
            .execute()
        )
    except Exception as exc:
        logger.warning('cleaner: no se pudo sellar %s: %s', row.get('id'), exc)


async def _record_run(
    sb: Any,
    *,
    origen: str,
    dry_run: bool,
    started_at: datetime,
    finished_at: datetime,
    deleted: list[dict[str, Any]],
    error: str | None,
) -> None:
    """Deja la corrida en `cleanup_runs`, con la foto de cada propiedad borrada.

    El snapshot es la red de contención: si el bot se equivoca, quedó registrado
    QUÉ borró y POR QUÉ en vez de desaparecer sin rastro. Best-effort — que
    falle la auditoría no puede tumbar la limpieza.
    """
    payload = {
        'origen': origen,
        'dry_run': dry_run,
        'revisadas': _state['checked'],
        'activas': _state['alive'],
        'caidas': _state['dead'],
        'indeterminadas': _state['unknown'],
        'eliminadas_count': _state['deleted'],
        'eliminadas': deleted,
        'error': error,
        'started_at': started_at.isoformat(),
        'finished_at': finished_at.isoformat(),
    }
    try:
        await sb.table('cleanup_runs').insert(payload).execute()
    except Exception as exc:
        logger.warning('cleaner: no se pudo registrar la corrida: %s', exc)


async def run_cleanup(
    sb: Any,
    *,
    limit: int = DEFAULT_LIMIT,
    dry_run: bool = False,
    checker: Checker | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    origen: str = 'manual',
) -> dict[str, Any]:
    """Revisa hasta ``limit`` propiedades y borra las que ya no están publicadas.

    ``dry_run=True`` reporta qué borraría sin tocar nada — la forma sana de
    estrenar el bot contra una base real.

    Protegido por un lock de módulo: una segunda llamada mientras hay una
    corrida en vuelo es un no-op que devuelve el estado en curso, igual que
    `geocode.run_backfill`.
    """
    if sb is None:
        return {**cleanup_state(), 'skipped': True}
    if _lock.locked():
        return {**cleanup_state(), 'skipped': True}

    async with _lock:
        check: Checker = checker or check_url
        started_at = _now()
        reset_state()
        _state.update({
            'running': True, 'origen': origen, 'dry_run': dry_run,
            'started_at': started_at.isoformat(),
        })
        deleted: list[dict[str, Any]] = []
        error: str | None = None

        try:
            rows = await _select_pending(sb, limit)
            _state['total'] = len(rows)
            sem = asyncio.Semaphore(max(1, concurrency))
            client = httpx.AsyncClient(
                headers={'User-Agent': _USER_AGENT, 'Accept-Language': 'es-AR,es;q=0.9'},
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            )
            try:
                await asyncio.gather(*(
                    _process_row(
                        sb, row, check=check, client=client, sem=sem,
                        dry_run=dry_run, deleted=deleted,
                    )
                    for row in rows
                ))
            finally:
                await client.aclose()
        except Exception as exc:
            error = str(exc)
            logger.warning('cleaner: corrida abortada: %s', exc)
        finally:
            finished_at = _now()
            _state.update({
                'running': False, 'error': error, 'finished_at': finished_at.isoformat(),
            })

        await _record_run(
            sb, origen=origen, dry_run=dry_run, started_at=started_at,
            finished_at=finished_at, deleted=deleted, error=error,
        )
        return {**cleanup_state(), 'skipped': False, 'eliminadas': deleted}


# ── verificación de una lista pegada a mano ──────────────────────────────────

# Tope por pedido: la verificación corre DENTRO del request HTTP (a diferencia
# de la limpieza de base, que es fire-and-forget), así que la lista tiene que
# terminar en un tiempo razonable.
MAX_LINKS = 50
_LINKS_CONCURRENCY = 10

# "www.zonaprop.com.ar/ficha-123.html" — pegado desde WhatsApp, sin esquema.
_BARE_DOMAIN_RE = re.compile(r'^[\w-]+(\.[\w-]+)+(/.*)?$')


def _normalize_link(raw: str) -> str | None:
    """Devuelve la URL lista para pedir, o None si eso no es un link."""
    link = raw.strip()
    if not link:
        return None
    if link.startswith(('http://', 'https://')):
        return link
    if _BARE_DOMAIN_RE.match(link):
        return f'https://{link}'
    return None


async def check_links(
    urls: list[str], *, checker: Checker | None = None, concurrency: int = _LINKS_CONCURRENCY,
) -> dict[str, Any]:
    """Verifica una lista de links pegada a mano y la parte en dos.

    Pensado para "estos 15 links se los mandé a un cliente hace un mes, ¿cuáles
    siguen vivos?". NO toca la base: no borra, no escribe, ni siquiera necesita
    Supabase.

    Devuelve ``activos`` y ``rotos`` (las dos listas que importan) más
    ``sin_definir``: los que el portal no dejó verificar (403, 429, timeout).
    Esos NO van a ``rotos`` a propósito — un bloqueo del portal no vuelve roto
    al link, y mandarlos a la basura le haría descartar links vivos.

    Un texto que ni siquiera es una URL sí cuenta como roto: no hay nada que
    verificar y tampoco sirve para reenviar.
    """
    seen: dict[str, str | None] = {}
    for raw in urls:
        candidate = (raw or '').strip()
        if not candidate:
            continue
        seen.setdefault(candidate, _normalize_link(candidate))

    if not seen:
        raise ValueError('Pegá al menos un link')
    if len(seen) > MAX_LINKS:
        raise ValueError(f'Máximo {MAX_LINKS} links por vez')

    check: Checker = checker or check_url
    sem = asyncio.Semaphore(max(1, concurrency))
    results: dict[str, CheckResult] = {}

    async def verify(original: str, normalized: str, client: Any) -> None:
        async with sem:
            try:
                results[original] = await check(normalized, client=client)
            except Exception as exc:
                results[original] = CheckResult(
                    'unknown', f'error al verificar ({type(exc).__name__})',
                )

    client = httpx.AsyncClient(
        headers={'User-Agent': _USER_AGENT, 'Accept-Language': 'es-AR,es;q=0.9'},
        timeout=_REQUEST_TIMEOUT,
        follow_redirects=True,
    )
    try:
        await asyncio.gather(*(
            verify(original, normalized, client)
            for original, normalized in seen.items()
            if normalized is not None
        ))
    finally:
        await client.aclose()

    buckets: dict[str, list[dict[str, str]]] = {'activos': [], 'rotos': [], 'sin_definir': []}
    for original, normalized in seen.items():
        if normalized is None:
            buckets['rotos'].append({'url': original, 'motivo': 'no es un link válido'})
            continue
        result = results[original]
        bucket = {'alive': 'activos', 'dead': 'rotos', 'unknown': 'sin_definir'}[result.verdict]
        buckets[bucket].append({'url': normalized, 'motivo': result.reason})

    return {**buckets, 'total': len(seen)}


# ── programación automática ──────────────────────────────────────────────────

SCHEDULE_ID = 'default'
DEFAULT_INTERVAL_DAYS = 7
MIN_INTERVAL_DAYS = 1
MAX_INTERVAL_DAYS = 365
DEFAULT_POLL_SECONDS = 300


def _default_schedule() -> dict[str, Any]:
    # `enabled=False`: el borrado automático se opta explícitamente, no se hereda
    # de un default.
    return {
        'enabled': False,
        'interval_days': DEFAULT_INTERVAL_DAYS,
        'last_run_at': None,
        'next_run_at': None,
    }


def _parse_dt(value: Any) -> datetime | None:
    """Parsea un timestamp de Postgres tolerando naive y basura."""
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except ValueError:
            return None
    # Un timestamp naive comparado contra un `now` aware levanta TypeError y
    # dejaría el scheduler colgado para siempre; se asume UTC.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _interval_of(schedule: dict[str, Any]) -> int:
    try:
        return int(schedule.get('interval_days') or DEFAULT_INTERVAL_DAYS)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_DAYS


def is_due(schedule: dict[str, Any], *, now: datetime | None = None) -> bool:
    """¿Toca limpiar? Un `last_run_at` ilegible cuenta como "nunca corrió":
    mejor una limpieza de más que un scheduler trabado para siempre."""
    if not schedule.get('enabled'):
        return False
    last = _parse_dt(schedule.get('last_run_at'))
    if last is None:
        return True
    moment = now or _now()
    return moment >= last + timedelta(days=_interval_of(schedule))


def next_run_at(schedule: dict[str, Any]) -> str | None:
    """Cuándo corre la próxima; None si está apagada o si nunca corrió."""
    if not schedule.get('enabled'):
        return None
    last = _parse_dt(schedule.get('last_run_at'))
    if last is None:
        return None
    return (last + timedelta(days=_interval_of(schedule))).isoformat()


def _validate_interval(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('El intervalo tiene que ser un número de días')
    if isinstance(value, float) and not value.is_integer():
        raise ValueError('El intervalo tiene que ser un número entero de días')
    days = int(value)
    if not MIN_INTERVAL_DAYS <= days <= MAX_INTERVAL_DAYS:
        raise ValueError(
            f'El intervalo tiene que estar entre {MIN_INTERVAL_DAYS} y {MAX_INTERVAL_DAYS} días'
        )
    return days


async def read_schedule(sb: Any) -> dict[str, Any]:
    """Cadencia configurada. Nunca levanta: sin fila o sin tabla → defaults."""
    if sb is None:
        return _default_schedule()
    try:
        res = await (
            sb.table('cleanup_schedule').select('*').eq('id', SCHEDULE_ID).limit(1).execute()
        )
    except Exception as exc:
        logger.warning('cleaner: no se pudo leer la programación: %s', exc)
        return _default_schedule()

    rows = res.data or []
    if not rows:
        return _default_schedule()

    row = rows[0]
    schedule: dict[str, Any] = {
        'enabled': bool(row.get('enabled')),
        'interval_days': _interval_of(row),
        'last_run_at': row.get('last_run_at'),
    }
    return {**schedule, 'next_run_at': next_run_at(schedule)}


async def save_schedule(sb: Any, *, enabled: bool, interval_days: Any) -> dict[str, Any]:
    """Guarda la cadencia. Levanta ValueError si el intervalo no sirve."""
    days = _validate_interval(interval_days)
    if sb is None:
        raise RuntimeError('Supabase no configurado')

    patch = {
        'enabled': bool(enabled),
        'interval_days': days,
        'updated_at': _now().isoformat(),
    }
    # SELECT → UPDATE/INSERT en vez de upsert: mantiene `last_run_at` intacto y
    # no depende de que el cliente exponga on_conflict.
    existing = await (
        sb.table('cleanup_schedule').select('id').eq('id', SCHEDULE_ID).limit(1).execute()
    )
    if existing.data:
        await sb.table('cleanup_schedule').update(patch).eq('id', SCHEDULE_ID).execute()
    else:
        await sb.table('cleanup_schedule').insert({
            'id': SCHEDULE_ID, 'last_run_at': None, **patch,
        }).execute()
    return await read_schedule(sb)


async def _mark_ran(sb: Any, *, when: datetime | None = None) -> None:
    stamp = (when or _now()).isoformat()
    try:
        existing = await (
            sb.table('cleanup_schedule').select('id').eq('id', SCHEDULE_ID).limit(1).execute()
        )
        if existing.data:
            await (
                sb.table('cleanup_schedule')
                .update({'last_run_at': stamp})
                .eq('id', SCHEDULE_ID)
                .execute()
            )
        else:
            await sb.table('cleanup_schedule').insert({
                'id': SCHEDULE_ID, 'enabled': True,
                'interval_days': DEFAULT_INTERVAL_DAYS, 'last_run_at': stamp,
            }).execute()
    except Exception as exc:
        logger.warning('cleaner: no se pudo sellar la corrida programada: %s', exc)


async def scheduler_tick(
    sb: Any, *, limit: int = DEFAULT_LIMIT, checker: Checker | None = None,
) -> dict[str, Any]:
    """Un latido del scheduler: si toca, limpia; si no, no hace nada."""
    if sb is None:
        return {'ran': False, 'schedule': _default_schedule()}

    schedule = await read_schedule(sb)
    if not is_due(schedule):
        return {'ran': False, 'schedule': schedule}

    # Se sella ANTES de correr: si la corrida muere a la mitad, el próximo tick
    # espera el intervalo completo en vez de reintentar en loop cada 5 minutos.
    await _mark_ran(sb)
    summary = await run_cleanup(sb, limit=limit, checker=checker, origen='scheduled')
    return {'ran': True, 'summary': summary, 'schedule': await read_schedule(sb)}


async def scheduler_loop(
    get_sb: Callable[[], Any], *, poll_seconds: int = DEFAULT_POLL_SECONDS,
) -> None:
    """Loop de fondo que arranca con la app (ver `app.main.lifespan`).

    Poll barato contra la fila de programación; el intervalo real ("cada 7
    días") lo decide `is_due` contra `last_run_at` PERSISTIDO, así que reiniciar
    el backend no adelanta ni pierde una limpieza.
    """
    while True:
        try:
            await scheduler_tick(get_sb())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('cleaner: tick del scheduler falló: %s', exc)
        await asyncio.sleep(poll_seconds)
