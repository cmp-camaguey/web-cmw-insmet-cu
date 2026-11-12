"""Poda de datos de monitoreo.

La lógica vive acá y no en la tarea de Huey porque la usan dos entradas: la
tarea periódica y el comando `prune_monitoring` (para correrla a mano o desde
cron). Duplicar las ventanas de retención en dos sitios era la forma segura de
que se separaran con el tiempo.
"""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.monitoring.models import JavaScriptError, PageView, RequestMetric, WebVital


def prune_monitoring(now=None):
    now = now or timezone.now()
    rum_days = getattr(settings, 'MONITORING_RETENTION_DAYS', 30)
    http_days = getattr(settings, 'MONITORING_HTTP_RETENTION_DAYS', 14)
    rum_cutoff = now - timedelta(days=rum_days)
    http_cutoff = now - timedelta(days=http_days)

    deleted = {
        'request_metrics': RequestMetric.objects.filter(created_at__lt=http_cutoff).delete()[0],
        'pageviews': PageView.objects.filter(created_at__lt=rum_cutoff).delete()[0],
        'web_vitals': WebVital.objects.filter(created_at__lt=rum_cutoff).delete()[0],
        'javascript_errors': JavaScriptError.objects.filter(created_at__lt=rum_cutoff).delete()[0],
    }
    return deleted
