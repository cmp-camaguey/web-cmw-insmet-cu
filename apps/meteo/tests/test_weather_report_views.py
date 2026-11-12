from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import SiteConfiguration
from apps.core.tests.base import add_confirmed_subscriber, get_default_list
from apps.meteo.models import WeatherReport
from config.huey import huey

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


def _disable_maintenance_mode():
    SiteConfiguration.objects.get_or_create(defaults={'maintenance_mode': False})


class WeatherReportEmailLinkTests(TestCase):
    """Regresión: el correo del reporte enlaza la página pública y sin href vacíos."""

    @classmethod
    def setUpTestData(cls):
        _disable_maintenance_mode()
        cls.admin = User.objects.create_superuser(
            'wrmailadmin',
            'wrmailadmin@example.com',
            'pass',
            first_name='Admin',
            last_name='Correo',
        )
        cls.recipient_list = get_default_list()
        add_confirmed_subscriber(cls.recipient_list, 'tiempo@example.com')

    @override_settings(SITE_URL='http://testserver/')
    def test_el_correo_enlaza_al_detalle_publico(self):
        self.client.force_login(self.admin)
        data = {
            'summary': 'Resumen de hoy',
            'content': 'Contenido',
            'send_email': 'on',
        }
        old_immediate = huey.immediate
        huey.immediate = True
        try:
            with override_settings(EMAIL_BACKEND=LOCMEM):
                response = self.client.post(reverse('meteo:tiempo_hoy_create'), data)
        finally:
            huey.immediate = old_immediate

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn('http://testserver/tiempo/hoy/', body)
        self.assertNotIn('href=""', body)

    def test_sin_la_casilla_no_se_envia_correo(self):
        self.client.force_login(self.admin)
        data = {'summary': 'Resumen de hoy', 'content': 'Contenido'}

        old_immediate = huey.immediate
        huey.immediate = True
        try:
            with override_settings(EMAIL_BACKEND=LOCMEM):
                response = self.client.post(reverse('meteo:tiempo_hoy_create'), data)
        finally:
            huey.immediate = old_immediate

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)

    def test_el_correo_usa_fallback_sin_request(self):
        """Triangulación: sin `request` cae al `BASE_URL` por defecto."""
        from apps.core.utils import mail_send

        report = WeatherReport.objects.create(
            report_type='today',
            summary='Resumen sin request',
            user=self.admin,
            date=timezone.now(),
        )
        old_immediate = huey.immediate
        huey.immediate = True
        try:
            with override_settings(EMAIL_BACKEND=LOCMEM):
                mail_send(None, report, 'Asunto sin request', 'home:weather_today')
        finally:
            huey.immediate = old_immediate

        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn('http://localhost:8000/tiempo/hoy/', body)
        self.assertNotIn('href=""', body)
        # `WeatherReport` no tiene `title`: el título cae al asunto.
        self.assertIn('Asunto sin request', body)
