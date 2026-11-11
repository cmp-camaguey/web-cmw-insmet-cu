"""Lectura de recursos del servidor en tiempo real.

Todo pasa por `psutil`. Dos detalles que no son obvios:

1. `cpu_percent(interval=None)` es no bloqueante pero necesita una muestra
   previa: la primera llamada de cada proceso devuelve 0.0 y mentiría. Se
   devuelve `None` en esa primera llamada y la plantilla muestra `—`.
2. Los bytes de red y de disco son acumulados desde el arranque del host. La
   velocidad se calcula con la muestra anterior guardada en caché. Con varios
   workers cada proceso lleva su propia muestra, pero como los contadores son
   del host, cada uno calcula la misma velocidad real; lo único que cambia es
   la ventana de promedio (más larga si ese worker atendió pocas peticiones).

Ninguna lectura puede romper la página: si psutil falla, el campo queda en
`None` y se dibuja `—`.
"""

import logging
import os
import time

import psutil
from django.conf import settings
from django.core.cache import cache

from apps.monitoring.services.formatting import format_duration

logger = logging.getLogger(__name__)

_RATE_CACHE_TTL = 300
_cpu_primed = False


def _safe(call, default=None):
    try:
        return call()
    except Exception:
        logger.debug('monitoring: no se pudo leer una métrica del sistema', exc_info=True)
        return default


def _rate(key, value, now):
    """Velocidad por segundo entre dos muestras consecutivas, o `None`."""
    if value is None:
        return None
    cache_key = f'monitoring:rate:{key}'
    previous = cache.get(cache_key)
    cache.set(cache_key, (value, now), _RATE_CACHE_TTL)
    if not previous:
        return None
    previous_value, previous_ts = previous
    elapsed = now - previous_ts
    if elapsed <= 0 or value < previous_value:
        return None
    return (value - previous_value) / elapsed


def _bytes_human(value):
    """Bytes a texto corto, para no mandar números de 12 dígitos al panel."""
    if value is None:
        return None
    units = ('B', 'KB', 'MB', 'GB', 'TB')
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f'{amount:.1f} {unit}' if unit != 'B' else f'{int(amount)} B'
        amount /= 1024
    return f'{amount:.1f} TB'


def _cpu():
    global _cpu_primed
    per_core = _safe(lambda: psutil.cpu_percent(interval=None, percpu=True), [])
    if not per_core:
        return {'percent': None, 'cores': [], 'count': _safe(psutil.cpu_count, None)}
    if not _cpu_primed:
        # Primera llamada del proceso: psutil todavía no tiene contra qué
        # comparar y devuelve todo en 0.0.
        _cpu_primed = True
        return {'percent': None, 'cores': [], 'count': len(per_core)}
    cores = [round(value, 1) for value in per_core]
    return {
        'percent': round(sum(cores) / len(cores), 1),
        'cores': cores,
        'count': len(cores),
    }


def _memory():
    memory = _safe(psutil.virtual_memory)
    if memory is None:
        return {'percent': None, 'used': None, 'total': None, 'available': None}
    return {
        'percent': memory.percent,
        'used': _bytes_human(memory.used),
        'total': _bytes_human(memory.total),
        'available': _bytes_human(memory.available),
    }


def _swap():
    swap = _safe(psutil.swap_memory)
    if swap is None:
        return {'percent': None, 'used': None, 'total': None}
    return {
        'percent': swap.percent,
        'used': _bytes_human(swap.used),
        'total': _bytes_human(swap.total),
    }


def _disk():
    path = getattr(settings, 'MONITORING_DISK_PATH', '/')
    usage = _safe(lambda: psutil.disk_usage(path))
    if usage is None:
        return {'percent': None, 'used': None, 'total': None, 'free': None, 'path': path}
    return {
        'percent': usage.percent,
        'used': _bytes_human(usage.used),
        'total': _bytes_human(usage.total),
        'free': _bytes_human(usage.free),
        'path': path,
    }


def _disk_io(now):
    counters = _safe(psutil.disk_io_counters)
    if counters is None:
        return {'read_per_s': None, 'write_per_s': None}
    read_per_s = _rate('disk_read', counters.read_bytes, now)
    write_per_s = _rate('disk_write', counters.write_bytes, now)
    return {
        'read_per_s': _bytes_human(read_per_s) if read_per_s is not None else None,
        'write_per_s': _bytes_human(write_per_s) if write_per_s is not None else None,
    }


def _network(now):
    counters = _safe(psutil.net_io_counters)
    if counters is None:
        return {'sent_per_s': None, 'recv_per_s': None, 'sent_total': None, 'recv_total': None}
    sent_per_s = _rate('net_sent', counters.bytes_sent, now)
    recv_per_s = _rate('net_recv', counters.bytes_recv, now)
    return {
        'sent_per_s': _bytes_human(sent_per_s) if sent_per_s is not None else None,
        'recv_per_s': _bytes_human(recv_per_s) if recv_per_s is not None else None,
        'sent_total': _bytes_human(counters.bytes_sent),
        'recv_total': _bytes_human(counters.bytes_recv),
    }


def _load():
    try:
        one, five, fifteen = os.getloadavg()
    except OSError:
        # Plataformas sin load average.
        return {'one': None, 'five': None, 'fifteen': None}
    except AttributeError:
        return {'one': None, 'five': None, 'fifteen': None}
    return {'one': round(one, 2), 'five': round(five, 2), 'fifteen': round(fifteen, 2)}


def _process():
    process = _safe(psutil.Process, None)
    if process is None:
        process = _safe(lambda: psutil.Process(os.getpid()))
    if process is None:
        return {'rss': None, 'threads': None, 'uptime_s': None, 'pid': None}
    memory = _safe(process.memory_info)
    create_time = _safe(process.create_time)
    uptime = int(time.time() - create_time) if create_time else None
    return {
        'pid': process.pid,
        'rss': _bytes_human(memory.rss) if memory is not None else None,
        'threads': _safe(process.num_threads),
        'uptime_s': uptime,
        'uptime_display': format_duration(uptime),
    }


def _host_uptime():
    boot_time = _safe(psutil.boot_time)
    if not boot_time:
        return None
    return int(time.time() - boot_time)


def system_snapshot():
    """Foto completa de recursos. Nunca lanza: los campos caen a `None`."""
    now = time.time()
    host_uptime = _host_uptime()
    return {
        'cpu': _cpu(),
        'memory': _memory(),
        'swap': _swap(),
        'disk': _disk(),
        'disk_io': _disk_io(now),
        'network': _network(now),
        'load': _load(),
        'process': _process(),
        'host_uptime_s': host_uptime,
        'host_uptime_display': format_duration(host_uptime),
    }
