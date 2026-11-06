import logging
import os
from decimal import ROUND_HALF_UP, Decimal
from email.mime.image import MIMEImage
from functools import lru_cache

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from weasyprint import HTML

from apps.commercial.models import (
    MESES,
    Contract,
    Customer,
    Invoice,
    InvoiceCostAllocation,
    Service,
)
from apps.core.models import CompanySettings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def require_pdf_renderer():
    """Se niega a generar un PDF sin que el motor de renderizado funcione.

    `WeasyPrint` es una biblioteca pura de Python, pero necesita la pila
    del sistema (Pango, Harfbuzz, GdkPixbuf) para convertir HTML a PDF. Si
    esa pila falta, el primer `write_pdf()` falla con un error profundo,
    no con un `ImportError` claro. Este chequeo previene que el worker
    de Huey intente generar facturas en ese estado y deja constancia de
    qué dependencias del sistema faltan, antes de tocar ningún archivo.

    Ejecuta una prueba real de renderizado (no solo una importación): al
    hacerlo una única vez por proceso (cacheado) se paga el costo de
    arranque de Pango como mucho una vez y, lo más importante, el chequeo
    ocurre ANTES de que se escriba un PDF a medio crear.
    """

    try:
        HTML(string='<p>ok</p>').write_pdf()
    except Exception as exc:
        raise RuntimeError(
            'No se puede generar el PDF de la factura: faltan bibliotecas del '
            'sistema necesarias para WeasyPrint (Pango/Harfbuzz).\n'
            '  Debian/Ubuntu: sudo apt install libpango-1.0-0 '
            'libpangoft2-1.0-0 libharfbuzz0b libgdk-pixbuf-2.0-0\n'
            '  Otras plataformas: consulte la documentación de WeasyPrint '
            'para las dependencias del sistema.'
        ) from exc


def _centro_de_costo_de_codigo(codigo):
    """Centro de costo embebido en el prefijo del código de servicio.

    Los códigos reales empiezan por el centro: `700501072507005` es
    `700.50107 | 2507 | 005`. Se devuelve `None` cuando el código es demasiado
    corto para contenerlo, porque inventar un centro sería peor que no tener
    ninguno: el operador lo carga a mano.
    """
    codigo = (codigo or '').strip()
    if len(codigo) < 8 or not codigo[:8].isdigit():
        return None
    return f'{codigo[:3]}.{codigo[3:8]}'


def centros_de_costo_conocidos():
    """Centros de costo que la pantalla de factura ofrece para elegir.

    Es una **unión** de dos fuentes, no una lista cerrada:

    1. los centros derivados de los códigos de los servicios comerciales
       (`700501072507005` -> `700.50107`), que es lo único derivable, y
    2. los `codigo` que ya aparecen en imputaciones guardadas, que es todo lo
       que alguien escribió a mano alguna vez.

    La segunda fuente no es redundante: los repartos reales de las facturas 276
    (25/65/10) y 279 (90/10) usan centros que **no salen de ningún código de
    servicio** (ver el docstring de `InvoiceCostAllocation`), así que no se
    pueden derivar. Por eso esto alimenta un `<datalist>` y no un `<select>`: el
    catálogo reconoce lo conocido sin cerrar la puerta a un centro nuevo, que
    sigue siendo válido si se escribe.

    Un código de servicio corto o no numérico no aporta centro: no se inventa
    ninguno a partir de un prefijo que no lo contiene. La lista sale ordenada y
    sin duplicados.
    """
    centros = set()
    for codigo in Service.objects.filter(service_type=Service.COMMERCIAL).values_list(
        'code', flat=True
    ):
        centro = _centro_de_costo_de_codigo(codigo)
        if centro:
            centros.add(centro)
    for codigo in InvoiceCostAllocation.objects.values_list('codigo', flat=True).distinct():
        centro = (codigo or '').strip()
        if centro:
            centros.add(centro)
    return sorted(centros)


