"""Audiencia automática de los envíos masivos.

Ver `odd/tasks/030-boletin-y-suscripcion.md` S11 y S13.
"""

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.audiences import audience_size, resolve_audience
from apps.core.models import EmailRecipientList, Notification, Subscriber, Subscription
from apps.core.tests.base import add_confirmed_subscriber, get_default_list
from apps.meteo.models import WeatherReport
from config.huey import huey

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


class AudienceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.default_list = get_default_list()
        cls.other_list = EmailRecipientList.objects.create(name='Prensa')

        def add(email, *, list_=None, confirmed=True, active=True):
            subscriber = Subscriber.objects.create(
                email=email,
                source='admin',
                confirmed_at=timezone.now() if confirmed else None,
                active=active,
            )
            Subscription.objects.create(
                subscriber=subscriber, recipient_list=list_ or cls.default_list, source='admin'
            )
            return subscriber

        cls.confirmed = add('confirmado@ejemplo.com')
        cls.pending = add('pendiente@ejemplo.com', confirmed=False)
        cls.inactive = add('baja@ejemplo.com', active=False)
        cls.other = add('prensa@ejemplo.com', list_=cls.other_list)

    def test_solo_entran_confirmados_y_activos_de_la_lista_principal(self):
        emails = [subscriber.email for subscriber in resolve_audience()]

        self.assertEqual(emails, ['confirmado@ejemplo.com'])

    def test_la_lista_secundaria_no_recibe_envios_automaticos(self):
        self.assertNotIn('prensa@ejemplo.com', [s.email for s in resolve_audience()])

    def test_audience_size_cuenta_lo_mismo_que_resuelve(self):
        self.assertEqual(audience_size(), len(resolve_audience()))

    def test_sin_lista_principal_la_audiencia_es_vacia(self):
        EmailRecipientList.objects.filter(pk=self.default_list.pk).update(is_default=False)

        self.assertEqual(resolve_audience(), [])
        self.assertEqual(audience_size(), 0)


class MailSendSuccessNotificationTests(TestCase):
    """El envío que salió bien también deja rastro.

    Hasta ahora solo el fracaso avisaba, y un envío correcto es justo el que
    nadie puede reconstruir después: el log de la tarea dice «correo enviado» por
    destinatario, sin decir de qué publicación ni a cuántos.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            'admin_envio', 'admin_envio@example.com', 'pass', first_name='A', last_name='B'
        )
        add_confirmed_subscriber(get_default_list(), 'recibe@ejemplo.com')

    def _report(self):
        return WeatherReport.objects.create(
            report_type='today', summary='Con gente', user=self.admin, date=timezone.now()
        )

    def _send(self, report):
        from apps.core.utils import mail_send

        old_immediate = huey.immediate
        huey.immediate = True
        try:
            mail_send(None, report, 'Asunto', 'home:weather_today')
        finally:
            huey.immediate = old_immediate

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_el_envio_exitoso_avisa_cuantos_lo_recibieron(self):
        self._send(self._report())

        self.assertEqual(len(mail.outbox), 1)
        notification = Notification.objects.get(recipient=self.admin, source='email')
        self.assertEqual(notification.severity, Notification.Severity.INFO)
        self.assertIn('1 suscriptor', notification.body)
        self.assertTrue(notification.url.startswith('/'))

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_el_mismo_envio_no_duplica_el_aviso_sin_leer(self):
        report = self._report()
        self._send(report)
        self._send(report)

        self.assertEqual(
            Notification.objects.filter(
                recipient=self.admin, source='email', read_at__isnull=True
            ).count(),
            1,
        )


class MailSendEmptyAudienceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            'admin_vacio', 'admin_vacio@example.com', 'pass', first_name='A', last_name='B'
        )
        get_default_list()

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_sin_suscriptores_avisa_al_admin_en_vez_de_callar(self):
        from apps.core.utils import mail_send

        report = WeatherReport.objects.create(
            report_type='today', summary='Sin nadie', user=self.admin, date=timezone.now()
        )
        old_immediate = huey.immediate
        huey.immediate = True
        try:
            mail_send(None, report, 'Asunto', 'home:weather_today')
        finally:
            huey.immediate = old_immediate

        self.assertEqual(len(mail.outbox), 0)
        notification = Notification.objects.get(recipient=self.admin, source='email')
        # El enlace se guarda como ruta: un nombre de URL (`home:...`) en un
        # `href` hace que el navegador lo trate como esquema y abra `xdg-open`.
        self.assertTrue(notification.url.startswith('/'))
        self.assertNotIn(':', notification.url.split('/')[1])
