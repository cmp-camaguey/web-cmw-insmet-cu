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
