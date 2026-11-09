"""Pruebas del núcleo de avisos (cambio 026, `design.md` §9 tests 1, 2, 6, 7 y 8)."""

from types import SimpleNamespace

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Notification
from apps.core.notifications import (
    TASK_ERROR_ALLOWLIST,
    make_dedupe_key,
    notify,
    notify_audience,
    notify_superusers,
    notify_task_failure,
)
from apps.core.tasks import send_email_task
from config.huey import huey


class NotificationDedupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('admin', 'admin@example.com', 'secreto')

    def _notify(self, key, title='Fallo de tarea'):
        return notify(
            self.user,
            source='huey',
            severity=Notification.Severity.ERROR,
            title=title,
            dedupe_key=key,
        )

    def test_dos_avisos_con_la_misma_clave_dejan_una_sola_fila(self):
        key = make_dedupe_key('huey', 'invoice:abc', 'ERROR')

        first = self._notify(key)
        second = self._notify(key, title='Fallo de tarea (reintento)')

        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(first.pk, second.pk)
        # El aviso existente no se reescribe: el primero es el que se vio.
        first.refresh_from_db()
        self.assertEqual(first.title, 'Fallo de tarea')

    def test_leer_un_aviso_y_repetir_el_hecho_crea_una_fila_nueva(self):
        key = make_dedupe_key('huey', 'invoice:abc', 'ERROR')

        first = self._notify(key)
        Notification.objects.filter(pk=first.pk).update(read_at=timezone.now())
        second = self._notify(key)

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(Notification.objects.count(), 2)
        self.assertEqual(Notification.objects.filter(read_at__isnull=True).count(), 1)

    def test_make_dedupe_key_es_estable_y_depende_de_las_partes(self):
        self.assertEqual(make_dedupe_key('a', 'b'), make_dedupe_key('a', 'b'))
        self.assertNotEqual(make_dedupe_key('a', 'b'), make_dedupe_key('a', 'c'))
        self.assertEqual(len(make_dedupe_key('a')), 64)

    def test_severidad_invalida_falla_en_vez_de_guardarse(self):
        with self.assertRaises(ValueError):
            notify(
                self.user,
                source='huey',
                severity='critico',
                title='Fallo',
                dedupe_key=make_dedupe_key('x'),
            )


class NotificationFlagTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('admin2', 'admin2@example.com', 'secreto')

    def _args(self):
        return {
            'source': 'huey',
            'severity': Notification.Severity.ERROR,
            'title': 'Fallo',
            'dedupe_key': make_dedupe_key('x'),
        }

    @override_settings(NOTIFICATIONS_ENABLED=False)
    def test_flag_apagado_no_crea_ninguna_fila(self):
        self.assertIsNone(notify(self.user, **self._args()))
        self.assertEqual(Notification.objects.count(), 0)

    def test_flag_encendido_por_defecto_crea_una_fila(self):
        self.assertIsNotNone(notify(self.user, **self._args()))
        self.assertEqual(Notification.objects.count(), 1)


class NotifySuperusersCountTests(TestCase):
    """`notify_superusers` reporta filas creadas, no destinatarios alcanzados.

    La tarea diaria de avisos por vencer registra `len(creadas)`: si devolviera
    las existentes, cada corrida informaría de recordatorios que no creó y el
    log dejaría de servir para saber si la tarea hizo algo.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin1 = User.objects.create_superuser('cuenta-admin1', 'cuenta1@example.com', 'x')
        cls.admin2 = User.objects.create_superuser('cuenta-admin2', 'cuenta2@example.com', 'x')

    def _args(self):
        return {
            'source': 'warning',
            'severity': Notification.Severity.WARNING,
            'title': 'Aviso por vencer',
            'dedupe_key': make_dedupe_key('warning', 'w1'),
        }

    def test_la_segunda_corrida_no_reporta_lo_que_ya_existia(self):
        self.assertEqual(len(notify_superusers(**self._args())), 2)

        self.assertEqual(notify_superusers(**self._args()), [])
        self.assertEqual(Notification.objects.count(), 2)

    def test_solo_cuenta_el_admin_que_no_lo_habia_visto(self):
        notify_superusers(**self._args())
        Notification.objects.filter(recipient=self.admin1).update(read_at=timezone.now())

        nuevas = notify_superusers(**self._args())

        self.assertEqual(len(nuevas), 1)
        self.assertEqual(nuevas[0].recipient, self.admin1)
        self.assertEqual(Notification.objects.count(), 3)

    @override_settings(NOTIFICATIONS_ENABLED=False)
    def test_sistema_apagado_no_reporta_nada(self):
        self.assertEqual(notify_superusers(**self._args()), [])
        self.assertEqual(Notification.objects.count(), 0)


class NotifyAudienceTests(TestCase):
    """Techo de audiencia por fuente (cambio 030, `design.md` §7.1).

    `notify_audience` decide a quién puede alcanzar cada `source`: una fuente
    interna no llega a un no-superusuario ni aunque la llamada pase clientes.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('admin-audiencia', 'admin@example.com', 'x')
        cls.titular = User.objects.create_user(
            'titular', 'titular@example.com', 'x', first_name='Ti', last_name='Tular'
        )
        cls.customer = SimpleNamespace(user=cls.titular)

    def _notify(self, source):
        # La llamada siempre pasa el cliente: lo que decide quién lo recibe es
        # la política de la fuente, no el llamador.
        return notify_audience(
            source=source,
            severity=Notification.Severity.INFO,
            title='Aviso de prueba',
            dedupe_key=make_dedupe_key('audiencia', source, 'test'),
            customers=[self.customer],
        )

    def test_una_fuente_interna_no_alcanza_al_cliente(self):
        self._notify('huey')

        self.assertEqual(
            list(Notification.objects.values_list('recipient__username', flat=True)),
            [self.admin.username],
        )

    def test_una_fuente_de_cliente_alcanza_al_titular(self):
        self._notify('invoice')

        self.assertEqual(
            set(Notification.objects.values_list('recipient__username', flat=True)),
            {self.admin.username, self.titular.username},
        )

    def test_el_admin_que_es_tambien_el_titular_recibe_una_sola_fila(self):
        self.customer.user = self.admin

        created = self._notify('subscription')

        self.assertEqual(len(created), 1)
        self.assertEqual(Notification.objects.count(), 1)

    def test_un_titular_inactivo_no_recibe_el_aviso(self):
        self.titular.is_active = False
        self.titular.save(update_fields=['is_active'])

        self._notify('subscription')

        self.assertEqual(
            list(Notification.objects.values_list('recipient__username', flat=True)),
            [self.admin.username],
        )

    def test_notify_superusers_sigue_siendo_solo_para_admins(self):
        """El envoltorio no hereda la audiencia de cliente de la fuente."""
        notify_superusers(
            source='subscription',
            severity=Notification.Severity.INFO,
            title='Aviso interno',
            dedupe_key=make_dedupe_key('audiencia', 'wrapper', 'test'),
        )

        self.assertEqual(
            list(Notification.objects.values_list('recipient__username', flat=True)),
            [self.admin.username],
        )


