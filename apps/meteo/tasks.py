"""Tareas periódicas de meteorología (cambio 026).

Huey corre con `utc=True` (el default de `SqliteHuey`, ver `config/huey.py`), así
que los `crontab` de este módulo se leen en UTC. Las 12 UTC son las 8 de la
mañana en `America/Havana`: un recordatorio de vencimiento a primera hora del día
es útil, a medianoche no.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from huey import crontab

from config.huey import huey

logger = logging.getLogger(__name__)

# Ruta de la lista de cada tipo de aviso: es donde se edita o se borra el aviso
# que está por vencer, que es la acción útil para quien lo recibe.
WARNING_URL_NAMES = {
    'early': 'meteo:alerta_temprana_list',
    'storm': 'meteo:tormenta_list',
    'tropical_cyclone': 'meteo:ciclon_tropical_list',
}


def avisos_por_vencer(horizon_hours=None):
    """Crea un recordatorio por cada aviso que vence dentro del horizonte.

    La clave de deduplicación es el aviso (`design.md` §2): mientras el admin no
    lo lea, la corrida diaria no acumula recordatorios del mismo vencimiento.

    Devuelve las notificaciones creadas, para que el log diga qué pasó.
    """
    from apps.core.models import Notification
    from apps.core.notifications import make_dedupe_key, notify_superusers
    from apps.meteo.models import Warning

    hours = (
        horizon_hours if horizon_hours is not None else settings.NOTIFICATIONS_WARNING_HORIZON_HOURS
    )
    now = timezone.now()
    limite = now + timedelta(hours=hours)

    # Ya vencido no es "por vencer": el aviso dejó de estar vigente y no hay
    # nada que el admin pueda adelantar.
    pendientes = Warning.objects.filter(valid_until__gt=now, valid_until__lte=limite)

    creadas = []
    for warning in pendientes:
        tipo = warning.get_warning_type_display()
        nombre = (warning.title or '').strip()
        vence = timezone.localtime(warning.valid_until).strftime('%d/%m/%Y %H:%M')
        creadas.extend(
            notify_superusers(
                source='warning',
                severity=Notification.Severity.WARNING,
                title=f'{tipo} por vencer: {nombre}' if nombre else f'{tipo} por vencer',
                body=f'Vence el {vence}.\n\n{warning.summary}'[:1000],
                url=reverse(WARNING_URL_NAMES[warning.warning_type])
                if warning.warning_type in WARNING_URL_NAMES
                else '',
                dedupe_key=make_dedupe_key('warning', warning.uuid),
                # El recordatorio muere con el aviso que recuerda: después de
                # `valid_until` no queda nada accionable que hacer.
                expires_at=warning.valid_until,
            )
        )
    return creadas


@huey.periodic_task(crontab(minute=0, hour=12), name='notificar_avisos_por_vencer')
def notificar_avisos_por_vencer_task():
    """Corrida diaria del recordatorio de avisos por vencer."""
    try:
        creadas = avisos_por_vencer()
        logger.info('Avisos por vencer notificados: %s', len(creadas))
    except Exception:
        # Se re-lanza para que la tarea quede en ERROR y se vea en el panel de
        # tareas. No hay bucle de avisos: esta tarea no está en la allowlist.
        logger.exception('Fallo al notificar los avisos por vencer')
        raise
