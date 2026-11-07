"""Avisos operativos de la entrega de facturas (cambio 026, fase 2).

El PDF y el correo fallan por razones distintas, así que son dos hechos
distintos: una factura puede tener el aviso del PDF y el del correo sin que uno
tape al otro, y el reintento que vuelve a fallar no deja una fila nueva.
"""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.commercial.models import Invoice
from apps.commercial.tests.factories import natural_customer
from apps.commercial.views.invoice_utils import enviar_correo_factura, marcar_pdf
from apps.core.models import Notification


class InvoiceNotificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('fact-admin', 'fact-admin@example.com', 'x')
        cls.user = User.objects.create_user('fact-cliente', 'cliente@example.com', 'x')
        cls.customer = natural_customer(cls.user)
        cls.invoice = Invoice.objects.create(
            customer=cls.customer,
            number='INV-AVISO',
            amount=Decimal('100.00'),
        )

    def test_pdf_fallido_crea_un_aviso_por_admin(self):
        marcar_pdf(self.invoice, error=RuntimeError('WeasyPrint caído'))

        aviso = Notification.objects.get()
        self.assertEqual(aviso.source, 'invoice')
        self.assertEqual(aviso.severity, Notification.Severity.ERROR)
        self.assertEqual(aviso.recipient, self.admin)
        self.assertIn('PDF', aviso.title)
        self.assertIn('WeasyPrint caído', aviso.body)
        self.assertEqual(aviso.url, reverse('commercial:factura_list'))

    def test_reintento_que_vuelve_a_fallar_no_duplica_el_aviso(self):
        marcar_pdf(self.invoice, error=RuntimeError('caído'))
        marcar_pdf(self.invoice, error=RuntimeError('caído otra vez'))

        self.assertEqual(Notification.objects.count(), 1)

    def test_pdf_ok_no_crea_aviso(self):
        marcar_pdf(self.invoice)

        self.assertEqual(self.invoice.pdf_status, Invoice.PdfStatus.READY)
        self.assertEqual(Notification.objects.count(), 0)

    def test_pdf_y_correo_fallidos_son_dos_avisos(self):
        marcar_pdf(self.invoice, error=RuntimeError('caído'))

        with patch('apps.commercial.views.invoice_utils.EmailMessage') as email:
            email.return_value.send.side_effect = RuntimeError('SMTP caído')
            self.assertFalse(enviar_correo_factura(self.invoice, self.customer))

        self.assertEqual(
            sorted(Notification.objects.values_list('title', flat=True)),
            ['Factura INV-AVISO: falló el PDF', 'Factura INV-AVISO: falló el correo'],
        )

    def test_correo_sin_destinatario_avisa_igual(self):
        self.customer.user.email = ''
        self.customer.user.save(update_fields=['email'])

        self.assertFalse(enviar_correo_factura(self.invoice, self.customer))

        aviso = Notification.objects.get()
        self.assertIn('correo', aviso.title)
        self.assertIn('no tiene correo registrado', aviso.body)
