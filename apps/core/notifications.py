"""Creación de avisos operativos (cambio 026).

La notificación se crea **en el borde que conoce el hecho**, nunca por una
señal genérica: un `post_save` sobre `Invoice` que mire `pdf_status` y otro
sobre `TaskExecutionLog` se pisarían, porque el PDF fallido de una factura
también deja una tarea en `ERROR` y el admin vería dos avisos del mismo hecho.
Ver `openspec/changes/026-sistema-notificaciones/design.md` §3.
"""

import hashlib
import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction

logger = logging.getLogger(__name__)

# Tareas de Huey cuyo fallo merece un aviso. Es opt-in a propósito: la tarea de
# factura queda FUERA porque tiene su aviso específico (PDF y correo fallidos en
# `apps/commercial/views/invoice_utils.py`), y avisar por las dos vías mostraría
# dos veces el mismo hecho. Una tarea nueva no avisa hasta que alguien decida
# que su fallo es accionable: un worker que falla en algo interno no es noticia
# para quien administra el portal.
TASK_ERROR_ALLOWLIST = frozenset({'send_email_task'})


def make_dedupe_key(*parts):
    """sha256 hex de lo que identifica el HECHO, no el momento.

    Cada fuente define sus partes (ver la tabla de `design.md` §2). Regla común:
    el `created_at` no entra. Sin eso, tres reintentos de la misma tarea dejan
    tres filas.
    """
    raw = '|'.join(str(p) for p in parts)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def notify(
    recipient,
    *,
    source,
    severity,
    title,
    body='',
    url='',
    dedupe_key,
    expires_at=None,
):
    """Crea el aviso si no existe uno sin leer con la misma clave.

    Devuelve la `Notification` (la existente si la había, o `None` cuando el
    sistema está apagado).

    El dedup real lo hace el índice único parcial de la base, no un `exists()`
    previo: dos workers de Gunicorn pueden ver el mismo fallo a la vez y un
    `get_or_create` sin protección pierde la carrera con `IntegrityError`. El
    `atomic` es necesario además por otra razón: en PostgreSQL un
    `IntegrityError` deja la transacción en estado abortado, y sin el savepoint
    el error se propagaría hasta el llamador y arrastraría trabajo que no tiene
    nada que ver.
    """
    from apps.core.models import Notification

    if not getattr(settings, 'NOTIFICATIONS_ENABLED', True):
        return None

    if severity not in Notification.Severity.values:
        raise ValueError(
            f"severity='{severity}' no es una severidad válida "
            f'({", ".join(Notification.Severity.values)}).'
        )

    lookup = {'recipient': recipient, 'dedupe_key': dedupe_key, 'read_at__isnull': True}
    defaults = {
        'source': source,
        'severity': severity,
        'title': title[:200],
        'body': body,
        'url': url,
        'expires_at': expires_at,
    }

    try:
        with transaction.atomic():
            obj, _created = Notification.objects.get_or_create(**lookup, defaults=defaults)
        return obj
    except IntegrityError:
        # Otro proceso la creó entre el SELECT y el INSERT. La suya es la buena.
        return Notification.objects.filter(**lookup).first()


def notify_superusers(
    *,
    source,
    severity,
    title,
    body='',
    url='',
    dedupe_key,
    expires_at=None,
):
    """Notifica a todos los superusuarios activos y devuelve las creadas.

    El conjunto de destinatarios es fijo a propósito (dos admins en el repo):
    una tabla de preferencias por usuario sería complejidad sin uso. Si algún
    día entra un rol que deba ver solo una parte, esa es la señal para agregarla.
    """
    from django.contrib.auth import get_user_model

    created = []
    for user in get_user_model().objects.filter(is_superuser=True, is_active=True):
        obj = notify(
            user,
            source=source,
            severity=severity,
            title=title,
            body=body,
            url=url,
            dedupe_key=dedupe_key,
            expires_at=expires_at,
        )
        if obj is not None:
            created.append(obj)
    return created


def notify_task_failure(task_name, *, task_id='', logical_key='', error=''):
    """Aviso de una tarea de Huey que terminó en ERROR, si está en la allowlist.

    Devuelve la lista vacía cuando la tarea no es accionable, que es el caso
    normal: solo las tareas de `TASK_ERROR_ALLOWLIST` avisan.
    """
    from django.urls import reverse

    from apps.core.models import Notification

    if task_name not in TASK_ERROR_ALLOWLIST:
        return []

    return notify_superusers(
        source='huey',
        severity=Notification.Severity.ERROR,
        title=f'Falló la tarea {task_name}',
        body=(error or '')[:500],
        url=reverse('dashboard:tasks'),
        # La clave es el trabajo lógico cuando existe (reintentar la misma
        # factura no duplica); si no, el id del encolado, que los reintentos de
        # ese mismo encolado comparten.
        dedupe_key=make_dedupe_key('huey', logical_key or task_id, 'ERROR'),
    )


def visible_for(user):
    """Avisos del usuario que todavía se deben mostrar.

    Una sola definición para el navbar, el historial y el contador: si cada uno
    filtrara por su lado, el badge podría decir 3 mientras la lista muestra 2, y
    ese desacuerdo es justo lo que hace desconfiar del aviso.

    Un aviso vencido no se muestra aunque la poda todavía no lo haya borrado:
    `expires_at` es la fecha del hecho, la poda es solo mantenimiento.
    """
    from django.db.models import Q
    from django.utils import timezone

    from apps.core.models import Notification

    return Notification.objects.filter(recipient=user).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
    )


def unread_count(user):
    """Cuántos avisos vigentes le quedan sin leer. `None` si el sistema está apagado.

    `None` y `0` no son lo mismo: el navbar esconde el icono con `None` (sistema
    apagado) y pinta "sin badge" con `0` (sistema encendido y nada pendiente).
    """
    if not getattr(settings, 'NOTIFICATIONS_ENABLED', True):
        return None
    return visible_for(user).filter(read_at__isnull=True).count()


def prune_notifications(now=None):
    """Borra los avisos que ya no sirven. Idempotente.

    Tres reglas, aplicadas en este orden para que los conteos del log sean
    excluyentes entre sí:

    1. Vencidos: `expires_at < ahora`. El hecho que anunciaban ya pasó.
    2. Leídos viejos: `read_at < ahora - RETENTION_READ`. Ya se vieron; el
       historial largo solo ocupa espacio.
    3. No leídos viejos: `created_at < ahora - RETENTION_UNREAD`. Un aviso de
       hace 90 días que nadie abrió ya no es accionable, y dejarlo solo infla el
       badge para siempre.

    Devuelve un dict con el conteo de cada categoría para que quien lo llame
    (comando o tarea) lo registre.
    """
    from django.utils import timezone

    from apps.core.models import Notification

    now = now or timezone.now()
    read_cutoff = now - timedelta(days=settings.NOTIFICATIONS_RETENTION_READ)
    unread_cutoff = now - timedelta(days=settings.NOTIFICATIONS_RETENTION_UNREAD)

    # Secuencial y no tres `count()` previos: despues de cada `delete()` las
    # filas ya no existen, asi que las categorias no se solapan y el log no
    # suma dos veces la misma fila.
    deleted = {
        'expired': Notification.objects.filter(expires_at__lt=now).delete()[0],
        'read': Notification.objects.filter(
            read_at__isnull=False, read_at__lt=read_cutoff
        ).delete()[0],
        'unread': Notification.objects.filter(
            read_at__isnull=True, created_at__lt=unread_cutoff
        ).delete()[0],
    }

    if any(deleted.values()):
        logger.info('Notificaciones podadas: %s', deleted)
    return deleted
