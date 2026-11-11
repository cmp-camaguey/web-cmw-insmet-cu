"""Salud de las dependencias: base de datos, caché y cola de tareas."""

import logging
import time
from datetime import timedelta

from django.core.cache import cache
from django.db import connection
from django.utils import timezone

logger = logging.getLogger(__name__)

# Mismo umbral que usa el panel de tareas: una tarea encolada que lleva más de
# cinco minutos sin arrancar significa que el worker de Huey no está corriendo.
STALE_QUEUE_MINUTES = 5


def database_health():
    started = time.perf_counter()
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception:
        logger.warning('monitoring: la base de datos no respondió', exc_info=True)
        return {'ok': False, 'latency_ms': None}
    return {'ok': True, 'latency_ms': round((time.perf_counter() - started) * 1000, 1)}


def cache_health():
    try:
        key = 'monitoring:health:ping'
        cache.set(key, '1', 10)
        return {'ok': cache.get(key) == '1'}
    except Exception:
        logger.warning('monitoring: la caché no respondió', exc_info=True)
        return {'ok': False}


def queue_health():
    from apps.core.models import TaskExecutionLog

    try:
        cutoff = timezone.now() - timedelta(minutes=STALE_QUEUE_MINUTES)
        stale = TaskExecutionLog.objects.filter(
            status=TaskExecutionLog.STATUS_ENQUEUED,
            enqueued_at__lt=cutoff,
        ).count()
        errors = TaskExecutionLog.objects.filter(
            status=TaskExecutionLog.STATUS_ERROR,
        ).count()
    except Exception:
        logger.warning('monitoring: no se pudo leer la cola de tareas', exc_info=True)
        return {'ok': False, 'stale': None, 'errors': None}
    return {'ok': stale == 0, 'stale': stale, 'errors': errors}


def health_snapshot():
    return {
        'database': database_health(),
        'cache': cache_health(),
        'queue': queue_health(),
    }
