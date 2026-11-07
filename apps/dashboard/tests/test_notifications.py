"""UI y permisos de los avisos operativos (cambio 026, fase 3).

Lo que se prueba aqui no es el modelo —eso esta en `apps/core/tests`— sino las
tres cosas que solo se rompen en la capa web: que el queryset no se escape del
destinatario, que un no-superusuario no entre, y que el contador del navbar diga
lo mismo que la lista.
"""

from datetime import timedelta

from django.contrib.auth.models import User
from django.template import Context, Template
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Notification


def _superuser(username='admin'):
    # `CheckUserProfileMiddleware` manda a /accounts/profile/update/ a quien no
    # tenga nombre, apellido o correo, y entonces la vista bajo prueba nunca
    # corre. El alta tiene que ser realista o el test mide otra cosa.
    return User.objects.create_superuser(
        username,
        f'{username}@example.com',
        'pass',
        first_name='Admin',
        last_name='Test',
    )


def _user(username='cliente', staff=False):
    return User.objects.create_user(
        username,
        f'{username}@example.com',
        'pass',
        first_name='Cliente',
        last_name='Test',
        is_staff=staff,
    )


def _notification(recipient, *, title='Aviso', read=False, severity='error', **kwargs):
    obj = Notification.objects.create(
        recipient=recipient,
        severity=severity,
        source='test',
        title=title,
        # Unica por titulo: el indice parcial solo impide duplicar las NO leidas
        # del mismo destinatario, que es justo lo que se quiere aislar aqui.
        dedupe_key=f'{recipient.pk}:{title}'.ljust(64, 'x')[:64],
        **kwargs,
    )
    if read:
        Notification.objects.filter(pk=obj.pk).update(read_at=timezone.now())
        obj.refresh_from_db()
    return obj


def _navbar_html(user):
    """Renderiza solo el tag del navbar, sin montar el dashboard entero."""
    request = RequestFactory().get('/dashboard/')
    request.user = user
    template = Template('{% load notification_tags %}{% notifications_navbar %}')
    return template.render(Context({'request': request}))


class NotificationListPermissionTests(TestCase):
    def test_el_historial_es_solo_para_superusuarios(self):
        self.client.force_login(_user(staff=True))
        response = self.client.get(reverse('dashboard:notifications'))
        self.assertEqual(response.status_code, 403)

    def test_un_anonimo_no_entra(self):
        response = self.client.get(reverse('dashboard:notifications'))
        self.assertEqual(response.status_code, 403)

    def test_marcar_leida_exige_post(self):
        admin = _superuser()
        aviso = _notification(admin)
        self.client.force_login(admin)
        response = self.client.get(reverse('dashboard:notification_read', args=[aviso.uuid]))
        self.assertEqual(response.status_code, 405)

    def test_marcar_todas_exige_post(self):
        admin = _superuser()
        self.client.force_login(admin)
        response = self.client.get(reverse('dashboard:notifications_read_all'))
        self.assertEqual(response.status_code, 405)

    def test_un_no_superusuario_no_puede_marcar_leida(self):
        admin = _superuser()
        aviso = _notification(admin)
        self.client.force_login(_user('intruso'))
        response = self.client.post(reverse('dashboard:notification_read', args=[aviso.uuid]))
        self.assertEqual(response.status_code, 403)
        aviso.refresh_from_db()
        self.assertIsNone(aviso.read_at)


