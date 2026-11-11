"""Los logs de operador no pueden decir `None` donde va el nombre del cliente.

`Customer.company_name` sólo existe para personas jurídicas: en una natural es
NULL, y los logs que lo leían directo dejaban la palabra `None` en el registro
de actividad. Para el operador eso es pista falsa: al auditar una suscripción
eliminada no hay forma de saber a quién se le quitó el servicio.

Cada test dispara la operación real y mira lo que quedó en `ActivityLog`.
"""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.commercial.forms.invoice import InvoiceForm
from apps.commercial.models import Service, ServiceSubscription
from apps.commercial.tests.factories import make_user, natural_customer
from apps.commercial.tests.test_views import disable_maintenance_mode
from apps.commercial.views.invoices import InvoiceCreateView
from apps.core.models import ActivityLog
from apps.core.tests.base import FileHandlingTestCase


class OperatorLogCustomerNameTests(FileHandlingTestCase):
    """Cada log que nombra a un cliente nombra a una persona natural."""

    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.admin = User.objects.create_superuser(
            'operador',
            'operador@example.com',
            'pass',
            first_name='Ope',
            last_name='Rador',
        )
        cls.customer = natural_customer(
            make_user('cliente.natural', first_name='Ana', last_name='Norte')
        )
        cls.service = Service.objects.create(
            user=cls.admin,
            title='Servicio Log',
            summary='S',
            service_type='commercial',
            code='LOG1',
            price=Decimal('25.00'),
        )
        cls.subscription = ServiceSubscription.objects.create(
            customer=cls.customer,
            service=cls.service,
            start_date=timezone.now(),
            payment_status='requested',
        )

    # --- helpers ---------------------------------------------------------

    def _logs(self):
        return list(ActivityLog.objects.order_by('id'))

    def _ultimo_log(self):
        return ActivityLog.objects.order_by('-id').first()

    def _afirmar_nombre_en_los_logs(self):
        """Ningún log dice `None` y todos nombran a la persona natural."""
        logs = self._logs()
        self.assertTrue(logs, 'la operación no dejó ningún log de actividad')
        for log in logs:
            self.assertNotIn('None', log.message)
            self.assertIn('Ana Norte', log.message)

    def _request(self, data=None):
        """Request con usuario y storage de mensajes, para las vistas sin dispatch."""
        from django.contrib.messages.storage.fallback import FallbackStorage

        request = RequestFactory().post('/', data or {})
        request.user = self.admin
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def _afirmar_mensajes(self, response):
        mensajes = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(mensajes, 'la operación no dejó ningún mensaje al usuario')
        for mensaje in mensajes:
            self.assertNotIn('None', mensaje)
        self.assertTrue(any('Ana Norte' in mensaje for mensaje in mensajes))

    # --- suscripciones ----------------------------------------------------

    def test_log_de_suscripcion_creada(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse('commercial:suscripcion_create'),
            {
                'customer': self.customer.pk,
                'service': self.service.pk,
                'start_date': '2026-03-01',
                'quantity': 1,
                'payment_method': 'qr',
            },
        )
        self._afirmar_nombre_en_los_logs()

    def test_log_de_suscripcion_actualizada(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse('commercial:suscripcion_update', args=[self.subscription.uuid]),
            {
                'customer': self.customer.pk,
                'service': self.service.pk,
                'start_date': '2026-03-02',
                'quantity': 2,
                'payment_method': 'transfer',
            },
        )
        self._afirmar_nombre_en_los_logs()

    def test_log_de_suscripcion_desactivada(self):
        from config.huey import huey

        self.client.force_login(self.admin)
        # La anulación avisa al cliente por correo (S5): con la cola real el
        # envío queda pendiente y rompe los tests que después cuentan la cola.
        old = huey.immediate
        huey.immediate = True
        try:
            self.client.post(
                reverse('commercial:suscripcion_cancel', args=[self.subscription.uuid]),
            )
        finally:
            huey.immediate = old
        self._afirmar_nombre_en_los_logs()

    def test_log_y_mensaje_de_suscripcion_eliminada_fisicamente(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('commercial:suscripcion_delete', args=[self.subscription.uuid]),
        )
        self._afirmar_nombre_en_los_logs()
        self._afirmar_mensajes(response)

    # --- clientes ---------------------------------------------------------

    def test_log_de_cliente_desactivado(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse('commercial:cliente_delete', args=[self.customer.uuid]),
        )
        self._afirmar_nombre_en_los_logs()

    def test_log_y_mensaje_de_cliente_eliminado_fisicamente(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('commercial:cliente_hard_delete', args=[self.customer.uuid]),
        )
        self._afirmar_nombre_en_los_logs()
        self._afirmar_mensajes(response)

    # --- facturación ------------------------------------------------------

    def test_logs_de_factura_por_lote(self):
        view = InvoiceCreateView()
        view.request = self._request()
        with patch('apps.commercial.views.invoices.generate_invoice_pdf_and_email_task'):
            view.process_batch_invoice(self.customer, 'REG-001', [self.subscription])
        self._afirmar_nombre_en_los_logs()

    def test_logs_de_factura_manual(self):
        datos = {
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-service': self.service.pk,
            'items-0-start_date': '2026-03-01',
            'items-0-cantidad': '3',
            'items-0-precio': '25.00',
            'items-0-codigo': '',
        }
        form = InvoiceForm({'customer': self.customer.pk, 'commercial_registry': 'REG-002'})
        self.assertTrue(form.is_valid(), form.errors)

        view = InvoiceCreateView()
        view.request = self._request(datos)
        with patch('apps.commercial.views.invoices.generate_invoice_pdf_and_email_task'):
            view.process_manual_invoice(form, self.customer, 'REG-002', None)
        self._afirmar_nombre_en_los_logs()

    # --- contratos y certificados ----------------------------------------

    def test_log_de_contrato_creado(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse('commercial:contrato_create'),
            {
                'customer': self.customer.pk,
                'number': '2026-0001',
                'start_date': '2026-03-01',
                'validity_years': 2,
            },
        )
        self._afirmar_nombre_en_los_logs()

    def test_log_de_certificado_creado(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse('commercial:certificado_create'),
            {
                'subscription': self.subscription.pk,
                'pdf': SimpleUploadedFile(
                    'cert.pdf',
                    b'%PDF-1.4 certificado de prueba',
                    content_type='application/pdf',
                ),
            },
        )
        self._afirmar_nombre_en_los_logs()

    def test_el_log_dice_el_nombre_y_no_el_campo_crudo(self):
        """El log tiene que distinguir el nombre mostrado del campo crudo."""
        self.client.force_login(self.admin)
        self.client.post(
            reverse('commercial:cliente_delete', args=[self.customer.uuid]),
        )
        log = self._ultimo_log()
        self.assertIn('Cliente desactivado: Ana Norte.', log.message)
        self.assertNotIn('company_name', log.message)

    def test_la_facturacion_no_deja_trabajo_en_la_cola_compartida(self):
        """La cola de Huey es compartida por toda la corrida de tests.

        Encolar de verdad deja tareas vivas y rompe los tests que afirman que
        la cola quedó vacía, así que la facturación se prueba con la tarea
        mockeada: lo que se verifica acá es el log, no el PDF.
        """
        from config.huey import huey

        antes = huey.pending_count() + huey.scheduled_count()
        view = InvoiceCreateView()
        view.request = self._request()
        with patch('apps.commercial.views.invoices.generate_invoice_pdf_and_email_task'):
            view.process_batch_invoice(self.customer, 'REG-001', [self.subscription])
        self.assertEqual(huey.pending_count() + huey.scheduled_count(), antes)
