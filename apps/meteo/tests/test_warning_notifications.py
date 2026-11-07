"""Recordatorio de avisos por vencer (cambio 026, fase 2).

La tarea periódica diaria avisa a los superusuarios cuando un `Warning` está por
terminar. El horizonte es configurable y la clave de deduplicación es el aviso,
así que dos corridas seguidas del mismo día no acumulan recordatorios.
"""

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Notification
from apps.meteo.models import Warning
from apps.meteo.tasks import avisos_por_vencer

HORIZON = 48


class WarningExpiryNotificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('meteo-admin', 'meteo-admin@example.com', 'x')
        cls.autor = User.objects.create_user('meteo-autor', 'autor@example.com', 'x')

    def _warning(self, *, hours, warning_type='early', title='Lluvias intensas'):
        return Warning.objects.create(
            warning_type=warning_type,
            title=title,
            user=self.autor,
            summary='Se esperan lluvias intensas en la provincia.',
            valid_until=timezone.now() + timedelta(hours=hours),
        )

    def test_aviso_que_vence_dentro_del_horizonte_avisa(self):
        warning = self._warning(hours=24)

        creadas = avisos_por_vencer(horizon_hours=HORIZON)

        self.assertEqual(len(creadas), 1)
        aviso = creadas[0]
        self.assertEqual(aviso.recipient, self.admin)
        self.assertEqual(aviso.severity, Notification.Severity.WARNING)
        self.assertEqual(aviso.source, 'warning')
        self.assertIn('Lluvias intensas', aviso.title)
        self.assertEqual(aviso.url, reverse('meteo:alerta_temprana_list'))
        # El recordatorio muere con el aviso que recuerda.
        self.assertEqual(aviso.expires_at, warning.valid_until)

    def test_aviso_fuera_del_horizonte_no_avisa(self):
        self._warning(hours=HORIZON + 1)

        self.assertEqual(avisos_por_vencer(horizon_hours=HORIZON), [])
        self.assertEqual(Notification.objects.count(), 0)

    def test_aviso_ya_vencido_no_avisa(self):
        self._warning(hours=-1)

        self.assertEqual(avisos_por_vencer(horizon_hours=HORIZON), [])
        self.assertEqual(Notification.objects.count(), 0)

    def test_dos_corridas_seguidas_no_duplican_el_recordatorio(self):
        self._warning(hours=24)

        avisos_por_vencer(horizon_hours=HORIZON)
        segunda = avisos_por_vencer(horizon_hours=HORIZON)

        self.assertEqual(Notification.objects.count(), 1)
        # El log de la tarea cuenta esta lista: una fila que ya existía no es
        # trabajo hecho por esta corrida.
        self.assertEqual(segunda, [])

    def test_el_horizonte_por_defecto_sale_de_settings(self):
        self._warning(hours=24)

        with self.settings(NOTIFICATIONS_WARNING_HORIZON_HOURS=1):
            self.assertEqual(avisos_por_vencer(), [])

        with self.settings(NOTIFICATIONS_WARNING_HORIZON_HOURS=HORIZON):
            self.assertEqual(len(avisos_por_vencer()), 1)
