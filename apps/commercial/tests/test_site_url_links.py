"""Los correos comerciales (factura y certificado) enlazan con `SITE_URL`.

El host del `request` es el host por el que el operador entró: la IP del
servidor en la red interna o `localhost`. El cliente que recibe el correo está
fuera de esa red, así que un enlace armado con ese host no abre. `SITE_URL` es
la URL pública del sitio y es lo único que puede viajar en un correo.
"""

from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.commercial.models import (
    Certificate,
    Invoice,
    InvoiceItem,
    Service,
    ServiceSubscription,
)
from apps.commercial.tests.factories import natural_customer
from apps.commercial.views.invoice_utils import enviar_correo_factura
from apps.commercial.views.subscriptions import enviar_correo_certificado
from apps.core.models import SiteConfiguration

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'
PUBLIC_SITE_URL = 'https://cmw.insmet.cu/'
INTERNAL_HOST = '10.0.0.5:8000'
# El host interno tiene que estar permitido: si no, Django lo rechaza antes de
# que el código llegue a armar el enlace, y el test probaría el `ALLOWED_HOSTS`
# en vez del contrato de `SITE_URL`.
ALLOWED_HOSTS = ['testserver', '10.0.0.5']

# El comando importa `require_pdf_renderer` por nombre, así que el preflight se
# simula donde se usa (el comando), no donde está definido.
PREFLIGHT = 'apps.commercial.management.commands.send_test_invoice.require_pdf_renderer'


def _request_desde_la_red_interna():
    return RequestFactory().post('/commercial/factura/', HTTP_HOST=INTERNAL_HOST)


class EnlaceDelCorreoDeFacturaTests(TestCase):
    """El correo de la factura apunta al sitio público, no al host del operador."""

    @classmethod
    def setUpTestData(cls):
        cls.customer = natural_customer()
        cls.invoice = Invoice.objects.create(
            customer=cls.customer,
            number='INV-SITE-URL',
            amount=Decimal('100.00'),
        )
        InvoiceItem.objects.create(
            invoice=cls.invoice,
            descripcion='Item',
            cantidad=1,
            precio=Decimal('100.00'),
        )

    @override_settings(SITE_URL=PUBLIC_SITE_URL, EMAIL_BACKEND=LOCMEM, ALLOWED_HOSTS=ALLOWED_HOSTS)
    def test_el_correo_de_factura_enlaza_con_site_url(self):
        request = _request_desde_la_red_interna()
        enviada = enviar_correo_factura(self.invoice, self.customer, request=request)

        self.assertTrue(enviada, 'el correo de la factura tiene que salir')
        self.assertEqual(len(mail.outbox), 1)
        cuerpo = mail.outbox[0].body
        esperado = f'{PUBLIC_SITE_URL}{reverse("commercial:suscripcion_list").lstrip("/")}'
        self.assertIn(esperado, cuerpo)
        self.assertNotIn(INTERNAL_HOST, cuerpo)


class EnlaceDelCorreoDeCertificadoTests(TestCase):
    """El correo del certificado apunta al sitio público, no al host del operador."""

    @classmethod
    def setUpTestData(cls):
        cls.customer = natural_customer()
        service = Service.objects.create(
            user=cls.customer.user,
            title='Servicio de prueba',
            summary='s',
            service_type='commercial',
            code='SITEURL1',
            price=Decimal('10.00'),
        )
        cls.subscription = ServiceSubscription.objects.create(
            customer=cls.customer,
            service=service,
            start_date=timezone.now() - timedelta(days=1),
            payment_status='paid',
        )
        Certificate.objects.create(
            subscription=cls.subscription,
            pdf=SimpleUploadedFile('cert.pdf', b'%PDF-1.4 test', 'application/pdf'),
        )

    @override_settings(SITE_URL=PUBLIC_SITE_URL, EMAIL_BACKEND=LOCMEM, ALLOWED_HOSTS=ALLOWED_HOSTS)
    def test_el_correo_de_certificado_enlaza_con_site_url(self):
        request = _request_desde_la_red_interna()
        enviado = enviar_correo_certificado(self.subscription, request=request)

        self.assertTrue(enviado, 'el correo del certificado tiene que salir')
        self.assertEqual(len(mail.outbox), 1)
        cuerpo = mail.outbox[0].body
        esperado = f'{PUBLIC_SITE_URL}{reverse("commercial:suscripcion_list").lstrip("/")}'
        self.assertIn(esperado, cuerpo)
        self.assertNotIn(INTERNAL_HOST, cuerpo)