class NotificationListTests(TestCase):
    def setUp(self):
        self.admin = _superuser()
        self.other = _superuser('otro-admin')
        self.client.force_login(self.admin)

    def test_solo_muestra_los_avisos_del_destinatario(self):
        _notification(self.admin, title='Mio')
        _notification(self.other, title='Ajeno')

        response = self.client.get(reverse('dashboard:notifications'))

        self.assertContains(response, 'Mio')
        self.assertNotContains(response, 'Ajeno')

    def test_no_muestra_avisos_vencidos_aunque_no_se_hayan_podado(self):
        _notification(self.admin, title='Vigente')
        _notification(
            self.admin,
            title='Caducado',
            expires_at=timezone.now() - timedelta(hours=1),
        )

        response = self.client.get(reverse('dashboard:notifications'))

        self.assertContains(response, 'Vigente')
        self.assertNotContains(response, 'Caducado')

    def test_pagina_de_diez_en_diez(self):
        for index in range(12):
            _notification(self.admin, title=f'Aviso {index:02d}')

        response = self.client.get(reverse('dashboard:notifications'))

        self.assertEqual(len(response.context['notifications']), 10)
        self.assertEqual(response.context['page_obj'].paginator.num_pages, 2)

    def test_el_contador_del_contexto_coincide_con_los_sin_leer(self):
        _notification(self.admin, title='Sin leer 1')
        _notification(self.admin, title='Sin leer 2')
        _notification(self.admin, title='Leido', read=True)

        response = self.client.get(reverse('dashboard:notifications'))

        self.assertEqual(response.context['unread'], 2)

    def test_el_historial_se_titula_notificaciones(self):
        response = self.client.get(reverse('dashboard:notifications'))

        self.assertEqual(response.context['title'], 'Notificaciones')

    def test_el_historial_ofrece_eliminar_todas(self):
        _notification(self.admin, title='Mia')

        response = self.client.get(reverse('dashboard:notifications'))

        self.assertContains(response, 'Eliminar todas')

    def test_el_borrado_se_confirma_en_un_modal(self):
        aviso = _notification(self.admin, title='Mia')

        response = self.client.get(reverse('dashboard:notifications'))

        self.assertContains(response, 'id="notification-delete-modal"')
        self.assertContains(
            response,
            f'data-delete-url="{reverse("dashboard:notification_delete", args=[aviso.uuid])}"',
        )
        self.assertContains(
            response,
            f'data-delete-url="{reverse("dashboard:notifications_delete_all")}"',
        )
        self.assertNotContains(response, 'return confirm(')


class NotificationMarkReadTests(TestCase):
    def setUp(self):
        self.admin = _superuser()
        self.other = _superuser('otro-admin')
        self.client.force_login(self.admin)

    def test_marca_leida_solo_la_indicada(self):
        primera = _notification(self.admin, title='Primera')
        segunda = _notification(self.admin, title='Segunda')

        response = self.client.post(reverse('dashboard:notification_read', args=[primera.uuid]))

        self.assertEqual(response.status_code, 302)
        primera.refresh_from_db()
        segunda.refresh_from_db()
        self.assertIsNotNone(primera.read_at)
        self.assertIsNone(segunda.read_at)

    def test_no_se_puede_marcar_el_aviso_de_otro(self):
        ajeno = _notification(self.other, title='Ajeno')

        response = self.client.post(reverse('dashboard:notification_read', args=[ajeno.uuid]))

        self.assertEqual(response.status_code, 404)
        ajeno.refresh_from_db()
        self.assertIsNone(ajeno.read_at)

    def test_el_post_del_desplegable_recibe_204_sin_redireccion(self):
        aviso = _notification(self.admin, title='Pendiente')

        response = self.client.post(
            reverse('dashboard:notification_read', args=[aviso.uuid]),
            headers={'X-Requested-With': 'fetch'},
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b'')
        aviso.refresh_from_db()
        self.assertIsNotNone(aviso.read_at)

    def test_marcar_todas_no_toca_los_avisos_de_otro(self):
        _notification(self.admin, title='Mio')
        ajeno = _notification(self.other, title='Ajeno')

        self.client.post(reverse('dashboard:notifications_read_all'))

        self.assertEqual(
            Notification.objects.filter(recipient=self.admin, read_at__isnull=True).count(), 0
        )
        ajeno.refresh_from_db()
        self.assertIsNone(ajeno.read_at)

    def test_no_vuelve_a_un_destino_externo(self):
        aviso = _notification(self.admin, title='Mio')

        response = self.client.post(
            reverse('dashboard:notification_read', args=[aviso.uuid]),
            {'next': 'https://evil.example/robo'},
        )

        self.assertRedirects(
            response, reverse('dashboard:notifications'), fetch_redirect_response=False
        )


