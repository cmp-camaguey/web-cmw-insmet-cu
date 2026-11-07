"""Poda de avisos (cambio 026, fase 4).

Se prueba la funcion, no el comando: el comando es un `stdout.write` alrededor de
`prune_notifications()`, y la tarea periodica llama a lo mismo. Lo que puede
romperse de verdad son las tres ventanas de retencion.
"""

from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.models import Notification
from apps.core.notifications import prune_notifications


def _admin():
    return User.objects.create_superuser('admin', 'admin@example.com', 'pass')


def _notification(recipient, title, *, created_days_ago=0, read_days_ago=None, expires_days=None):
    """Crea un aviso con fechas movidas a mano.

    `auto_now_add` y `auto_now` ignoran lo que se pase en `create()`, asi que
    despues del INSERT se corrige con un `update()`: es la unica forma de
    simular antiguedad sin congelar el reloj.
    """
    obj = Notification.objects.create(
        recipient=recipient,
        severity='error',
        source='test',
        title=title,
        dedupe_key=title.ljust(64, 'x')[:64],
    )
    update = {'created_at': timezone.now() - timedelta(days=created_days_ago)}
    if read_days_ago is not None:
        update['read_at'] = timezone.now() - timedelta(days=read_days_ago)
    if expires_days is not None:
        update['expires_at'] = timezone.now() + timedelta(days=expires_days)
    Notification.objects.filter(pk=obj.pk).update(**update)
    obj.refresh_from_db()
    return obj


@override_settings(
    NOTIFICATIONS_RETENTION_READ=30,
    NOTIFICATIONS_RETENTION_UNREAD=90,
)
class PruneNotificationsTests(TestCase):
    def setUp(self):
        self.admin = _admin()

    def test_borra_las_leidas_fuera_de_retencion(self):
        _notification(self.admin, 'Vieja leida', created_days_ago=60, read_days_ago=40)

        deleted = prune_notifications()

        self.assertEqual(deleted['read'], 1)
        self.assertFalse(Notification.objects.filter(title='Vieja leida').exists())

    def test_conserva_las_leidas_dentro_de_retencion(self):
        _notification(self.admin, 'Leida reciente', created_days_ago=10, read_days_ago=5)

        deleted = prune_notifications()

        self.assertEqual(deleted['read'], 0)
        self.assertTrue(Notification.objects.filter(title='Leida reciente').exists())

    def test_borra_las_no_leidas_fuera_de_retencion(self):
        _notification(self.admin, 'Vieja sin leer', created_days_ago=120)

        deleted = prune_notifications()

        self.assertEqual(deleted['unread'], 1)
        self.assertFalse(Notification.objects.filter(title='Vieja sin leer').exists())

    def test_conserva_las_no_leidas_recientes(self):
        _notification(self.admin, 'Sin leer reciente', created_days_ago=5)

        deleted = prune_notifications()

        self.assertEqual(deleted['unread'], 0)
        self.assertTrue(Notification.objects.filter(title='Sin leer reciente').exists())

    def test_borra_las_vencidas_aunque_sean_recientes(self):
        _notification(self.admin, 'Vencida', expires_days=-1)

        deleted = prune_notifications()

        self.assertEqual(deleted['expired'], 1)
        self.assertFalse(Notification.objects.filter(title='Vencida').exists())

    def test_conserva_las_que_vencen_en_el_futuro(self):
        _notification(self.admin, 'Vigente', expires_days=2)

        deleted = prune_notifications()

        self.assertEqual(deleted['expired'], 0)
        self.assertTrue(Notification.objects.filter(title='Vigente').exists())

    def test_es_idempotente(self):
        _notification(self.admin, 'Vieja leida', created_days_ago=60, read_days_ago=40)
        _notification(self.admin, 'Vieja sin leer', created_days_ago=120)
        _notification(self.admin, 'Vencida', expires_days=-1)

        first = prune_notifications()
        second = prune_notifications()

        self.assertEqual(sum(first.values()), 3)
        self.assertEqual(second, {'expired': 0, 'read': 0, 'unread': 0})

    def test_el_comando_informa_lo_que_borro(self):
        _notification(self.admin, 'Vencida', expires_days=-1)
        stdout = StringIO()

        call_command('notificar_podar', stdout=stdout)

        self.assertIn('1 vencidos', stdout.getvalue())

    def test_la_poda_corre_tambien_con_el_sistema_apagado(self):
        """Apagar los avisos apaga la creacion, no el mantenimiento de la tabla."""
        _notification(self.admin, 'Vencida', expires_days=-1)

        with override_settings(NOTIFICATIONS_ENABLED=False):
            deleted = prune_notifications()

        self.assertEqual(deleted['expired'], 1)
