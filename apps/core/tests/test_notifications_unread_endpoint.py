"""Endpoint del contador de avisos sin leer (cambio 026).

El numero vivia dentro del JSON de estado de despliegue. Se separo porque ese
JSON lee archivos y consulta el remoto en cada peticion, y la campana del portal
(cambio 030) lo va a pedir a menudo. Lo que se prueba aqui es la separacion: que
el endpoint solo cuente, que no acepte a quien no debe, y que el cache no pueda
servir un numero que la base ya no sostiene.
"""

from datetime import timedelta

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Notification
from apps.core.notifications import (
    cached_unread_count,
    invalidate_unread_count,
    notify,
    unread_count_cache_key,
)

URL_NAME = 'notifications:unread'


def _superuser(username='admin'):
    return User.objects.create_superuser(
        username,
        f'{username}@example.com',
        'pass',
        first_name='Admin',
        last_name='Test',
    )


def _user(username='cliente'):
    return User.objects.create_user(
        username,
        f'{username}@example.com',
        'pass',
        first_name='Cliente',
        last_name='Test',
    )


def _notification(recipient, *, title='Aviso', read=False, expires_at=None):
    obj = Notification.objects.create(
        recipient=recipient,
        severity=Notification.Severity.ERROR,
        source='test',
        title=title,
        dedupe_key=f'{recipient.pk}:{title}'.ljust(64, 'x')[:64],
        expires_at=expires_at,
    )
    if read:
        Notification.objects.filter(pk=obj.pk).update(read_at=timezone.now())
    return obj


class UnreadEndpointTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = _superuser()

    def _get(self, **headers):
        self.client.force_login(self.admin)
        return self.client.get(reverse(URL_NAME), headers=headers)

    def test_devuelve_solo_los_que_quedan_sin_leer(self):
        _notification(self.admin, title='Pendiente')
        _notification(self.admin, title='Leido', read=True)

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'unread': 1})

    def test_un_aviso_vencido_no_cuenta(self):
        # `expires_at` es la fecha del hecho: la poda es mantenimiento y puede
        # llegar despues, pero el badge no puede seguir anunciandolo.
        _notification(
            self.admin,
            title='Vencido',
            expires_at=timezone.now() - timedelta(minutes=1),
        )

        self.assertEqual(self._get().json(), {'unread': 0})

    def test_el_etag_evita_reenviar_el_cuerpo(self):
        _notification(self.admin, title='Pendiente')
        first = self._get()
        etag = first['ETag']

        second = self._get(**{'If-None-Match': etag})

        self.assertEqual(second.status_code, 304)
        self.assertEqual(second.content, b'')
        self.assertEqual(second['ETag'], etag)

    def test_el_etag_cambia_cuando_cambia_el_conteo(self):
        first = self._get()
        _notification(self.admin, title='Nuevo')

        second = self._get(**{'If-None-Match': first['ETag']})

        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json(), {'unread': 1})

    def test_no_responde_a_un_usuario_normal(self):
        self.client.force_login(_user())

        self.assertEqual(self.client.get(reverse(URL_NAME)).status_code, 403)

    def test_no_responde_a_un_anonimo(self):
        self.assertEqual(self.client.get(reverse(URL_NAME)).status_code, 403)

    @override_settings(NOTIFICATIONS_ENABLED=False)
    def test_con_el_sistema_apagado_el_conteo_es_nulo(self):
        _notification(self.admin, title='Pendiente')

        # `null` y no `0`: el JS esconde el badge con uno y pinta "sin badge"
        # con el otro, igual que el render del servidor.
        self.assertIsNone(self._get().json()['unread'])

    def test_la_respuesta_no_se_guarda_en_caches_intermedios(self):
        response = self._get()

        self.assertIn('private', response['Cache-Control'])
        self.assertIn('no-cache', response['Cache-Control'])


class UnreadCountCacheTests(TestCase):
    """El cache se usa solo con Redis, y solo cuando se puede invalidar."""

    def setUp(self):
        cache.clear()
        self.admin = _superuser()

    @override_settings(USE_REDIS_CACHE=False)
    def test_sin_cache_compartido_se_consulta_la_base_cada_vez(self):
        with self.assertNumQueries(1):
            cached_unread_count(self.admin)
        with self.assertNumQueries(1):
            cached_unread_count(self.admin)

    @override_settings(USE_REDIS_CACHE=True)
    def test_con_cache_compartido_la_segunda_lectura_no_va_a_la_base(self):
        cached_unread_count(self.admin)

        with self.assertNumQueries(0):
            self.assertEqual(cached_unread_count(self.admin), 0)

    @override_settings(USE_REDIS_CACHE=True)
    def test_invalidar_obliga_a_recalcular(self):
        _notification(self.admin, title='Pendiente')
        self.assertEqual(cached_unread_count(self.admin), 1)

        invalidate_unread_count(self.admin)

        with self.assertNumQueries(1):
            self.assertEqual(cached_unread_count(self.admin), 1)

    @override_settings(USE_REDIS_CACHE=True)
    def test_crear_un_aviso_invalida_el_cache_del_destinatario(self):
        # El aviso lo puede crear un worker de Huey, que es otro proceso: si la
        # invalidacion no cruzara, el badge se quedaria atras hasta el TTL.
        cached_unread_count(self.admin)
        self.assertIsNotNone(cache.get(unread_count_cache_key(self.admin)))

        notify(
            self.admin,
            source='test',
            severity=Notification.Severity.ERROR,
            title='Nuevo',
            dedupe_key='nuevo'.ljust(64, 'x'),
        )

        self.assertIsNone(cache.get(unread_count_cache_key(self.admin)))

    @override_settings(USE_REDIS_CACHE=True)
    def test_marcar_leido_invalida_el_cache(self):
        aviso = _notification(self.admin, title='Pendiente')
        cached_unread_count(self.admin)
        self.client.force_login(self.admin)

        self.client.post(reverse('dashboard:notification_read', args=[aviso.uuid]))

        self.assertIsNone(cache.get(unread_count_cache_key(self.admin)))
