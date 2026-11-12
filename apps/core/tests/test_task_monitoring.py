from django.core.mail.backends.base import BaseEmailBackend
from django.test import TestCase, override_settings

from apps.core.models import CompanySettings, TaskExecutionLog
from apps.core.tasks import generate_invoice_pdf_and_email_task, send_email_task
from config.huey import huey

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


class FailingEmailBackend(BaseEmailBackend):
    """Backend que fuerza un fallo en el envío para probar la visibilidad de errores."""

    def send_messages(self, messages):
        raise RuntimeError('SMTP caído (simulado)')


@huey.task()
def _boom_task():
    raise RuntimeError('fallo forzado para monitoreo')


class TaskExecutionLogUuidTests(TestCase):
    def test_uuid_is_generated_and_unique(self):
        first = TaskExecutionLog.objects.create(task_id='uuid-a', task_name='send_email_task')
        second = TaskExecutionLog.objects.create(task_id='uuid-b', task_name='send_email_task')
        self.assertIsNotNone(first.uuid)
        self.assertNotEqual(first.uuid, second.uuid)


class CustomPermissionLabelTests(TestCase):
    """Los permisos custom usan el codename nativo de Django y etiqueta española.

    El codename en minúsculas es el que Django genera solo: los permisos se
    renombraron en sitio (fila de `auth_permission` conservada, ver la migración
    `0004` de `core`), así que los grupos mantienen lo que ya tenían asignado.
    """

    def test_task_execution_log_permissions_are_labelled_in_spanish(self):
        labels = dict(TaskExecutionLog._meta.permissions)
        self.assertEqual(labels['view_taskexecutionlog'], 'Ver')
        self.assertEqual(labels['add_taskexecutionlog'], 'Añadir')
        self.assertEqual(labels['change_taskexecutionlog'], 'Editar')
        self.assertEqual(labels['delete_taskexecutionlog'], 'Eliminar')

    def test_company_settings_declares_four_custom_permissions(self):
        codenames = {codename for codename, _ in CompanySettings._meta.permissions}
        self.assertEqual(
            codenames,
            {
                'view_companysettings',
                'add_companysettings',
                'change_companysettings',
                'delete_companysettings',
            },
        )
        self.assertEqual(CompanySettings._meta.default_permissions, ())


class TaskMonitoringTests(TestCase):
    def _retry_count(self, task):
        # huey.task(retries=N) is stored as default_retries inside .settings
        return task.settings.get('default_retries', 0)

    def _with_immediate(self, fn):
        """Ejecuta fn con huey en modo inmediato (dispara señales de ciclo de vida)."""
        old = huey.immediate
        huey.immediate = True
        try:
            fn()
        finally:
            huey.immediate = old

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_enqueue_creates_success_log(self):
        def run():
            send_email_task(
                subject='Asunto',
                html_message='<p>Hola</p>',
                from_email='from@example.com',
                recipients=['to@example.com'],
            )

        self._with_immediate(run)

        log = TaskExecutionLog.objects.filter(task_name='send_email_task').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.status, TaskExecutionLog.STATUS_SUCCESS)

    def test_failure_records_error(self):
        def run():
            _boom_task()

        self._with_immediate(run)

        log = TaskExecutionLog.objects.filter(task_name='_boom_task').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.status, TaskExecutionLog.STATUS_ERROR)
        self.assertGreaterEqual(log.attempts, 1)
        self.assertTrue(log.traceback)

    def test_retry_config(self):
        self.assertGreaterEqual(self._retry_count(send_email_task), 1)
        self.assertGreaterEqual(self._retry_count(generate_invoice_pdf_and_email_task), 1)

    @override_settings(EMAIL_BACKEND='apps.core.tests.test_task_monitoring.FailingEmailBackend')
    def test_send_email_no_swallow(self):
        def run():
            send_email_task(
                subject='Fallo',
                html_message='<p>x</p>',
                from_email='from@example.com',
                recipients=['to@example.com'],
            )

        self._with_immediate(run)

        log = TaskExecutionLog.objects.filter(task_name='send_email_task').first()
        self.assertIsNotNone(log)
        # La excepción NO fue tragada: el estado es de error (ERROR o RETRYING)
        # y el traceback quedó registrado. Si se tragara, el estado sería SUCCESS.
        self.assertIn(
            log.status,
            [TaskExecutionLog.STATUS_ERROR, TaskExecutionLog.STATUS_RETRYING],
        )
        self.assertTrue(log.traceback)

    def test_retryable_task_persists_func_name_and_args(self):
        """generate_invoice_pdf_and_email_task es retryable: su func_name y func_args
        deben persistirse para poder reencolarla desde el dashboard."""

        def run():
            generate_invoice_pdf_and_email_task(
                invoice_uuid='00000000-0000-0000-0000-000000000000',
                site_url='http://testserver',
            )

        self._with_immediate(run)

        log = TaskExecutionLog.objects.filter(
            task_name='generate_invoice_pdf_and_email_task'
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.func_name, 'apps.core.tasks.generate_invoice_pdf_and_email_task')
        self.assertTrue(log.func_args)
        self.assertIn('00000000-0000-0000-0000-000000000000', log.func_args)

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_non_retryable_task_keeps_func_args_empty(self):
        """send_email_task NO es retryable: su func_name se guarda pero func_args
        queda vacío (nunca se persisten datos sensibles)."""

        def run():
            send_email_task(
                subject='Asunto',
                html_message='<p>Hola</p>',
                from_email='from@example.com',
                recipients=['to@example.com'],
            )

        self._with_immediate(run)

        log = TaskExecutionLog.objects.filter(task_name='send_email_task').first()
        self.assertIsNotNone(log)
        self.assertFalse(log.func_args)
