from datetime import UTC, datetime, time, timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.core.models import SiteConfiguration
from apps.core.utils import local_day_window


def disable_maintenance_mode():
    SiteConfiguration.objects.update_or_create(defaults={'maintenance_mode': False})


class ActiveTodayLocaldateTests(TestCase):
    """Regression test for the dashboard `active_today` user-stats count.

    `dashboard.py` counted `last_login__date=timezone.now().date()`. The
    `__date` lookup resolves in the local timezone, but `timezone.now().date()`
    yields the UTC date, so between 20:00 and 23:59 local the two disagree.

    The counts are deliberately asymmetric (2 users on the local day, 1 on the
    UTC day) so the assertion actually discriminates: a symmetric fixture would
    return the same number with and without the fix, and would prove nothing.
    """

    # UTC 2026-10-04T02:30 == Havana 2026-10-03 22:30. UTC date rolled over.
    FIXED_UTC = datetime(2026, 10, 4, 2, 30, 0, tzinfo=UTC)
    LOCAL_DATE = datetime(2026, 10, 3).date()
    UTC_DATE = datetime(2026, 10, 4).date()

    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.staff = User.objects.create_superuser(
            'staff',
            'staff@example.com',
            'pass',
            first_name='Admin',
            last_name='Super',
        )

    def _user_logged_in_on(self, username, day):
        user = User.objects.create_user(username, f'{username}@example.com', 'pass')
        user.last_login = timezone.make_aware(datetime.combine(day, time.min))
        user.save(update_fields=['last_login'])
        return user

    def test_active_today_counts_the_local_day_not_the_utc_day(self):
        with patch('django.utils.timezone.now', return_value=self.FIXED_UTC):
            self.client.force_login(self.staff)
            # force_login fires user_logged_in, which stamps staff.last_login
            # with "now" -- i.e. the frozen instant, whose local date is the one
            # under test. Staff is not part of the fixture; clear it so the
            # count reflects only the three seeded users.
            self.staff.last_login = None
            self.staff.save(update_fields=['last_login'])

            self._user_logged_in_on('local_one', self.LOCAL_DATE)
            self._user_logged_in_on('local_two', self.LOCAL_DATE)
            self._user_logged_in_on('utc_only', self.UTC_DATE)

            # Guard the premise: this instant really is inside the window.
            self.assertEqual(timezone.localdate(), self.LOCAL_DATE)
            self.assertNotEqual(timezone.localdate(), self.UTC_DATE)

            response = self.client.get(reverse('dashboard:index'))

        self.assertEqual(response.status_code, 200)
        stats = response.context['user_stats']
        # Two users logged in on the local day; the UTC-day user is not one of them.
        self.assertEqual(stats['active_today'], 2)

    def test_el_conteo_recorta_el_dia_local_por_sus_bordes(self):
        """El conteo usa la ventana [inicio, fin) del dia local.

        `last_login__date=timezone.localdate()` resuelve America/Havana dentro
        de PostgreSQL con el tzdata de la base. Con una ventana de instantes la
        frontera la decide Python. La prueba fija la semantica semiabierta: el
        primer instante del dia cuenta, la medianoche del dia siguiente no.
        """
        inicio, fin = local_day_window(self.LOCAL_DATE)
        bordes = {
            'antes': inicio - timedelta(seconds=1),
            'primero': inicio,
            'ultimo': fin - timedelta(seconds=1),
            'siguiente': fin,
        }

        with patch('django.utils.timezone.now', return_value=self.FIXED_UTC):
            self.client.force_login(self.staff)
            self.staff.last_login = None
            self.staff.save(update_fields=['last_login'])

            for nombre, instante in bordes.items():
                user = User.objects.create_user(nombre, f'{nombre}@example.com', 'pass')
                user.last_login = instante
                user.save(update_fields=['last_login'])

            # Guard the premise: the frozen instant really is in this window.
            self.assertEqual(timezone.localdate(), self.LOCAL_DATE)

            response = self.client.get(reverse('dashboard:index'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['user_stats']['active_today'], 2)

    def test_el_conteo_no_delega_la_zona_en_la_base(self):
        """La consulta del conteo no resuelve la zona dentro de PostgreSQL.

        `last_login__date=...` compila a `(last_login AT TIME ZONE
        'America/Havana')::date`: la zona se resuelve con el tzdata de la imagen
        de la base y el conteo se corre un dia cuando discrepa del de Python.
        Esta prueba mira el SQL real, que es el unico lugar donde la diferencia
        se ve sin depender del entorno.
        """
        with patch('django.utils.timezone.now', return_value=self.FIXED_UTC):
            self.client.force_login(self.staff)
            with CaptureQueriesContext(connection) as capturadas:
                self.client.get(reverse('dashboard:index'))

        consultas = [q['sql'] for q in capturadas.captured_queries]
        del_conteo = [sql for sql in consultas if 'last_login' in sql]
        self.assertTrue(del_conteo, 'el conteo no emitio ninguna consulta')
        for sql in del_conteo:
            self.assertNotIn('AT TIME ZONE', sql)
