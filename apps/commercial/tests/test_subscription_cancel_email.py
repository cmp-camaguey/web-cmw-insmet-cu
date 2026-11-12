"""Aviso al cliente cuando se anula su suscripción.

De los tres cambios de estado, solo la anulación necesita correo propio: `pending`
ya manda la factura y `paid` ya manda el certificado.
"""

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.commercial.models import Invoice, InvoiceItem, Service, ServiceSubscription
from apps.commercial.tests.factories import natural_customer
from apps.core.models import SiteConfiguration
from config.huey import huey

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


class SubscriptionCancelEmailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        SiteConfiguration.objects.update_or_create(defaults={'maintenance_mode': False})
        cls.staff = User.objects.create_superuser(
            'staff_anula', 'staff@anula.test', 'pw', first_name='Sta', last_name='Ff'
        )
        cls.client_user = User.objects.create_user(
            'cliente_anula',
            'cliente@anula.test',
            'pw',
            first_name='Cli',
            last_name='Ente',
        )
        cls.customer = natural_customer(cls.client_user)
        cls.service = Service.objects.create(
            user=cls.staff,
            title='Pronóstico por temporada',
            summary='Resumen',
            service_type=Service.COMMERCIAL,
            service_category='pronostico',
            price=100,
            code='ANULA-1',
        )

    def setUp(self):
        # `enviar_correo_anulacion` encola por Huey: sin `immediate` la tarea
        # queda en la cola y `mail.outbox` nunca la ve.
        self._old_immediate = huey.immediate
        huey.immediate = True
        mail.outbox = []
        self.client.force_login(self.staff)

    def tearDown(self):
        huey.immediate = self._old_immediate

    def _subscription(self, *, payment_status='requested', customer=None):
        return ServiceSubscription.objects.create(
            customer=customer or self.customer,
            service=self.service,
            start_date=timezone.now().date(),
            payment_status=payment_status,
            quantity=1,
        )

    def _anular(self, subscription):
        return self.client.post(
            reverse('commercial:suscripcion_cancel', args=[subscription.uuid]), follow=True
        )

    @override_settings(EMAIL_BACKEND=LOCMEM, SITE_URL='http://testserver/')
    def test_la_anulacion_avisa_al_cliente(self):
        subscription = self._subscription()

        response = self._anular(subscription)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['cliente@anula.test'])
        self.assertIn(self.service.title, mail.outbox[0].subject)
        self.assertIn(self.service.title, mail.outbox[0].body)
        self.assertIn('quedó anulada', mail.outbox[0].body)
        self.assertIn(str(self.service.uuid), mail.outbox[0].body)

    @override_settings(EMAIL_BACKEND=LOCMEM, SITE_URL='http://testserver/')
    def test_un_cliente_sin_correo_no_rompe_la_anulacion(self):
        """El cliente creado desde el panel puede no tener cuenta ni correo."""
        sin_cuenta = natural_customer(username='sin-correo')
        User.objects.filter(pk=sin_cuenta.user_id).update(email='')
        subscription = self._subscription(customer=sin_cuenta)

        response = self._anular(subscription)

        self.assertEqual(response.status_code, 200)
        subscription.refresh_from_db()
        self.assertFalse(subscription.record_active)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(EMAIL_BACKEND=LOCMEM, SITE_URL='http://testserver/')
    def test_una_anulacion_rechazada_no_avisa_de_nada(self):
        """Con factura emitida la anulación se rechaza: no hubo hecho que avisar."""
        subscription = self._subscription()
        invoice = Invoice.objects.create(
            customer=self.customer,
            number='F-ANULA-1',
            amount=100,
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            subscription=subscription,
            descripcion=self.service.title,
            cantidad=1,
            precio=100,
            importe=100,
        )

        self._anular(subscription)

        subscription.refresh_from_db()
        self.assertTrue(subscription.record_active)
        self.assertEqual(len(mail.outbox), 0)
