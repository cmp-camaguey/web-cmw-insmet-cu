"""Registro y agregación de métricas HTTP reales.

Se guarda una fila por petición (ver el docstring de `models.py`). Las
consultas están acotadas: los percentiles usan una muestra de las últimas
5 000 peticiones y la concurrencia las últimas 1 000, para que un panel abierto
toda la mañana no termine leyendo un día entero de filas.
"""

import logging
from datetime import UTC, timedelta

from django.conf import settings
from django.db.models import Avg, Count, Max, Q
from django.db.models.functions import TruncMinute
from django.utils import timezone

from apps.monitoring.models import RequestMetric

logger = logging.getLogger(__name__)

# Rutas que no se registran: estáticos, media, el beacon (si se registrara, el
# propio panel se contaría como tráfico del sitio) y el endpoint JSON que este
# panel consulta cada 5 segundos.
EXCLUDED_PREFIXES = (
    '/static/',
    '/media/',
    '/favicon.ico',
    '/rum/collect/',
    '/dashboard/monitoring/data/',
)

PERCENTILE_SAMPLE = 5000
CONCURRENCY_SAMPLE = 1000
UPTIME_MIN_SAMPLES = 20


def is_excluded(path):
    return any(path.startswith(prefix) for prefix in EXCLUDED_PREFIXES)


def record_request(request, status_code, duration_ms):
    """Guarda una petición. Devuelve si se registró; nunca lanza."""
    if not getattr(settings, 'MONITORING_ENABLED', True):
        return False
    path = (getattr(request, 'path', '/') or '/')[:255]
    if is_excluded(path):
        return False
    try:
        RequestMetric.objects.create(
            method=(request.method or 'GET')[:10],
            path=path,
            status_code=int(status_code),
            duration_ms=max(0, int(duration_ms)),
        )
        return True
    except Exception:
        # Un fallo al medir jamás puede tumbar la petición que se estaba
        # midiendo: se registra en el log y se sigue.
        logger.warning('monitoring: no se pudo registrar la petición', exc_info=True)
        return False


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = round(fraction * (len(ordered) - 1))
    index = min(len(ordered) - 1, max(0, index))
    return ordered[index]


def _status_counts(queryset):
    return queryset.aggregate(
        total=Count('id'),
        status_2xx=Count('id', filter=Q(status_code__lt=300)),
        status_3xx=Count('id', filter=Q(status_code__gte=300, status_code__lt=400)),
        status_4xx=Count('id', filter=Q(status_code__gte=400, status_code__lt=500)),
        status_5xx=Count('id', filter=Q(status_code__gte=500)),
        max_ms=Max('duration_ms'),
        avg_ms=Avg('duration_ms'),
    )


def http_summary(minutes=60, now=None):
    now = now or timezone.now()
    since = now - timedelta(minutes=minutes)
    queryset = RequestMetric.objects.filter(created_at__gte=since)
    counts = _status_counts(queryset)
    total = counts['total'] or 0
    durations = list(
        queryset.order_by('-created_at').values_list('duration_ms', flat=True)[:PERCENTILE_SAMPLE]
    )
    return {
        'window_minutes': minutes,
        'total': total,
        'status': {
            '2xx': counts['status_2xx'] or 0,
            '3xx': counts['status_3xx'] or 0,
            '4xx': counts['status_4xx'] or 0,
            '5xx': counts['status_5xx'] or 0,
        },
        'error_rate': round(100 * (counts['status_5xx'] or 0) / total, 2) if total else None,
        'avg_ms': round(counts['avg_ms'], 1) if counts['avg_ms'] is not None else None,
        'p50_ms': _percentile(durations, 0.50),
        'p95_ms': _percentile(durations, 0.95),
        'max_ms': counts['max_ms'],
        'samples': len(durations),
        'concurrency': concurrency(now=now),
    }


def uptime(hours=24, now=None):
    """Uptime = porcentaje de peticiones registradas que no devolvieron 5xx."""
    now = now or timezone.now()
    since = now - timedelta(hours=hours)
    counts = _status_counts(RequestMetric.objects.filter(created_at__gte=since))
    total = counts['total'] or 0
    errors = counts['status_5xx'] or 0
    percent = None
    if total >= UPTIME_MIN_SAMPLES:
        percent = round(100 * (total - errors) / total, 2)
    return {
        'window_hours': hours,
        'samples': total,
        'errors': errors,
        'percent': percent,
        'slots': uptime_slots(hours=hours, now=now),
    }


