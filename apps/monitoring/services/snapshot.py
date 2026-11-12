"""Arma la foto completa que consume el panel y su endpoint JSON."""

from django.conf import settings
from django.utils import timezone

from apps.monitoring.services import health, http, system, traffic
from apps.monitoring.services.formatting import format_duration

DEGRADED_CPU = 90
DEGRADED_MEMORY = 90
DEGRADED_DISK = 90
DOWN_DISK = 97
ERROR_RATE_DEGRADED = 2
ERROR_RATE_DOWN = 50
MIN_SAMPLES_FOR_DOWN = 20


def _overall(system_data, health_data, http_data, incident):
    down_reasons = []
    degraded_reasons = []

    if not health_data['database']['ok']:
        down_reasons.append('La base de datos no responde.')
    elif (
        health_data['database']['latency_ms'] is not None
        and health_data['database']['latency_ms'] >= 1000
    ):
        degraded_reasons.append('La base de datos tarda más de un segundo en responder.')

    error_rate = http_data['error_rate']
    if (
        http_data['total'] >= MIN_SAMPLES_FOR_DOWN
        and error_rate is not None
        and error_rate >= ERROR_RATE_DOWN
    ):
        down_reasons.append(f'La tasa de errores 5xx es del {error_rate}%.')

    if down_reasons:
        return {'level': 'down', 'label': 'Caído', 'reasons': down_reasons}

    if incident['active']:
        degraded_reasons.append('Hay una racha de errores 5xx en curso.')
    if error_rate is not None and error_rate > ERROR_RATE_DEGRADED:
        degraded_reasons.append(f'La tasa de errores 5xx es del {error_rate}%.')
    if not health_data['queue']['ok']:
        degraded_reasons.append('El worker de tareas tiene tareas encoladas sin arrancar.')
    if not health_data['cache']['ok']:
        degraded_reasons.append('La caché no responde.')

    cpu = system_data['cpu']['percent']
    if cpu is not None and cpu >= DEGRADED_CPU:
        degraded_reasons.append(f'La CPU está al {cpu}%.')
    memory = system_data['memory']['percent']
    if memory is not None and memory >= DEGRADED_MEMORY:
        degraded_reasons.append(f'La memoria está al {memory}%.')
    disk = system_data['disk']['percent']
    if disk is not None and disk >= DOWN_DISK:
        degraded_reasons.append(f'El disco está al {disk}%: no queda espacio utilizable.')
    elif disk is not None and disk >= DEGRADED_DISK:
        degraded_reasons.append(f'El disco está al {disk}%.')

    if degraded_reasons:
        return {'level': 'degraded', 'label': 'Degradado', 'reasons': degraded_reasons}
    return {'level': 'ok', 'label': 'Operativo', 'reasons': []}


def build_snapshot(now=None):
    now = now or timezone.now()
    enabled = getattr(settings, 'MONITORING_ENABLED', True)

    system_data = system.system_snapshot()
    health_data = health.health_snapshot()
    http_data = http.http_summary(minutes=60, now=now)
    http_data['series'] = http.requests_per_minute(minutes=30, now=now)
    uptime_data = http.uptime(hours=24, now=now)
    incident = http.active_incident(now=now)
    incident['duration_display'] = format_duration(incident['duration_s'])
    traffic_data = traffic.traffic_snapshot(now=now)
    vitals_data = traffic.web_vitals(hours=24, now=now)
    errors_data = traffic.javascript_errors(hours=24, now=now)
    weight_data = traffic.page_weight(hours=24, now=now)

    pageviews_24h = traffic_data.get('pageviews_24h') or 0
    errors_data['per_100_pageviews'] = (
        round(errors_data['count'] / pageviews_24h * 100, 2) if pageviews_24h else None
    )

    return {
        'generated_at': timezone.localtime(now).isoformat(),
        'generated_at_display': timezone.localtime(now).strftime('%H:%M:%S'),
        'enabled': enabled,
        'status': _overall(system_data, health_data, http_data, incident),
        'system': system_data,
        'health': health_data,
        'http': http_data,
        'uptime': uptime_data,
        'incident': incident,
        'traffic': traffic_data,
        'vitals': vitals_data,
        'errors': errors_data,
        'weight': weight_data,
        'retention_days': getattr(settings, 'MONITORING_RETENTION_DAYS', 30),
        'http_retention_days': getattr(settings, 'MONITORING_HTTP_RETENTION_DAYS', 14),
        'disk_path': system_data['disk']['path'],
    }
