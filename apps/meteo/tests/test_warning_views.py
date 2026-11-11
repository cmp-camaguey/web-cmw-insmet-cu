from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import SiteConfiguration
from apps.core.tests.base import add_confirmed_subscriber, get_default_list
from apps.meteo.models import Warning
from config.huey import huey

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


def _disable_maintenance_mode():
    SiteConfiguration.objects.update_or_create(defaults={'maintenance_mode': False})


class WarningFormDateTests(TestCase):
    """Regresión: el valor de valid_until debe persistir en edición y re-render."""

    @classmethod
    def setUpTestData(cls):
        _disable_maintenance_mode()
        cls.admin = User.objects.create_superuser(
            'warnadmin',
            'warnadmin@example.com',
            'pass',
            first_name='Admin',
            last_name='User',
        )
        cls.warning = Warning.objects.create(
            warning_type='early',
            user=cls.admin,
            summary='Resumen del aviso',
            valid_until=timezone.datetime(
                2026, 8, 5, 14, 30, tzinfo=timezone.get_current_timezone()
            ),
        )

    def test_update_get_prepopulates_valid_until(self):
        self.client.force_login(self.admin)
        url = reverse('meteo:alerta_temprana_update', args=[self.warning.uuid])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="05/08/2026 02:30 PM"')

    def test_invalid_post_keeps_submitted_valid_until(self):
        self.client.force_login(self.admin)
        url = reverse('meteo:alerta_temprana_update', args=[self.warning.uuid])
        data = {
            'summary': '',
            'valid_until': '2026-08-05T14:30',
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="05/08/2026 02:30 PM"')


class WarningEmailLinkTests(TestCase):
    """Regresión: el correo del aviso enlaza la página pública y sin href vacíos."""

    @classmethod
    def setUpTestData(cls):
        _disable_maintenance_mode()
        cls.admin = User.objects.create_superuser(
            'warnmailadmin',
            'warnmailadmin@example.com',
            'pass',
            first_name='Admin',
            last_name='Correo',
        )
        cls.recipient_list = get_default_list()
        add_confirmed_subscriber(cls.recipient_list, 'aviso@example.com')

    def _post_create(self, url_name, data):
        self.client.force_login(self.admin)
        old_immediate = huey.immediate
        huey.immediate = True
        try:
            with override_settings(EMAIL_BACKEND=LOCMEM):
                return self.client.post(reverse(url_name), data)
        finally:
            huey.immediate = old_immediate

    def _warning_data(self):
        return {
            'title': 'Aviso de prueba',
            'summary': 'Resumen del aviso',
            'valid_until': '2027-01-01T10:00',
            'send_email': 'on',
        }

    @override_settings(SITE_URL='http://testserver/')
    def test_el_correo_enlaza_al_listado_publico(self):
        response = self._post_create('meteo:alerta_temprana_create', self._warning_data())
        self.assertEqual(response.status_code, 302)

        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn('http://testserver/aviso/alerta_temprana/', body)
        self.assertNotIn('href=""', body)

    def test_sin_la_casilla_no_se_envia_correo(self):
        data = self._warning_data()
        data.pop('send_email')

        response = self._post_create('meteo:alerta_temprana_create', data)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)

    def test_el_correo_rellena_titulo_y_anio(self):
        self._post_create('meteo:alerta_temprana_create', self._warning_data())

        body = mail.outbox[0].body
        self.assertIn('Aviso de prueba', body)
        self.assertIn(f'Copyright &copy; {timezone.now().year}', body)