def uptime_slots(hours=24, now=None, slot_minutes=15):
    """Bloques estilo status page: un bloque por tramo, con su estado."""
    now = now or timezone.now()
    since = now - timedelta(hours=hours)
    rows = (
        RequestMetric.objects.filter(created_at__gte=since)
        # La truncación se fija en UTC: el middleware de zona horaria activa la
        # zona del sitio por request y la rejilla de minutos debe ser la misma
        # que la de la serie que se dibuja.
        .annotate(bucket=TruncMinute('created_at', tzinfo=UTC))
        .values('bucket')
        .annotate(
            total=Count('id'),
            errors=Count('id', filter=Q(status_code__gte=500)),
        )
        .order_by('bucket')
    )
    per_minute = {}
    for row in rows:
        bucket = row['bucket']
        if timezone.is_aware(bucket):
            bucket = timezone.localtime(bucket)
        key = bucket.replace(second=0, microsecond=0)
        per_minute[key] = (row['total'], row['errors'])

    slot_count = max(1, int(hours * 60 / slot_minutes))
    start = (now - timedelta(hours=hours)).replace(second=0, microsecond=0)
    buckets = [[0, 0] for _ in range(slot_count)]
    for minute, values in per_minute.items():
        if minute < start or minute > now:
            continue
        index = int((minute - start).total_seconds() // (60 * slot_minutes))
        # El minuto en curso cae justo en el borde derecho de la ventana; se
        # asigna al último bloque en vez de quedar fuera.
        index = min(slot_count - 1, max(0, index))
        buckets[index][0] += values[0]
        buckets[index][1] += values[1]

    slots = []
    for index in range(slot_count):
        slot_start = start + timedelta(minutes=slot_minutes * index)
        total, errors = buckets[index]
        if total == 0:
            state = 'nodata'
        elif errors == 0:
            state = 'ok'
        elif errors < total:
            state = 'degraded'
        else:
            state = 'down'
        slots.append(
            {
                'label': slot_start.strftime('%H:%M'),
                'state': state,
                'total': total,
                'errors': errors,
            }
        )
    return slots


def active_incident(now=None, lookback=500):
    """Racha de 5xx en curso y cuánto lleva.

    Se camina hacia atrás desde la última petición hasta la primera que no fue
    5xx. `lookback` acota el cálculo: una caída con más de 500 peticiones
    seguidas reporta una duración menor que la real, nunca mayor.
    """
    now = now or timezone.now()
    rows = list(
        RequestMetric.objects.order_by('-created_at').values_list('created_at', 'status_code')[
            :lookback
        ]
    )
    streak = []
    for created_at, status_code in rows:
        if status_code < 500:
            break
        streak.append(created_at)
    if not streak:
        return {'active': False, 'since': None, 'duration_s': None, 'count': 0}
    since = min(streak)
    return {
        'active': True,
        'since': timezone.localtime(since).isoformat(),
        'duration_s': int((now - since).total_seconds()),
        'count': len(streak),
    }


def concurrency(seconds=60, now=None):
    """Peticiones simultáneas máximas en los últimos `seconds`.

    Se aproxima por solape de intervalos: cada petición ocupa desde
    `created_at - duration` hasta `created_at`. Es una cota real de lo que
    pasó, no una estimación inventada.
    """
    now = now or timezone.now()
    since = now - timedelta(seconds=seconds)
    rows = list(
        RequestMetric.objects.filter(created_at__gte=since)
        .order_by('created_at')
        .values_list('created_at', 'duration_ms')[:CONCURRENCY_SAMPLE]
    )
    events = []
    for created_at, duration in rows:
        events.append((created_at - timedelta(milliseconds=duration), 1))
        events.append((created_at, -1))
    events.sort()
    current = peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def requests_per_minute(minutes=30, now=None):
    """Serie por minuto para el sparkline. Rellena los minutos sin tráfico."""
    now = now or timezone.now()
    since = (now - timedelta(minutes=minutes)).replace(second=0, microsecond=0)
    rows = (
        RequestMetric.objects.filter(created_at__gte=since)
        # La truncación se fija en UTC: el middleware de zona horaria activa la
        # zona del sitio por request y la rejilla de minutos debe ser la misma
        # que la de la serie que se dibuja.
        .annotate(bucket=TruncMinute('created_at', tzinfo=UTC))
        .values('bucket')
        .annotate(count=Count('id'), avg_ms=Avg('duration_ms'))
        .order_by('bucket')
    )
    buckets = {}
    for row in rows:
        bucket = row['bucket']
        if timezone.is_aware(bucket):
            bucket = timezone.localtime(bucket)
        buckets[bucket.replace(second=0, microsecond=0)] = (
            row['count'],
            round(row['avg_ms'], 1) if row['avg_ms'] is not None else None,
        )

    series = []
    for index in range(minutes + 1):
        minute = since + timedelta(minutes=index)
        count, avg_ms = buckets.get(minute, (0, None))
        series.append({'time': minute.strftime('%H:%M'), 'count': count, 'avg_ms': avg_ms})
    return series
