import logging
import os

from django.core.mail import EmailMessage
from huey import crontab

from config.huey import huey

logger = logging.getLogger(__name__)

RETRY_POLICY = {'retries': 3, 'retry_delay': 30, 'retry_backoff': 2}


@huey.task(**RETRY_POLICY)
def generate_invoice_pdf_and_email_task(invoice_uuid, site_url, solo_paso=None):
    """Genera el PDF de la factura y envía el correo, en dos pasos con estado.

    Los dos pasos se registran por separado en la factura (`pdf_status` y
    `email_status`) porque fallan por razones distintas: el PDF necesita el
    renderizador de WeasyPrint y el correo necesita un SMTP. Antes era una
    sola operación, así que un correo caído se reportaba como tarea exitosa y
    un PDF roto se re-renderizaba en cada reintento aunque el correo ya
    hubiera salido.

    La tarea es idempotente: si el PDF ya quedó `ready` no se vuelve a
    renderizar, y si el correo ya salió no se reenvía. Eso es lo que permite
    que el reintento de Huey corrija un paso sin tocar el otro. `solo_paso`
    fuerza uno de los dos desde el botón de reintentar del dashboard.
    """
    from apps.commercial.models import Invoice
    from apps.commercial.views.invoice_utils import (
        enviar_correo_factura,
        generate_invoice_pdf_standalone,
        marcar_pdf,
    )

    try:
        invoice = Invoice.objects.get(uuid=invoice_uuid)
    except Invoice.DoesNotExist:
        logger.warning(
            'Tarea de factura %s abortada: la factura ya no existe. '
            'La cola la tenía pendiente cuando se borró.',
            invoice_uuid,
        )
        return None

    customer = invoice.customer

    if solo_paso != 'email' and (solo_paso == 'pdf' or not invoice.pdf_ready):
        items = list(invoice.items.all())
        try:
            generate_invoice_pdf_standalone(invoice, customer, items)
        except Exception as e:
            marcar_pdf(invoice, error=e)
            raise
        marcar_pdf(invoice)

    if solo_paso == 'pdf':
        return invoice.uuid

    if (
        solo_paso != 'pdf'
        and invoice.email_status != Invoice.EmailStatus.SENT
        and not enviar_correo_factura(invoice, customer, base_url=site_url)
    ):
        raise RuntimeError(
            f'No se pudo enviar el correo de la factura {invoice.number}: '
            f'{invoice.email_error or "sin detalle"}'
        )

    return invoice.uuid


@huey.task(**RETRY_POLICY)
def send_email_task(
    subject,
    html_message,
    from_email,
    recipients,
    attachment_name=None,
    attachment_content=None,
    attachment_mime=None,
    attachment_path=None,
):
    if attachment_path and os.path.isfile(attachment_path):
        if not attachment_name:
            attachment_name = os.path.basename(attachment_path)
        with open(attachment_path, 'rb') as f:
            attachment_content = f.read()
        attachment_mime = 'application/pdf'
    email = EmailMessage(
        subject=subject,
        body=html_message,
        from_email=from_email,
        to=list(recipients) if recipients else [],
    )
    email.content_subtype = 'html'
    if attachment_name and attachment_content and attachment_mime:
        email.attach(attachment_name, attachment_content, attachment_mime)
    email.send()
    logger.info(f'Correo enviado: {subject}')


@huey.periodic_task(crontab(minute=0, hour=4), name='podar_notificaciones')
def podar_notificaciones():
    """Poda diaria de avisos (cambio 026, fase 4).

    Huey corre con `utc=True`, asi que las 4 UTC son la medianoche en
    `America/Havana`: la poda no compite con nadie y no toca el badge de un
    admin que este mirando el panel a media manana.

    Corre con el sistema apagado tambien (`NOTIFICATIONS_ENABLED=False`): limpiar
    datos viejos no es una funcion visible que haya que apagar, y dejarla
    detenida solo hace crecer la tabla.
    """
    from apps.core.notifications import prune_notifications

    deleted = prune_notifications()
    if any(deleted.values()):
        logger.info(
            'Poda de notificaciones: %s vencidas, %s leidas, %s no leidas',
            deleted['expired'],
            deleted['read'],
            deleted['unread'],
        )
    return deleted
