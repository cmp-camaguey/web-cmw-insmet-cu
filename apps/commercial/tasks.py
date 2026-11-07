"""Tareas periódicas del área comercial (cambio 026, huecos de notificaciones).

Huey corre con `utc=True` (ver `config/huey.py`), así que el `crontab` de este
módulo se lee en UTC. Las 13 UTC son las 9 de la mañana en `America/Havana`: un
recordatorio de cobro a primera hora se puede atender el mismo día.
"""

import logging
from datetime import timedelta

from huey import crontab

from config.huey import huey

logger = logging.getLogger(__name__)


def facturas_pendientes(now=None):
    """Recuerda las facturas que llevan demasiado tiempo sin cobrarse.

    Una factura no tiene estado propio: el pago vive en la suscripción de cada
    línea (`Invoice.status_display`, `apps/commercial/models.py`). Por eso el
    filtro mira las líneas con suscripción `pending` y activa, no un campo de la
    factura que podría divergir del cobro real.

    El recordatorio se repite cada `INVOICE_PENDING_REMINDER_DAYS` mientras el
    cobro siga pendiente. Sin ese intervalo, leer el aviso lo haría reaparecer al
    día siguiente, que es la forma más rápida de que nadie vuelva a mirar el
    icono.

    Devuelve las notificaciones creadas para que el log diga qué pasó.
    """
    from django.conf import settings
    from django.urls import reverse
    from django.utils import timezone

    from apps.commercial.models import Invoice
    from apps.core.models import Notification
    from apps.core.notifications import make_dedupe_key, notify_superusers

    days = settings.INVOICE_PENDING_REMINDER_DAYS
    if days <= 0:
        return []

    now = now or timezone.now()
    cutoff = now - timedelta(days=days)

    invoices = (
        Invoice.objects.filter(
            record_active=True,
            is_cancelled=False,
            issue_date__lte=cutoff,
            items__subscription__payment_status='pending',
            items__subscription__record_active=True,
        )
        .distinct()
        .order_by('issue_date')
    )

    created = []
    for invoice in invoices:
        created.extend(
            notify_superusers(
                source='invoice_pending',
                severity=Notification.Severity.WARNING,
                title='Factura pendiente de pago',
                body=(
                    f'La factura {invoice.number} de '
                    f'{invoice.customer.display_name if invoice.customer else "un cliente"} '
                    f'lleva más de {days} días sin pagarse.'
                ),
                url=reverse('commercial:factura_list'),
                dedupe_key=make_dedupe_key('invoice_pending', invoice.uuid),
                repeat_after=timedelta(days=days),
            )
        )

    if created:
        logger.info('Recordatorios de facturas pendientes: %s', len(created))
    return created


@huey.periodic_task(crontab(minute=0, hour=13), name='recordar_facturas_pendientes')
def recordar_facturas_pendientes():
    """Corrida diaria del recordatorio. Ver `facturas_pendientes`."""
    return len(facturas_pendientes())
