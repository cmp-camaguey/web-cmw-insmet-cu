"""Correos transaccionales del área comercial que no son factura ni certificado.

Vive fuera de `views/` por la misma razón que `notifications.py`: el hecho es de
dominio y no de la pantalla que lo provoca.

Ver `odd/tasks/030b-avisos-de-clientes.md` S5 y A3.
"""

import logging

from django.conf import settings
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

logger = logging.getLogger(__name__)


def enviar_correo_anulacion(subscription):
    """Avisa al cliente que su suscripción quedó anulada.

    Es transaccional y sale solo a la dirección del titular: no pasa por la
    audiencia ni por la ventana horaria, igual que la factura y el certificado.
    Solo la anulación necesita correo propio, porque los otros dos cambios de
    estado ya escriben al cliente por su camino: `pending` manda la factura y
    `paid` manda el certificado. Avisar también de esos dos sería mandar dos
    correos del mismo hecho.

    Devuelve `True` si se encoló. Un cliente creado desde el panel puede no
    tener cuenta ni correo; en ese caso no hay a quién avisar y la anulación
    sigue siendo válida.
    """
    from apps.core.tasks import send_email_task

    customer = subscription.customer
    email = (getattr(customer.user, 'email', '') or '').strip()
    if not email:
        logger.info(
            'Suscripción %s anulada sin correo del cliente: no se envía aviso.',
            subscription.uuid,
        )
        return False

    service = subscription.service
    context = {
        'title': 'Su suscripción quedó anulada',
        'customer_name': customer.display_name,
        'service_title': service.title,
        'cancelled_date': date_format(timezone.localdate(), 'DATE_FORMAT'),
        'service_url': settings.SITE_URL
        + reverse('home:services_commercial_detail', args=[service.uuid]).lstrip('/'),
        'index_url': settings.SITE_URL,
        'current_year': timezone.now().year,
    }
    html_message = render_to_string('emails/suscripcion_anulada.html', context)
    send_email_task(
        f'Su suscripción a {service.title} quedó anulada',
        html_message,
        settings.DEFAULT_FROM_EMAIL,
        [email],
    )
    return True
