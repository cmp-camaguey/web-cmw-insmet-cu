"""Tareas periódicas del área comercial (cambio 026, huecos de notificaciones).

Huey corre con `utc=True` (el valor por defecto de `SqliteHuey`; `config/huey.py`
no lo fija a propósito), así que el `crontab` de este módulo se lee en UTC. Las
13 UTC son las 9 de la mañana en `America/Havana` durante el horario de verano y
las 8 durante el de invierno: en ambos casos, un recordatorio de cobro a primera
hora se puede atender el mismo día.
"""

import logging
from collections import defaultdict
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


def contratos_por_vencer(now=None):
    """Avisa de los contratos que vencen dentro del horizonte configurado.

    El vencimiento se filtra en Python porque `Contract.end_date` es una
    propiedad derivada de `start_date` + `validity_years`: la base no puede
    comparar una columna que no existe.

    Un contrato renovado no avisa. Si el cliente ya tiene otro contrato más
    nuevo que cubre la fecha de fin del anterior, el servicio no se interrumpe y
    avisar sería pedir una renovación que ya se firmó.

    `Certificate` queda fuera a propósito: el cliente se queda con sus
    certificados para siempre una vez entregados, así que no vencen y no tienen
    nada que renovar.

    Devuelve las notificaciones creadas para que el log diga qué pasó.
    """
    from django.conf import settings
    from django.urls import reverse
    from django.utils import timezone

    from apps.commercial.models import Contract
    from apps.core.models import Notification
    from apps.core.notifications import make_dedupe_key, notify_superusers

    days = settings.CONTRACT_EXPIRY_WARNING_DAYS
    if days <= 0:
        return []

    # `localdate` y no la fecha UTC: el horizonte de aviso es una fecha de
    # calendario de La Habana. Con UTC, mover el `crontab` a antes de las 05
    # correría el horizonte un día sin que nada lo delate.
    hoy = timezone.localdate(now)
    limite = hoy + timedelta(days=days)

    created = []
    # Una sola consulta: la renovación se comprueba contra los contratos del
    # mismo cliente y pedirlos dentro del bucle sería una consulta por contrato
    # en una tarea que corre a diario.
    contratos = list(
        Contract.objects.filter(record_active=True)
        .select_related('customer')
        .order_by('start_date')
    )
    por_cliente = defaultdict(list)
    for contrato in contratos:
        por_cliente[contrato.customer_id].append(contrato)

    for contrato in contratos:
        fin = contrato.end_date
        if not (hoy <= fin <= limite):
            continue
        # Renovación: otro contrato del mismo cliente que cubre este fin y se
        # extiende más allá. La condición es "se extiende más allá", no "empieza
        # más tarde": el inicio lo escribe el operador a mano y una renovación
        # firmada el mismo día deja el empate que `start_date >` no veía. Exigir
        # `start_date >=` tampoco vale, porque dos contratos idénticos cubren el
        # mismo rango y se suprimirían uno al otro escondiendo dos vencimientos.
        # El propio contrato no entra: no se extiende más allá de su fin.
        renovado = any(
            otro.cubre(fin) and otro.end_date > fin for otro in por_cliente[contrato.customer_id]
        )
        if renovado:
            continue

        created.extend(
            notify_superusers(
                source='commercial',
                severity=Notification.Severity.WARNING,
                title='Contrato por vencer',
                body=(
                    f'El contrato {contrato.number} de '
                    f'{contrato.customer.display_name} vence el '
                    f'{fin.strftime("%d/%m/%Y")}.'
                ),
                url=reverse('commercial:contrato_list'),
                # La fecha de fin entra en la clave porque identifica el hecho:
                # si el contrato se extiende, el aviso que queda es otro.
                dedupe_key=make_dedupe_key(
                    'commercial', contrato.uuid, 'expiring', fin.isoformat()
                ),
                # Semanal: el contrato sigue por vencer y el aviso tiene que
                # volver, pero no cada mañana.
                repeat_after=timedelta(days=7),
            )
        )

    if created:
        logger.info('Avisos de contratos por vencer: %s', len(created))
    return created


@huey.periodic_task(crontab(minute=0, hour=13), name='avisar_contratos_por_vencer')
def avisar_contratos_por_vencer():
    """Corrida diaria del aviso de contratos. Ver `contratos_por_vencer`."""
    return len(contratos_por_vencer())
