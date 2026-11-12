"""La casilla «Enviar por correo» es el único freno de un envío masivo.

La audiencia es automática, así que el operador no elige lista: lo único que
decide es si este guardado manda correo o no. Estos tests fijan ese contrato.
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from apps.core.models import EmailRecipientList
from apps.core.tests.base import get_default_list
from apps.meteo.forms.warning import WarningForm
from apps.meteo.forms.weather_report import WeatherReportForm
from apps.meteo.models import Warning, WeatherReport


class SendEmailDefaultTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        get_default_list()
        cls.user = User.objects.create_user('autor', 'autor@example.com', 'pass')

    def test_un_aviso_nuevo_arranca_marcado(self):
        form = WarningForm()

        self.assertTrue(form.fields['send_email'].initial)

    def test_un_aviso_editado_arranca_desmarcado(self):
        warning = Warning.objects.create(
            warning_type='early',
            title='Aviso',
            user=self.user,
            summary='Resumen',
            valid_until=timezone.now(),
        )

        form = WarningForm(instance=warning)

        self.assertFalse(form.fields['send_email'].initial)

    def test_un_reporte_arranca_desmarcado_al_crear(self):
        form = WeatherReportForm()

        self.assertFalse(form.fields['send_email'].initial)

    def test_un_reporte_editado_sigue_desmarcado(self):
        report = WeatherReport.objects.create(
            report_type='today', summary='Resumen', user=self.user, date=timezone.now()
        )

        form = WeatherReportForm(instance=report)

        self.assertFalse(form.fields['send_email'].initial)

    def test_el_texto_de_ayuda_dice_a_cuantos_llega(self):
        EmailRecipientList.objects.get(is_default=True)
        from apps.core.tests.base import add_confirmed_subscriber

        add_confirmed_subscriber(EmailRecipientList.objects.get(is_default=True), 'uno@example.com')

        form = WarningForm()

        self.assertIn('1 suscriptor', form.fields['send_email'].help_text)
