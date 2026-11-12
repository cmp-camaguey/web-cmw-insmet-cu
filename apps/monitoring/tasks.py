"""Tareas periódicas de monitoreo.

`apps.monitoring.apps.MonitoringConfig.ready()` importa este módulo: ese import
es lo que registra el `crontab` en Huey. Si se borra, la poda deja de existir
sin que nada falle a la vista.
"""

from huey import crontab

from apps.monitoring.prune import prune_monitoring
from config.huey import huey


@huey.periodic_task(crontab(minute=30, hour=4), name='podar_monitoreo')
def podar_monitoreo():
    return prune_monitoring()