class TaskFailureNotificationTests(TestCase):
    """Prueba 7 de `design.md` §9: el fallo de una tarea avisa, la de factura no.

    En modo inmediato Huey ejecuta el primer intento, pero un reintento queda
    programado con `retry_delay` y no se corre dentro del test. Por eso el
    "fallo final" se fuerza pasando `retries=0` al encolar, que es exactamente
    el estado en el que el receiver avisa.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('admin3', 'admin3@example.com', 'secreto')
        cls.otro_admin = User.objects.create_superuser('admin4', 'admin4@example.com', 'secreto')

    def setUp(self):
        # El intento con reintentos deja una tarea en el scheduler de Huey.
        huey.flush()
        self.addCleanup(huey.flush)

    def _with_immediate(self, fn):
        """Ejecuta fn con Huey en modo inmediato (dispara las señales de ciclo de vida).

        Es el mismo patrón que usa `test_task_monitoring.py`.
        """
        old = huey.immediate
        huey.immediate = True
        try:
            fn()
        finally:
            huey.immediate = old

    def _send_email_that_fails(self, **options):
        send_email_task(
            subject='Fallo',
            html_message='<p>x</p>',
            from_email='from@example.com',
            recipients=['to@example.com'],
            **options,
        )

    @override_settings(EMAIL_BACKEND='apps.core.tests.test_task_monitoring.FailingEmailBackend')
    def test_el_fallo_final_de_una_tarea_de_la_allowlist_avisa_a_los_admins(self):
        self._with_immediate(lambda: self._send_email_that_fails(retries=0))

        avisos = Notification.objects.filter(source='huey')
        self.assertEqual(avisos.count(), 2)
        self.assertEqual(
            set(avisos.values_list('recipient__username', flat=True)),
            {'admin3', 'admin4'},
        )
        aviso = avisos.first()
        self.assertEqual(aviso.severity, Notification.Severity.ERROR)
        self.assertIn('send_email_task', aviso.title)

    @override_settings(EMAIL_BACKEND='apps.core.tests.test_task_monitoring.FailingEmailBackend')
    def test_un_fallo_con_reintentos_pendientes_todavia_no_avisa(self):
        # La cola puede resolverlo sola en el reintento siguiente; avisar acá
        # mandaría un error por algo que se arregló solo.
        self._with_immediate(self._send_email_that_fails)

        self.assertEqual(Notification.objects.count(), 0)

    @override_settings(EMAIL_BACKEND='apps.core.tests.test_task_monitoring.FailingEmailBackend')
    def test_el_aviso_no_lleva_el_traceback_y_enlaza_a_la_vista_del_log(self):
        self._with_immediate(lambda: self._send_email_that_fails(retries=0))

        aviso = Notification.objects.filter(source='huey').first()
        self.assertIsNotNone(aviso)
        self.assertNotIn('Traceback', aviso.body)
        self.assertEqual(aviso.url, reverse('dashboard:tasks'))

    def test_la_tarea_de_factura_no_emite_aviso_propio(self):
        # La factura tiene su fuente específica (PDF y correo): avisar también
        # desde la tarea mostraría dos veces el mismo hecho.
        self.assertNotIn('generate_invoice_pdf_and_email_task', TASK_ERROR_ALLOWLIST)
        self.assertEqual(
            notify_task_failure('generate_invoice_pdf_and_email_task', error='WeasyPrint caído'),
            [],
        )
        self.assertEqual(Notification.objects.count(), 0)

    def test_una_tarea_desconocida_no_avisa(self):
        self.assertEqual(notify_task_failure('limpiar_cache', error='x'), [])
        self.assertEqual(Notification.objects.count(), 0)