class BaseUrlDeLaFacturaTests(TestCase):
    """Cómo se resuelve la URL base del correo de la factura.

    Tres casos, y los tres tienen que quedar explícitos:

    1. Sin `request` y sin `base_url` explícito: manda `SITE_URL`.
    2. Con `base_url` explícito: manda el explícito (quien lo pasa, sabe).
    3. Con `request`: `SITE_URL` igual, porque el host del request es el del
       operador, no el del cliente.
    """

    @classmethod
    def setUpTestData(cls):
        cls.customer = natural_customer()
        cls.invoice = Invoice.objects.create(
            customer=cls.customer,
            number='INV-BASE-URL',
            amount=Decimal('100.00'),
        )
        InvoiceItem.objects.create(
            invoice=cls.invoice,
            descripcion='Item',
            cantidad=1,
            precio=Decimal('100.00'),
        )

    @override_settings(SITE_URL=PUBLIC_SITE_URL, EMAIL_BACKEND=LOCMEM, ALLOWED_HOSTS=ALLOWED_HOSTS)
    def test_sin_request_usa_site_url(self):
        enviada = enviar_correo_factura(self.invoice, self.customer)

        self.assertTrue(enviada)
        cuerpo = mail.outbox[0].body
        esperado = f'{PUBLIC_SITE_URL}{reverse("commercial:suscripcion_list").lstrip("/")}'
        self.assertIn(esperado, cuerpo)

    @override_settings(SITE_URL=PUBLIC_SITE_URL, EMAIL_BACKEND=LOCMEM, ALLOWED_HOSTS=ALLOWED_HOSTS)
    def test_un_base_url_explicito_manda_sobre_site_url(self):
        """El `base_url` explícito se respeta.

        Es lo que hace el camino encolado (`invoices.py` → tarea de Huey → esta
        función). Mientras ese llamador pase el host del `request`, el enlace
        seguirá saliendo por ahí: este test deja constancia de que el override
        existe y funciona, no de que el problema esté resuelto.
        """
        enviada = enviar_correo_factura(
            self.invoice,
            self.customer,
            base_url='https://sitio-explicito.example/',
        )

        self.assertTrue(enviada)
        cuerpo = mail.outbox[0].body
        esperado = (
            f'https://sitio-explicito.example/{reverse("commercial:suscripcion_list").lstrip("/")}'
        )
        self.assertIn(esperado, cuerpo)
        self.assertNotIn(PUBLIC_SITE_URL, cuerpo)


class SendTestInvoiceUsaSiteUrlTests(TestCase):
    """El comando de prueba entrega la URL canónica a la tarea de Huey."""

    @classmethod
    def setUpTestData(cls):
        cls.customer = natural_customer()
        cls.invoice = Invoice.objects.create(
            customer=cls.customer,
            number='CMD-SITE-URL',
            amount=Decimal('50.00'),
        )

    @override_settings(SITE_URL=PUBLIC_SITE_URL)
    def test_el_comando_manda_site_url_a_la_tarea(self):
        with (
            mock.patch(PREFLIGHT, return_value=None),
            mock.patch('apps.core.tasks.generate_invoice_pdf_and_email_task') as tarea,
        ):
            out, err = StringIO(), StringIO()
            call_command(
                'send_test_invoice',
                str(self.invoice.uuid),
                '--encolar',
                stdout=out,
                stderr=err,
            )

        self.assertTrue(tarea.called, 'el comando tiene que encolar la tarea')
        self.assertEqual(tarea.call_args.args[1], PUBLIC_SITE_URL)


class EncoladoUsaSiteUrlTests(TestCase):
    """Las vistas que encolan la factura pasan la URL pública, no la del request.

    `generate_invoice_pdf_and_email_task` acepta un `site_url` explícito y lo
    respeta. Si la vista lo arma con `request.build_absolute_uri('/')`, el
    correo que arma el worker vuelve al host interno por el que entró el
    operador.
    """

    @classmethod
    def setUpTestData(cls):
        SiteConfiguration.objects.get_or_create(defaults={'maintenance_mode': False})
        cls.staff = User.objects.create_user(
            'cola.staff', 'cola@example.com', 'pass', first_name='Cola', last_name='Staff'
        )
        ct = ContentType.objects.get_for_model(Invoice)
        # `change_invoice` habilita el reintento; `view_invoice` hace falta
        # porque la vista redirige al listado y `follow=True` lo atraviesa.
        perms = ct.permission_set
        cls.staff.user_permissions.add(
            perms.get(codename='change_invoice'), perms.get(codename='view_invoice')
        )
        cls.invoice = Invoice.objects.create(
            customer=natural_customer(),
            number='CMD-COLA',
            amount=Decimal('25.00'),
        )
        cls.url = reverse('commercial:factura_retry_pdf', args=[cls.invoice.uuid])

    @override_settings(SITE_URL=PUBLIC_SITE_URL, ALLOWED_HOSTS=ALLOWED_HOSTS)
    def test_reintentar_pdf_encola_con_site_url(self):
        self.client.force_login(self.staff)
        with mock.patch(
            'apps.commercial.views.invoices.generate_invoice_pdf_and_email_task'
        ) as tarea:
            response = self.client.post(self.url, HTTP_HOST=INTERNAL_HOST, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(tarea.call_args.args[1], PUBLIC_SITE_URL)