class NotificationNavbarTests(TestCase):
    def test_el_icono_aparece_para_el_superusuario(self):
        admin = _superuser()
        _notification(admin, title='Aviso visible')

        html = _navbar_html(admin)

        self.assertIn('notifications-navbar', html)
        self.assertIn('Aviso visible', html)

    def test_el_icono_no_aparece_para_un_usuario_normal(self):
        self.assertEqual(_navbar_html(_user()).strip(), '')

    def test_el_icono_no_aparece_para_un_staff_sin_superusuario(self):
        self.assertEqual(_navbar_html(_user('staff', staff=True)).strip(), '')

    @override_settings(NOTIFICATIONS_ENABLED=False)
    def test_el_icono_no_aparece_con_el_sistema_apagado(self):
        admin = _superuser()
        _notification(admin, title='Aviso visible')

        self.assertEqual(_navbar_html(admin).strip(), '')

    def test_el_badge_topea_en_9_mas(self):
        admin = _superuser()
        for index in range(12):
            _notification(admin, title=f'Aviso {index:02d}')

        html = _navbar_html(admin)

        self.assertIn('9+', html)

    def test_el_desplegable_muestra_como_maximo_las_configuradas(self):
        admin = _superuser()
        for index in range(12):
            _notification(admin, title=f'Aviso {index:02d}')

        html = _navbar_html(admin)

        self.assertEqual(html.count('Aviso '), 10)

    def test_el_desplegable_solo_lista_las_no_leidas(self):
        admin = _superuser()
        _notification(admin, title='Pendiente')
        _notification(admin, title='Ya leida', read=True)

        html = _navbar_html(admin)

        self.assertIn('Pendiente', html)
        self.assertNotIn('Ya leida', html)

    def test_el_desplegable_no_lleva_titulo_ni_marcar_todas(self):
        admin = _superuser()
        _notification(admin, title='Pendiente')

        html = _navbar_html(admin)

        # El pie alcanza con "Ver todas": el titulo de seccion y el boton de
        # marcar en bloque viven en el historial, no en el desplegable.
        self.assertNotIn('Marcar todas', html)
        self.assertNotIn('Avisos', html)
        self.assertIn('Ver todas', html)

    def test_el_desplegable_enlaza_al_marcado_sin_depender_de_js(self):
        admin = _superuser()
        aviso = _notification(admin, title='Pendiente')

        html = _navbar_html(admin)

        self.assertIn(
            f'data-notification-read="{reverse("dashboard:notification_read", args=[aviso.uuid])}"',
            html,
        )

    def test_el_icono_se_renderiza_en_el_navbar_del_panel(self):
        admin = _superuser()
        self.client.force_login(admin)

        response = self.client.get(reverse('dashboard:index'))

        self.assertContains(response, 'notifications-navbar')


class NotificationCounterTests(TestCase):
    def test_el_estado_de_actualizacion_no_lleva_el_contador(self):
        # El contador tiene su propio endpoint. Si volviera a viajar aqui, la
        # campana del portal (cambio 030) lo pediria junto a `deployed_sha()` y
        # `remote_sha()` en cada peticion de cada cliente.
        admin = _superuser()
        _notification(admin, title='Sin leer')
        self.client.force_login(admin)

        response = self.client.get(reverse('dashboard:update_status'))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('notifications', response.json())


class NotificationDeleteTests(TestCase):
    def setUp(self):
        self.admin = _superuser()
        self.other = _superuser('otro-admin')
        self.client.force_login(self.admin)

    def test_elimina_solo_la_indicada(self):
        primera = _notification(self.admin, title='Primera')
        segunda = _notification(self.admin, title='Segunda')

        response = self.client.post(reverse('dashboard:notification_delete', args=[primera.uuid]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Notification.objects.filter(pk=primera.pk).exists())
        self.assertTrue(Notification.objects.filter(pk=segunda.pk).exists())

    def test_no_se_puede_eliminar_la_de_otro(self):
        ajena = _notification(self.other, title='Ajena')

        response = self.client.post(reverse('dashboard:notification_delete', args=[ajena.uuid]))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Notification.objects.filter(pk=ajena.pk).exists())

    def test_eliminar_todas_borra_solo_las_propias(self):
        _notification(self.admin, title='Mia sin leer')
        _notification(self.admin, title='Mia leida', read=True)
        ajena = _notification(self.other, title='Ajena')

        response = self.client.post(reverse('dashboard:notifications_delete_all'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Notification.objects.filter(recipient=self.admin).count(), 0)
        self.assertTrue(Notification.objects.filter(pk=ajena.pk).exists())

    def test_eliminar_exige_post(self):
        aviso = _notification(self.admin, title='Mia')

        response = self.client.get(reverse('dashboard:notification_delete', args=[aviso.uuid]))

        self.assertEqual(response.status_code, 405)
        self.assertTrue(Notification.objects.filter(pk=aviso.pk).exists())

    def test_eliminar_todas_exige_post(self):
        _notification(self.admin, title='Mia')

        response = self.client.get(reverse('dashboard:notifications_delete_all'))

        self.assertEqual(response.status_code, 405)
        self.assertEqual(Notification.objects.filter(recipient=self.admin).count(), 1)

    def test_un_no_superusuario_no_puede_eliminar(self):
        aviso = _notification(self.admin, title='Mia')
        self.client.force_login(_user('intruso'))

        response = self.client.post(reverse('dashboard:notification_delete', args=[aviso.uuid]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Notification.objects.filter(pk=aviso.pk).exists())
