"""Notificaciones del panel originadas en el área comercial (cambio 026).

Vive fuera de `views/` a propósito: la fuente del hecho es el dominio, no la
vista. La solicitud de servicio la crea el portal público y el recordatorio de
facturas lo corre Huey, así que poner estas funciones dentro de una vista
obligaría a la otra mitad a importar una vista entera para notificar.
"""


def notificar_solicitud_suscripcion(subscription):
    """Avisa a los admins que un cliente pidió un servicio desde el portal.

    Solo se llama desde el camino del CLIENTE. Si la suscripción la crea el
    staff desde el panel, quien la crea ya sabe que existe: notificarle su propia
    acción convertiría el icono en un eco.

    La clave incluye la suscripción y no la fecha: dos envíos del mismo
    formulario generan dos suscripciones distintas y, por tanto, dos hechos
    distintos que el admin debe ver por separado.
    """
    from django.urls import reverse

    from apps.core.models import Notification
    from apps.core.notifications import make_dedupe_key, notify_superusers

    return notify_superusers(
        source='subscription',
        severity=Notification.Severity.INFO,
        title='Nueva solicitud de servicio',
        body=(
            f'{subscription.customer.display_name} solicitó '
            f'{subscription.service.title} ({subscription.quantity}).'
        ),
        url=reverse('commercial:suscripcion_list'),
        dedupe_key=make_dedupe_key('subscription', subscription.uuid, 'requested'),
    )


def notificar_factura_emitida(invoice):
    """Avisa al cliente, y a los admins, que su factura ya existe.

    Es un aviso de cliente (`source='invoice'`): `notify_audience` deja entrar al
    titular de la factura, y el admin lo sigue viendo porque toda fuente alcanza
    a los superusuarios. El enlace es «Mis Suscripciones» y no la factura: el
    cliente no tiene `view_invoice`, así que la lista de facturación le da 403 y
    el permiso que sí tiene es `view_subscription`.

    La clave es la factura, no la fecha de emisión: reemitir el PDF o reenviar el
    correo no crea otro aviso.
    """
    from django.urls import reverse

    from apps.core.models import Notification
    from apps.core.notifications import make_dedupe_key, notify_audience

    customer = invoice.customer
    if customer is None:
        return []

    return notify_audience(
        source='invoice',
        severity=Notification.Severity.INFO,
        title=f'Factura {invoice.number} emitida',
        body=f'Período {invoice.period_label} · {invoice.amount} CUP.',
        url=reverse('commercial:suscripcion_list'),
        dedupe_key=make_dedupe_key('invoice', invoice.uuid, 'issued'),
        customers=[customer],
    )


def notificar_estado_suscripcion(subscription, estado):
    """Avisa al cliente de un cambio de estado de su suscripción.

    Solo dos estados lo necesitan: `paid` (pago aprobado) y `cancelled`
    (anulada). `requested` lo provoca el propio cliente desde el portal y
    `pending` llega con la factura, que ya tiene su aviso; avisar aquí de esos
    dos sería contar el mismo hecho dos veces.

    El aviso es para el titular y para los admins: la anulación la decide el
    operador, pero el cliente tiene que verla en su campana aunque el correo
    falle.
    """
    from django.urls import reverse

    from apps.core.models import Notification
    from apps.core.notifications import make_dedupe_key, notify_audience

    textos = {
        'paid': (
            'Pago aprobado',
            'Su certificado está disponible en el listado de suscripciones.',
        ),
        'cancelled': (
            'Suscripción anulada',
            'La solicitud de servicio quedó anulada.',
        ),
    }
    titulo, detalle = textos[estado]

    customer = subscription.customer
    return notify_audience(
        source='subscription',
        severity=Notification.Severity.INFO,
        title=f'{titulo}: {subscription.service.title}',
        body=detalle,
        url=reverse('commercial:suscripcion_list'),
        dedupe_key=make_dedupe_key('subscription', subscription.uuid, estado),
        customers=[customer],
    )


def notificar_cliente_desactivado(customer, suscripciones_vivas):
    """Avisa a los admins que se desactivó un cliente que sigue con servicio.

    No es un eco de la acción: es la única superficie donde el hecho aparece. La
    pantalla no lo dice, el cliente desaparece del listado y sus suscripciones
    siguen vivas, así que lo que se está dejando de facturar no se ve en ningún
    sitio.

    `source='commercial'` y no `'subscription'`: es información interna de
    facturación, no un mensaje para el cliente. Cuando la audiencia por `source`
    entre en juego, una fuente que no sea de cliente tiene que quedarse en los
    superusuarios, y nombrarla aquí lo deja explícito en vez de depender de que
    `'subscription'` signifique dos cosas.
    """
    from django.urls import reverse

    from apps.core.models import Notification
    from apps.core.notifications import make_dedupe_key, notify_superusers

    if suscripciones_vivas == 1:
        detalle = 'su suscripción activa sigue en pie'
    else:
        detalle = f'sus {suscripciones_vivas} suscripciones activas siguen en pie'

    return notify_superusers(
        source='commercial',
        severity=Notification.Severity.WARNING,
        title='Cliente desactivado con servicios activos',
        body=(
            f'{customer.display_name} quedó desactivado y {detalle}. '
            'Revise si corresponde anularlas o seguir facturándolas.'
        ),
        url=reverse('commercial:suscripcion_list'),
        dedupe_key=make_dedupe_key('commercial', customer.uuid, 'deactivated'),
    )
