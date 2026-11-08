from datetime import date, timedelta

from django.contrib import admin
from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.core.admin import ActivityLogAdmin
from apps.core.models import ActivityLog, SiteConfiguration
from apps.core.utils import local_day_window, log_action, log_activity_from_request


def disable_maintenance_mode():
    obj, _ = SiteConfiguration.objects.get_or_create(defaults={'maintenance_mode': False})
    if obj.maintenance_mode:
        obj.maintenance_mode = False
        obj.save()


class LogActionRecordingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('logger', 'logger@example.com', 'password')

    def test_log_action_creates_activity_log_and_logentry(self):
        obj = self.user
        before_activity = ActivityLog.objects.count()
        before_logentry = LogEntry.objects.count()

        log_action(self.user, obj, 2, 'Se editó el usuario.')

        self.assertEqual(ActivityLog.objects.count(), before_activity + 1)
        self.assertEqual(LogEntry.objects.count(), before_logentry + 1)

        al = ActivityLog.objects.order_by('-action_time').first()
        self.assertEqual(al.user, self.user)
        self.assertEqual(al.action_flag, 2)
        self.assertEqual(al.message, 'Se editó el usuario.')
        self.assertEqual(al.object_repr, str(obj))
        self.assertEqual(al.content_type, ContentType.objects.get_for_model(obj))
        self.assertEqual(al.object_id, str(obj.pk))
        # Sin request: IP y user_agent quedan vacíos (retrocompatibilidad).
        self.assertIsNone(al.ip_address)
        self.assertEqual(al.user_agent, '')

    def test_log_action_without_request_leaves_ip_blank(self):
        log_action(self.user, self.user, 1, 'Adición sin request')

        al = ActivityLog.objects.order_by('-action_time').first()
        self.assertIsNone(al.ip_address)
        self.assertEqual(al.user_agent, '')


class LogActivityFromRequestTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('requester', 'req@example.com', 'password')

    def test_log_activity_from_request_captures_ip_and_user_agent(self):
        factory = RequestFactory()
        request = factory.get('/')
        request.META['REMOTE_ADDR'] = '203.0.113.7'
        request.META['HTTP_USER_AGENT'] = 'Mozilla/5.0 TestAgent'
        request.user = self.user

        before = ActivityLog.objects.count()
        log_activity_from_request(request, self.user, 4, 'El usuario inició sesión.')

        self.assertEqual(ActivityLog.objects.count(), before + 1)
        al = ActivityLog.objects.order_by('-action_time').first()
        self.assertEqual(al.ip_address, '203.0.113.7')
        self.assertEqual(al.user_agent, 'Mozilla/5.0 TestAgent')
        self.assertEqual(al.user, self.user)
        self.assertEqual(al.action_flag, 4)
        self.assertEqual(al.message, 'El usuario inició sesión.')


class ActivityLogAdminTests(TestCase):
    def test_activity_log_registered_with_filters_and_search(self):
        self.assertTrue(admin.site.is_registered(ActivityLog))
        model_admin = admin.site._registry[ActivityLog]
        self.assertIsInstance(model_admin, ActivityLogAdmin)
        self.assertIn('user', model_admin.list_filter)
        self.assertIn('action_flag', model_admin.list_filter)
        self.assertIn('action_time', model_admin.list_filter)
        self.assertIn('action_time', model_admin.list_display)
        self.assertTrue(
            {'object_repr', 'message', 'ip_address'}.issubset(set(model_admin.search_fields))
        )


class ActivityLogViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.superuser = User.objects.create_superuser(
            'su', 'su@example.com', 'password', first_name='Admin', last_name='User'
        )
        cls.normal = User.objects.create_user(
            'normal', 'n@example.com', 'password', first_name='Norm', last_name='Aluser'
        )
        log_action(cls.superuser, cls.superuser, 1, 'Registro creado por superuser')

    def test_superuser_get_returns_200_and_lists_activity(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('core:activity_log'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Registro creado por superuser')

    def test_non_superuser_get_returns_403(self):
        disable_maintenance_mode()
        self.client.force_login(self.normal)
        response = self.client.get(reverse('core:activity_log'))
        self.assertEqual(response.status_code, 403)

    def test_filter_by_action_flag_excludes_other_flags(self):
        log_action(self.superuser, self.superuser, 2, 'Registro cambiado por superuser')
        self.client.force_login(self.superuser)

        response = self.client.get(reverse('core:activity_log'), {'action_flag': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Registro creado por superuser')
        self.assertNotContains(response, 'Registro cambiado por superuser')

    def test_filter_by_date_range_accepts_picker_dd_mm_yyyy_format(self):
        # El picker Tempus envía dd/mm/yyyy: un formato inválido sería un 500.
        self.client.force_login(self.superuser)
        response = self.client.get(
            reverse('core:activity_log'),
            {'date_from': '13/09/2026', 'date_to': '13/09/2026'},
        )
        self.assertEqual(response.status_code, 200)

    def test_partial_returns_table_body_only(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('core:activity_log'), {'partial': '1'})
        self.assertEqual(response.status_code, 200)
        # El tbody debe ir dentro de <table>: DOMParser descarta un <tbody> suelto.
        self.assertContains(response, '<table>')
        self.assertContains(response, '<tbody>')
        self.assertContains(response, 'Registro creado por superuser')
        self.assertNotContains(response, '<html')

    def test_partial_never_renders_a_colspan_empty_row(self):
        """El partial sin resultados no debe renderizar una fila con colspan.

        Es el mismo bug que se veía en el monitoreo de tareas: DataTables mapea
        celdas por posición, así que un único <td colspan="6"> en una tabla de 6
        columnas dispara "Requested unknown parameter '1' for row 0, column 1"
        y rompe la tabla justo cuando el operador filtra y no encuentra nada.
        """
        self.client.force_login(self.superuser)
        ActivityLog.objects.all().delete()
        response = self.client.get(reverse('core:activity_log'), {'partial': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'colspan')
        # Y con filas reales tampoco hay ninguna fila decolspan.
        ActivityLog.objects.create(
            user=self.superuser,
            action_flag=1,
            object_repr='x',
            message='prueba',
        )
        response = self.client.get(reverse('core:activity_log'), {'partial': '1'})
        self.assertNotContains(response, 'colspan')

    def test_partial_applies_picker_date_filters(self):
        self.client.force_login(self.superuser)
        response = self.client.get(
            reverse('core:activity_log'),
            {'partial': '1', 'date_from': '13/09/2026', 'date_to': '13/09/2026'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<table>')
        self.assertContains(response, '<tbody>')

    def test_el_filtro_por_fecha_recorta_el_dia_local_por_sus_bordes(self):
        """El rango del picker es [inicio, fin) del dia local, no un `__date`.

        `action_time__date__gte/lte` resuelve America/Havana dentro de
        PostgreSQL con el tzdata de la base; con una ventana de instantes la
        frontera la decide Python. La prueba fija la semantica semiabierta: el
        primer instante del dia entra, la medianoche del dia siguiente no.
        """
        dia = date(2026, 11, 8)
        inicio, fin = local_day_window(dia)
        bordes = {
            'antes del dia': inicio - timedelta(seconds=1),
            'primer instante': inicio,
            'ultimo instante': fin - timedelta(seconds=1),
            'medianoche siguiente': fin,
        }
        ActivityLog.objects.all().delete()
        for etiqueta, instante in bordes.items():
            registro = ActivityLog.objects.create(
                user=self.superuser,
                action_flag=1,
                object_repr=etiqueta,
                message=etiqueta,
            )
            ActivityLog.objects.filter(pk=registro.pk).update(action_time=instante)

        self.client.force_login(self.superuser)
        response = self.client.get(
            reverse('core:activity_log'),
            {'partial': '1', 'date_from': '08/11/2026', 'date_to': '08/11/2026'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'primer instante')
        self.assertContains(response, 'ultimo instante')
        self.assertNotContains(response, 'antes del dia')
        self.assertNotContains(response, 'medianoche siguiente')

    def test_el_filtro_por_fecha_no_delega_la_zona_en_la_base(self):
        """Ninguna consulta del filtro resuelve la zona dentro de PostgreSQL.

        `action_time__date__gte` compila a `(action_time AT TIME ZONE
        'America/Havana')::date`: la zona se resuelve con el tzdata de la imagen
        de la base, no con el de Python, y el borde del dia se corre una hora
        cuando discrepan. Esta prueba mira el SQL real, que es el unico lugar
        donde la diferencia se ve sin depender del entorno.
        """
        self.client.force_login(self.superuser)

        with CaptureQueriesContext(connection) as capturadas:
            self.client.get(
                reverse('core:activity_log'),
                {'partial': '1', 'date_from': '08/11/2026', 'date_to': '08/11/2026'},
            )

        consultas = [q['sql'] for q in capturadas.captured_queries]
        del_filtro = [
            sql for sql in consultas if 'core_activity_log' in sql and 'action_time' in sql
        ]
        self.assertTrue(del_filtro, 'el filtro no emitio ninguna consulta')
        for sql in del_filtro:
            self.assertNotIn('AT TIME ZONE', sql)
