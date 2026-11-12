import re
from datetime import timedelta

from django.contrib import admin
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.admin import TaskExecutionLogAdmin
from apps.core.models import SiteConfiguration, TaskExecutionLog


def _disable_maintenance():
    SiteConfiguration.objects.get_or_create(defaults={'maintenance_mode': False})


class TaskMonitoringViewTests(TestCase):
    """Runtime coverage for verify gaps TASK-VIEW-1 and TASK-ADMIN-1."""

    @classmethod
    def setUpTestData(cls):
        _disable_maintenance()
        cls.url = reverse('dashboard:tasks')
        cls.superuser = User.objects.create_superuser(
            'su_monitor',
            'su_monitor@example.com',
            'pass',
            first_name='Su',
            last_name='Monitor',
        )
        # Non-superuser with a complete personal profile and no Customer:
        # the CheckUserProfileMiddleware will NOT redirect, so the view's
        # UserPassesTestMixin (is_superuser) decides access.
        cls.non_superuser = User.objects.create_user(
            'reg_monitor',
            'reg_monitor@example.com',
            'pass',
            first_name='Reg',
            last_name='Monitor',
        )

    def test_non_superuser_blocked(self):
        self.client.force_login(self.non_superuser)
        response = self.client.get(self.url)
        # A complete non-staff profile hits the view guard => 403.
        # (If staff-but-not-superuser, it would redirect to login => 302.)
        self.assertIn(response.status_code, (403, 302))
        self.assertEqual(response.status_code, 403)

    def test_superuser_allowed(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_stale_queue_banner(self):
        now = timezone.now()
        TaskExecutionLog.objects.create(
            task_id='stale-1',
            task_name='send_email_task',
            status=TaskExecutionLog.STATUS_ENQUEUED,
            enqueued_at=now - timedelta(minutes=10),
        )
        self.client.force_login(self.superuser)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('en cola sin iniciar por más de', content)
        self.assertIn('alert-danger', content)
        self.assertIn('<strong>1</strong>', content)

    def test_badge_uses_lt_style(self):
        """La tabla usa badges con estilo Tabler bg-*-lt (convención del proyecto)."""
        TaskExecutionLog.objects.create(
            task_id='err-badge-1',
            task_name='send_email_task',
            status=TaskExecutionLog.STATUS_ERROR,
            enqueued_at=timezone.now(),
        )
        TaskExecutionLog.objects.create(
            task_id='enqueued-badge-1',
            task_name='send_email_task',
            status=TaskExecutionLog.STATUS_ENQUEUED,
            enqueued_at=timezone.now(),
        )
        self.client.force_login(self.superuser)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('badge bg-danger-lt', content)
        self.assertIn('badge bg-info-lt', content)

    def test_actions_use_icons_and_tooltips(self):
        """ "Acciones con iconos + tooltips; los args viven en el modal."""
        long_args = 'arg1=' + 'x' * 200
        TaskExecutionLog.objects.create(
            task_id='err-icon-1',
            task_name='generate_invoice_pdf_and_email_task',
            status=TaskExecutionLog.STATUS_ERROR,
            enqueued_at=timezone.now(),
            func_name='apps.core.tasks.generate_invoice_pdf_and_email_task',
            func_args='{}',
            args_repr=long_args,
        )
        self.client.force_login(self.superuser)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # Botones de acción con clase btn-icon y tooltip.
        self.assertIn('btn-icon btn-outline-danger btn-sm', content)
        self.assertIn('data-bs-toggle="tooltip"', content)
        # Iconos de las acciones (traceback / reintentar / eliminar).
        self.assertIn('ti-code', content)
        self.assertIn('ti-refresh', content)
        self.assertIn('ti-trash', content)
        # Args: fuera de la tabla, dentro del modal de traceback y sin truncar
        # (antes iban en una <td> con title=; ahora el <code> del modal).
        tabla = content.split('data-tasks-modals')[0]
        self.assertNotIn(long_args, tabla)
        self.assertNotIn('<th>Args</th>', content)
        self.assertIn(long_args, content)
        self.assertIn('Argumentos', content)

    def test_delete_uses_confirm_modal(self):
        """ "Eliminar" abre el modal de confirmación (estilo listados)."""
        TaskExecutionLog.objects.create(
            task_id='del-modal-1',
            task_name='send_email_task',
            status=TaskExecutionLog.STATUS_ERROR,
            enqueued_at=timezone.now(),
        )
        self.client.force_login(self.superuser)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # Botón con data-action->eliminar + uuid + tooltip "Eliminar".
        self.assertIn('action-btn', content)
        self.assertIn('data-action="eliminar"', content)
        self.assertIn('data-bs-original-title="Eliminar">', content)
        # El modal de confirmación está presente con su form.
        self.assertIn('id="confirmTaskDeleteModal"', content)
        self.assertIn('id="confirmTaskDeleteForm"', content)
        self.assertIn('name="action" value="delete"', content)

    def test_action_urls_use_uuid_not_pk(self):
        """Las acciones de la tabla se direccionan por uuid, no por pk.

        Es la convención del proyecto para modelos expuestos; el pk numérico
        deja adivinable la fila y no debe aparecer en la URL ni en `data-uuid`.
        """
        execution = TaskExecutionLog.objects.create(
            task_id='uuid-url-1',
            task_name='generate_invoice_pdf_and_email_task',
            status=TaskExecutionLog.STATUS_ERROR,
            enqueued_at=timezone.now(),
            func_name='apps.core.tasks.generate_invoice_pdf_and_email_task',
            func_args='{"args": ["00000000-0000-0000-0000-000000000000", "http://x"]}',
            logical_key='invoice:00000000-0000-0000-0000-000000000000',
        )
        self.client.force_login(self.superuser)
        content = self.client.get(self.url).content.decode()
        self.assertIn(f'data-uuid="{execution.uuid}"', content)
        self.assertIn(
            reverse('dashboard:tasks_action', args=[execution.uuid]),
            content,
        )
        self.assertNotIn(f'data-uuid="{execution.pk}"', content)
        self.assertNotIn(f'/tasks/{execution.pk}/action/', content)

    def test_retry_button_only_on_error(self):
        """ "Reintentar" solo aparece en tareas que fallaron.

        Antes la condición era "si tiene func_name y func_args", o sea que una
        tarea en SUCCESS mostraba el botón: "se puede reencolar" no es lo mismo
        que "falló", y ofrecerlo invitaba a duplicar trabajo ya hecho.
        """
        estados = [
            TaskExecutionLog.STATUS_SUCCESS,
            TaskExecutionLog.STATUS_ENQUEUED,
            TaskExecutionLog.STATUS_EXECUTING,
            TaskExecutionLog.STATUS_RETRYING,
            TaskExecutionLog.STATUS_REVOKED,
            TaskExecutionLog.STATUS_ERROR,
        ]
        for i, estado in enumerate(estados):
            TaskExecutionLog.objects.create(
                task_id=f'retry-btn-{i}',
                task_name='generate_invoice_pdf_and_email_task',
                status=estado,
                enqueued_at=timezone.now(),
                func_name='apps.core.tasks.generate_invoice_pdf_and_email_task',
                func_args='{"args": ["x", "y"], "kwargs": {}}',
            )
        self.client.force_login(self.superuser)
        content = self.client.get(self.url).content.decode()

        # Todos los estados menos ERROR se renderizan, y solo uno lleva botón.
        for estado in estados:
            with self.subTest(estado=estado):
                fila = self._fila_de(content, estado)
                self.assertEqual(
                    'ti-refresh' in fila,
                    estado == TaskExecutionLog.STATUS_ERROR,
                    f'reintentar debería verse solo en ERROR, no en {estado}',
                )

    def _fila_de(self, content, estado):
        """Devuelve el HTML de la fila cuyo badge dice `estado`."""

        clase = {
            TaskExecutionLog.STATUS_SUCCESS: 'bg-success-lt',
            TaskExecutionLog.STATUS_ENQUEUED: 'bg-info-lt',
            TaskExecutionLog.STATUS_EXECUTING: 'bg-primary-lt',
            TaskExecutionLog.STATUS_ERROR: 'bg-danger-lt',
            TaskExecutionLog.STATUS_RETRYING: 'bg-warning-lt',
            TaskExecutionLog.STATUS_REVOKED: 'bg-secondary-lt',
        }[estado]
        self.assertIn(clase, content, f'no se renderizó ninguna fila {estado}')
        inicio = content.index(f'class="badge {clase}"')
        # La fila empieza antes del badge (columna de tarea) y termina después
        # de la columna de acciones.
        fila_inicio = content.rindex('<tr>', 0, inicio)
        fila_fin = content.index('</tr>', inicio) + len('</tr>')
        return content[fila_inicio:fila_fin]

    def test_partial_returns_table_for_polling(self):
        """?partial=1 devuelve la tabla sola, para el auto-refresco."""
        TaskExecutionLog.objects.create(
            task_id='partial-1',
            task_name='send_email_task',
            status=TaskExecutionLog.STATUS_ERROR,
            enqueued_at=timezone.now(),
            traceback='Traceback (most recent call last): ...',
        )
        self.client.force_login(self.superuser)
        response = self.client.get(self.url, {'partial': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'pages/dashboard/tasks_table.html')
        content = response.content.decode()
        # La tabla y los modales de traceback: sin el modal, una fila que pasa a
        # ERROR con el polling no tendría a dónde apuntar su botón.
        self.assertIn('id="tasks-table"', content)
        self.assertIn('id="tb-', content)
        # Y nada del chrome de la página: para eso se pidió el partial.
        self.assertNotIn('confirmTaskDeleteModal', content)
        self.assertNotIn('<html', content.lower())

    def test_partial_keeps_status_filter(self):
        """El polling respeta el filtro de la barra de estado."""
        # Se distinguen por `args_repr`, que sí se renderiza: dos filas con el
        # mismo `task_name` serían indistinguibles en el HTML.
        TaskExecutionLog.objects.create(
            task_id='filtro-ok',
            task_name='send_email_task',
            status='SUCCESS',
            args_repr='args=[correo-ok]',
        )
        TaskExecutionLog.objects.create(
            task_id='filtro-err',
            task_name='send_email_task',
            status='ERROR',
            args_repr='args=[correo-err]',
        )
        self.client.force_login(self.superuser)
        content = self.client.get(self.url, {'partial': '1', 'status': 'ERROR'}).content.decode()
        self.assertIn('correo-err', content)
        self.assertNotIn('correo-ok', content)

    def test_fingerprint_is_stable_until_something_changes(self):
        """La sonda solo cambia cuando cambia algo que la tabla muestra."""
        TaskExecutionLog.objects.create(
            task_id='fp-1',
            task_name='send_email_task',
            status=TaskExecutionLog.STATUS_ENQUEUED,
            summary='Factura A',
        )
        self.client.force_login(self.superuser)

        def token():
            response = self.client.get(self.url, {'fingerprint': '1'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Content-Type'], 'application/json')
            return response.json()['token']

        primero = token()
        self.assertEqual(
            token(),
            primero,
            'sin cambios en la tabla, la huella debe ser idéntica',
        )

        # Un cambio de estado es exactamente lo que tiene que moverla.
        TaskExecutionLog.objects.filter(task_id='fp-1').update(status='SUCCESS')
        self.assertNotEqual(token(), primero)

        # Y una fila nueva también.
        antes_de_agregar = token()
        TaskExecutionLog.objects.create(
            task_id='fp-2', task_name='send_email_task', status='SUCCESS'
        )
        self.assertNotEqual(token(), antes_de_agregar)

    def test_fingerprint_does_not_render_a_template(self):
        """La sonda es una respuesta de API, no una página."""
        self.client.force_login(self.superuser)
        response = self.client.get(self.url, {'fingerprint': '1'})
        self.assertNotIn(b'<table', response.content)
        self.assertNotIn(b'<html', response.content.lower())

    def test_fingerprint_respects_status_filter(self):
        """Con un filtro activo, la huella cubre solo lo que se ve."""
        TaskExecutionLog.objects.create(
            task_id='fp-ok', task_name='send_email_task', status='SUCCESS'
        )
        error = TaskExecutionLog.objects.create(
            task_id='fp-err', task_name='send_email_task', status='ERROR'
        )
        self.client.force_login(self.superuser)

        def token(**params):
            params['fingerprint'] = '1'
            return self.client.get(self.url, params).json()['token']

        solo_error = token(status='ERROR')
        # Tocar una fila que NO está en el filtro no debe mover la huella.
        TaskExecutionLog.objects.filter(task_id='fp-ok').update(attempts=7)
        self.assertEqual(token(status='ERROR'), solo_error)
        # Tocar una que sí está en el filtro, sí.
        TaskExecutionLog.objects.filter(pk=error.pk).update(attempts=2)
        self.assertNotEqual(token(status='ERROR'), solo_error)

    def test_args_column_removed_and_moved_to_modal(self):
        """La columna Args se fue de la tabla (no aportaba) al modal."""
        TaskExecutionLog.objects.create(
            task_id='args-1',
            task_name='generate_invoice_pdf_and_email_task',
            status=TaskExecutionLog.STATUS_ERROR,
            enqueued_at=timezone.now(),
            traceback='Traceback (most recent call last): ...',
            func_name='apps.core.tasks.generate_invoice_pdf_and_email_task',
            func_args='{"args": ["secreto-1", "http://x"], "kwargs": {}}',
            args_repr='args=(secreto-1, http://x)',
        )
        self.client.force_login(self.superuser)
        content = self.client.get(self.url).content.decode()

        # Fuera de la tabla: el <th> ya no existe.
        self.assertNotIn('<th>Args</th>', content)
        # Pero el dato no se perdió: sigue disponible en el modal de traceback.
        self.assertIn('Argumentos', content)
        self.assertIn('secreto-1', content)

    def test_partial_never_renders_a_colspan_empty_row(self):
        """El template NO debe renderizar una fila de "no hay nada" con colspan.

        DataTables mapea celdas por posición: si al tbody le llega una fila con
        un único <td colspan="N"> para una tabla de N columnas, se queja con
        "Requested unknown parameter '1' for row 0, column 1" y deja la tabla a
        medio construir. Pasaba en la carga inicial y en cada filtro sin
        resultados. El estado vacío lo pinta DataTables (language.emptyTable),
        no el template: por eso la fila no se renderiza nunca.
        """
        self.client.force_login(self.superuser)

        def assert_sin_fila_vacia(params):
            content = self.client.get(self.url, params).content.decode()
            tabla = content.split('<table id="tasks-table"', 1)[1].split('</table>', 1)[0]
            self.assertNotIn('colspan', tabla)
            # Solo filas reales: el trozo tras el último </tr> es whitespace.
            for fila in tabla.split('<tr')[1:]:
                cuerpo = fila.split('</tr>', 1)[0]
                if '<td' not in cuerpo:
                    continue  # fila de encabezado
                celdas = cuerpo.count('<td')
                self.assertEqual(celdas, 8, f'fila con {celdas} celdas, se esperaban 8')

        assert_sin_fila_vacia({})  # sin registros
        assert_sin_fila_vacia({'status': 'ERROR'})  # filtro sin resultados

        # Con filas reales tampoco aparece ninguna fila decolspan.
        TaskExecutionLog.objects.create(
            task_id='cols-1', task_name='send_email_task', status='SUCCESS'
        )
        assert_sin_fila_vacia({})

    def test_filter_buttons_are_marked_and_ready_for_ajax(self):
        """Los botones de filtro se interceptan por JS y marcan el activo.

        Antes eran <a href> que recargaban la página entera, tirando el estado
        del operador; ahora el JS los intercepta (data-tasks-filter) y marca
        cuál está aplicado. Sin data-tasks-filter, el click recarga todo y el
        atributo falta.

        El marcado se busca con una expresión regular que tolera whitespace y
        no con un espacio literal a propósito: djlint parte los atributos
        `class` largos en varias líneas cuando hace falta, y un conteo sobre el
        texto exacto rompería con el formateador y no con un cambio real de
        comportamiento.
        """
        self.client.force_login(self.superuser)
        for status in (None, 'ERROR', 'ENQUEUED'):
            params = {'status': status} if status else {}
            content = self.client.get(self.url, params).content.decode()
            self.assertEqual(content.count('data-tasks-filter'), 3)
            # Solo uno de los tres lleva la clase active, y el marcado depende
            # del filtro pedido, no de un valor fijo.
            activos = sum(
                len(re.findall(rf'btn-outline-{variante}\s+active', content))
                for variante in ('secondary', 'danger', 'info')
            )
            self.assertEqual(activos, 1, f'activos={activos} para status={status!r}')

    def test_active_filter_is_exposed_to_the_template(self):
        """La vista expone current_status para pintar el filtro aplicado."""
        TaskExecutionLog.objects.create(task_id='st-1', task_name='send_email_task', status='ERROR')
        self.client.force_login(self.superuser)
        response = self.client.get(self.url, {'status': 'ERROR'})
        self.assertEqual(response.context['current_status'], 'ERROR')

    def test_retry_form_lives_inside_the_row_it_belongs_to(self):
        """El form de reintento viaja dentro de su <td>.

        El botón usa `form="retry-N"` en vez de un <form> anidado, pero el form
        sí se renderiza dentro de la celda de acciones. Eso es lo que permite
        que el polling no tenga que sincronizar forms aparte: al repoblar con
        `cell.innerHTML`, el form viaja con la fila y la asociación por id sigue
        siendo válida. Si alguien mueve el form fuera del <td>, este test falla.
        """
        log = TaskExecutionLog.objects.create(
            task_id='retry-form-1',
            task_name='generate_invoice_pdf_and_email_task',
            status=TaskExecutionLog.STATUS_ERROR,
            enqueued_at=timezone.now(),
            func_name='apps.core.tasks.generate_invoice_pdf_and_email_task',
            func_args='{}',
        )
        self.client.force_login(self.superuser)
        content = self.client.get(self.url).content.decode()

        fila = content.split(f'data-bs-target="#tb-{log.uuid}"', 1)[1].split('</tr>', 1)[0]
        self.assertIn(f'form="retry-{log.uuid}"', fila)
        self.assertIn(f'<form id="retry-{log.uuid}"', fila)

    def test_auto_refresh_is_wired(self):
        """La página carga el JS del polling y la tabla trae su URL de refresco."""
        self.client.force_login(self.superuser)
        content = self.client.get(self.url).content.decode()
        self.assertIn('dist/js/tasks-monitor.js', content)
        self.assertIn('data-refresh-url=', content)
        self.assertIn('data-language-url=', content)


class TaskMonitoringActionTests(TestCase):
    """Acciones POST sobre un registro de tarea: reintentar y eliminar."""

    @classmethod
    def setUpTestData(cls):
        _disable_maintenance()
        cls.url = reverse('dashboard:tasks')
        cls.superuser = User.objects.create_superuser(
            'su_mon_action',
            'su_mon_action@example.com',
            'pass',
            first_name='Su',
            last_name='Action',
        )
        # El middleware CheckUserProfileMiddleware exige email + nombres completos.
        cls.non_superuser = User.objects.create_user(
            'reg_mon_action',
            'reg_mon_action@example.com',
            'pass',
            first_name='Reg',
            last_name='Action',
        )

    @staticmethod
    def _log(**overrides):
        # `logical_key` es lo que hace que un reintento reutilice esta fila en
        # vez de abrir otra: la señal la guarda en cada encolado nuevo. Las
        # filas creadas por el código anterior a esa clave no la traen y siguen
        # abriendo fila nueva al reintentarse.
        defaults = {
            'task_id': 'task-action-1',
            'task_name': 'generate_invoice_pdf_and_email_task',
            'status': TaskExecutionLog.STATUS_ERROR,
            'enqueued_at': timezone.now(),
            'func_name': 'apps.core.tasks.generate_invoice_pdf_and_email_task',
            'func_args': '{"args": ["00000000-0000-0000-0000-000000000000", "http://x"]}',
            'logical_key': 'invoice:00000000-0000-0000-0000-000000000000',
        }
        defaults.update(overrides)
        return TaskExecutionLog.objects.create(**defaults)

    def test_retry_reenqueues_task(self):
        execution = self._log()
        from config.huey import huey

        pending_before = huey.pending_count()
        self.client.force_login(self.superuser)
        response = self.client.post(
            reverse('dashboard:tasks_action', args=[execution.uuid]),
            {'action': 'retry'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, self.url)
        # La tarea fue reencolada en la cola persistente.
        self.assertEqual(huey.pending_count(), pending_before + 1)

    def test_retry_updates_same_row_not_a_new_one(self):
        """Reintentar no abre una fila nueva: reutiliza la del trabajo.

        Es el bug que más confundía al operador: pulsaba "Reintentar" sobre una
        tarea y le aparecía una segunda fila, con lo que el error original
        quedaba ahí para siempre y era imposible seguir el hilo del fallo.
        """
        execution = self._log()
        from config.huey import huey

        self.client.force_login(self.superuser)
        antes = TaskExecutionLog.objects.count()
        task_id_original = execution.task_id

        self.client.post(
            reverse('dashboard:tasks_action', args=[execution.uuid]),
            {'action': 'retry'},
        )

        self.assertEqual(
            TaskExecutionLog.objects.count(),
            antes,
            'el reintento no debe crear una fila nueva en el log',
        )
        execution.refresh_from_db()
        self.assertNotEqual(
            execution.task_id,
            task_id_original,
            'la fila debe apuntar al id de intento nuevo, no quedarse en el viejo',
        )
        self.assertEqual(
            execution.status,
            TaskExecutionLog.STATUS_ENQUEUED,
            'la misma fila vuelve a "En cola" en vez de duplicarse',
        )
        huey.flush()

    def test_retry_non_retryable_rejected(self):
        execution = self._log(
            task_name='send_email_task',
            func_name='apps.core.tasks.send_email_task',
            func_args='',
        )
        self.client.force_login(self.superuser)
        response = self.client.post(
            reverse('dashboard:tasks_action', args=[execution.uuid]),
            {'action': 'retry'},
        )
        self.assertEqual(response.status_code, 302)
        # El registro sigue igual: no se reencoló ni se modificó.
        execution.refresh_from_db()
        self.assertEqual(execution.status, TaskExecutionLog.STATUS_ERROR)

    def test_delete_removes_log(self):
        execution = self._log()
        self.client.force_login(self.superuser)
        response = self.client.post(
            reverse('dashboard:tasks_action', args=[execution.uuid]),
            {'action': 'delete'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(TaskExecutionLog.objects.filter(pk=execution.pk).exists())

    def test_non_superuser_blocked_from_action(self):
        execution = self._log()
        self.client.force_login(
            User.objects.create_user(
                'reg_mon_action2',
                'reg_mon_action2@example.com',
                'pass',
                first_name='Reg',
                last_name='Action',
            )
        )
        response = self.client.post(
            reverse('dashboard:tasks_action', args=[execution.uuid]),
            {'action': 'delete'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(TaskExecutionLog.objects.filter(pk=execution.pk).exists())


class TaskExecutionLogAdminTests(TestCase):
    """Runtime coverage for verify gap TASK-ADMIN-1 (admin status filter)."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            'su_admin_mon',
            'su_admin_mon@example.com',
            'pass',
            first_name='Su',
            last_name='Admin',
        )

    def test_registered_and_readonly(self):
        self.assertIn(TaskExecutionLog, admin.site._registry)
        admin_class = admin.site._registry[TaskExecutionLog]
        self.assertIsInstance(admin_class, TaskExecutionLogAdmin)
        self.assertFalse(admin_class.has_add_permission(None))
        self.assertFalse(admin_class.has_change_permission(None))
        self.assertIn('status', admin_class.list_filter)

    def test_status_filter_returns_only_matching(self):
        now = timezone.now()
        TaskExecutionLog.objects.create(
            task_id='err-1',
            task_name='send_email_task',
            status=TaskExecutionLog.STATUS_ERROR,
            enqueued_at=now,
        )
        TaskExecutionLog.objects.create(
            task_id='ok-1',
            task_name='send_email_task',
            status=TaskExecutionLog.STATUS_SUCCESS,
            enqueued_at=now,
        )
        rf = RequestFactory()
        request = rf.get('/admin/core/taskexecutionlog/?status__exact=ERROR')
        request.user = self.superuser
        changelist = TaskExecutionLogAdmin(TaskExecutionLog, admin.site).get_changelist_instance(
            request
        )
        qs = changelist.get_queryset(request)
        self.assertEqual(qs.count(), 1)
        self.assertEqual(qs.first().status, TaskExecutionLog.STATUS_ERROR)