def _prefill_cost_allocations_from_items(items):
    """Reparto inicial a partir de los centros de los ítems facturados.

    Ojo con lo que esto NO es: el reparto real de las facturas del CMP no sale de
    los ítems. La factura 276 tiene un único ítem repartido en tres centros
    (25/65/10), y ningún cálculo sobre montos produce tres porcentajes de una
    sola línea. El reparto es una decisión del operador; esto es sólo el punto de
    partida que le ahorra escribir los códigos.

    Lo que sí sale de los ítems es el centro, porque viaja en el prefijo. El
    reparto en partes iguales está ajustado para sumar EXACTAMENTE 100.00 (con
    tres centros quedan 33.33/33.33/33.34, porque el residuo del redondeo se
    absorbe en la última fila) ya que un reparto que no suma 100 es justo lo que
    el formset después rechaza.
    """
    centros = []
    for item in items:
        centro = _centro_de_costo_de_codigo(getattr(item, 'codigo', None))
        if centro and centro not in centros:
            centros.append(centro)

    if not centros:
        return []

    base = (Decimal('100') / len(centros)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    reparto = [{'codigo': centro, 'porcentaje': base} for centro in centros]
    # El residuo del redondeo se absorbe en la última fila para que la suma sea
    # exacta en cents y no 99.99.
    reparto[-1]['porcentaje'] += Decimal('100.00') - sum(
        (fila['porcentaje'] for fila in reparto), Decimal('0')
    )
    return reparto


def _contrato_de_factura(invoice):
    """Devuelve el contrato vigente para el cliente y el período facturado."""
    if invoice is None:
        return None
    if invoice.customer_id is None:
        return None
    # `period_start` es `DateField` y `issue_date.date()` normaliza el
    # `DateTimeField`, así que `fecha` ya es siempre una `date`.
    fecha = invoice.period_start or invoice.issue_date.date()
    return Contract.objects.vigente_para(invoice.customer, fecha)


def _datos_proveedor(company, contract):

    return {
        'nombre': company.nombre,
        'direccion': company.direccion,
        'codigo_reeup': company.codigo_reeup,
        'nit': company.nit,
        'cuenta_bancaria': company.cuenta_bancaria,
        'agencia_bancaria': company.agencia_bancaria,
        'telefonos': company.telefonos,
        'registro_comercial': company.registro_comercial,
        'no_contrato': contract.number if contract else '',
        'fecha_contrato': contract.start_date.strftime('%d/%m/%Y') if contract else '',
    }


def _datos_cliente(customer):

    if customer is None:
        return {
            'es_juridica': False,
            'nombre': '',
            'documento_identidad': '',
            'direccion': '',
            'codigo_reeup': '',
            'nit': '',
            'cuenta_bancaria': '',
            'agencia_bancaria': '',
            'telefonos': '',
        }

    es_juridica = customer.client_type == Customer.ClientType.JURIDICA
    user = customer.user
    nombre_persona = user.get_full_name() if user else ''
    if es_juridica:
        nombre = customer.company_name or nombre_persona
    else:
        nombre = nombre_persona or customer.company_name
    if not nombre and user:
        nombre = user.username

    return {
        'es_juridica': es_juridica,
        'nombre': nombre or '',
        'documento_identidad': customer.identity_document or '',
        'direccion': customer.address,
        'codigo_reeup': customer.reeup or '',
        'nit': customer.nit or '',
        'cuenta_bancaria': customer.account or '',
        'agencia_bancaria': customer.agency_bank or '',
        'telefonos': customer.phone or '',
    }


def _fecha_en_espanol(fecha):
    """`dd de <mes> del aaaa`, con el mes en español y sin depender del locale.

    `strftime('%B')` usa el locale del proceso (C en producción), no el
    `LANGUAGE_CODE='es-mx'` de la app: el PDF salía "11 de October del 2026".
    La tabla `MESES` es la misma que usa la frase del período.
    """
    return f'{fecha.day:02d} de {MESES[fecha.month - 1]} del {fecha.year}'


def generate_invoice_pdf_standalone(invoice, customer, items):
    company = CompanySettings.get_instance()
    context = {
        'cost_allocations': [
            {'codigo': asignacion.codigo, 'porcentaje': asignacion.porcentaje}
            for asignacion in invoice.cost_allocations.all()
        ],
        'numero_factura': invoice.number,
        'fecha_facturacion': _fecha_en_espanol(invoice.issue_date),
        'periodo_facturacion': invoice.period_label,
        'cliente': _datos_cliente(customer),
        'proveedor': _datos_proveedor(company, _contrato_de_factura(invoice)),
        'items': [
            {
                'codigo': item.codigo,
                'descripcion': item.descripcion,
                'cantidad': item.cantidad,
                'unidad_medida': item.unidad_medida,
                'precio': item.precio,
                'importe': item.importe,
            }
            for item in items
        ],
        'total': float(invoice.amount),
        'current_year': timezone.now().year,
    }
    require_pdf_renderer()
    html_string = render_to_string('pages/commercial/invoice/template.html', context)
    pdf_bytes = HTML(string=html_string).write_pdf()
    filename = f'factura_{invoice.id}.pdf'
    invoice.pdf.save(filename, ContentFile(pdf_bytes))
    return pdf_bytes


def _notificar_fallo_factura(invoice, kind):
    """Avisa a los superusuarios que un paso de entrega de la factura falló.

    `kind` es `'pdf'` o `'email'`, y entra en la clave de deduplicación: un
    reintento que vuelve a fallar no deja una fila nueva, pero un PDF roto y un
    correo roto de la misma factura sí son dos hechos distintos.

    Se llama desde las ramas `FAILED`, que son las que conocen el hecho. No hay
    señal sobre `Invoice` a propósito: el PDF fallido también deja una tarea en
    `ERROR` y dos avisos del mismo hecho es ruido (ver `design.md` §3).

    No propaga excepciones: la factura ya tiene su estado y su error guardados,
    y esa es la información que importa.
    """
    from apps.core.models import Notification
    from apps.core.notifications import make_dedupe_key, notify_superusers

    etiqueta = {'pdf': 'el PDF', 'email': 'el correo'}.get(kind, kind)
    detalle = invoice.pdf_error if kind == 'pdf' else invoice.email_error
    try:
        notify_superusers(
            source='invoice',
            severity=Notification.Severity.ERROR,
            title=f'Factura {invoice.number or invoice.uuid}: falló {etiqueta}',
            body=(detalle or '')[:500],
            # No hay vista de detalle de factura: la lista es donde se
            # reintenta el PDF y el reenvío del correo, que es la acción útil.
            url=reverse('commercial:factura_list'),
            dedupe_key=make_dedupe_key('invoice', invoice.uuid, kind),
        )
    except Exception:
        logger.exception('No se pudo crear el aviso de factura %s (%s)', invoice.uuid, kind)


def marcar_pdf(invoice, error=None):

    invoice.pdf_status = Invoice.PdfStatus.FAILED if error else Invoice.PdfStatus.READY
    invoice.pdf_error = str(error) if error else None
    invoice.save(update_fields=['pdf_status', 'pdf_error'])
    if error:
        _notificar_fallo_factura(invoice, 'pdf')
    return invoice


def enviar_correo_factura(invoice, customer, request=None, base_url=None):

    if not customer.user or not customer.user.email:
        invoice.email_status = Invoice.EmailStatus.FAILED
        invoice.email_error = 'El cliente no tiene correo registrado.'
        invoice.save(update_fields=['email_status', 'email_error'])
        _notificar_fallo_factura(invoice, 'email')
        return False

    first_item = invoice.items.first()
    subscription = first_item.subscription if first_item else None

    if subscription and subscription.payment_method == 'qr':
        template = 'pages/commercial/emails/factura_qr.html'
    else:
        template = 'pages/commercial/emails/factura.html'

    company = CompanySettings.get_instance()

    if base_url is None:
        # La URL sale de `SITE_URL` y NO del `request`: el cliente no está en la
        # red interna, y el host por el que el operador entró no es el público.
        base_url = settings.SITE_URL

    # El cliente no tiene `view_invoice` (facturación le queda en 403), así que
    # el enlace apunta a «Mis Suscripciones», cubierta por `view_subscription`
    # —el permiso que sí tiene el grupo `Clientes`—. La clave del contexto se
    # mantiene: las tres plantillas de correo la leen con ese nombre.
    listado_url = base_url + reverse('commercial:suscripcion_list').lstrip('/')

    context = {
        'invoice': invoice,
        'customer': customer,
        'subscription': subscription,
        'payment_method': subscription.get_payment_method_display() if subscription else '',
        'company': company,
        'index_url': base_url,
        'listado_url': listado_url,
        'current_year': timezone.now().year,
    }

    html_content = render_to_string(template, context)
    # `company_name` es NULL en una persona natural y la f-string lo escribía
    # literal como `None`. `display_name` es el mismo criterio que usan los
    # cuerpos de las plantillas: cae al nombre del usuario y luego al username.
    subject = f'Factura {invoice.number} - {customer.display_name}'

    email = EmailMessage(
        subject=subject,
        body=html_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[customer.user.email],
    )
    email.content_subtype = 'html'

    if invoice.pdf and invoice.pdf.storage.exists(invoice.pdf.name):
        with invoice.pdf.storage.open(invoice.pdf.name, 'rb') as f:
            email.attach(
                f'factura_{invoice.number}_{invoice.issue_date:%Y-%m-%d}.pdf',
                f.read(),
                'application/pdf',
            )

    if subscription and subscription.payment_method == 'qr':
        from django.contrib.staticfiles import finders

        qr_path = finders.find('dist/img/QR/QR.png')
        if not qr_path:
            qr_path = os.path.join(settings.STATIC_ROOT, 'dist/img/QR/QR.png')
        if os.path.exists(qr_path):
            with open(qr_path, 'rb') as f:
                qr_bytes = f.read()
            # El PNG viaja una sola vez, como parte MIME inline con
            # cabecera `Content-ID`: es la única forma de que el
            # `cid:qr_pago.png` del cuerpo resuelva, porque
            # `email.attach(nombre, bytes, mimetype)` crea un adjunto SIN
            # `Content-ID`. No se añade además como adjunto descargable,
            # para no duplicar el archivo en el correo. `_subtype`
            # explícito porque Python 3.14 ya no incluye `imghdr` para
            # adivinarlo del contenido.
            parte_inline = MIMEImage(qr_bytes, _subtype='png')
            parte_inline.add_header('Content-ID', '<qr_pago.png>')
            parte_inline.add_header('Content-Disposition', 'inline', filename='qr_pago.png')
            email.attach(parte_inline)

    try:
        email.send()
        invoice.email_status = Invoice.EmailStatus.SENT
        invoice.email_error = None
        invoice.save(update_fields=['email_status', 'email_error'])
        logger.info(f'Factura {invoice.number} enviada a {customer.user.email}')
        return True
    except Exception as e:
        logger.error(f'Error enviando factura {invoice.number}: {e}')
        invoice.email_status = Invoice.EmailStatus.FAILED
        invoice.email_error = str(e)
        invoice.save(update_fields=['email_status', 'email_error'])
        _notificar_fallo_factura(invoice, 'email')
        return False
