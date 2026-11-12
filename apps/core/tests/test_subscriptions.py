"""Consentimiento de correo: alta, confirmación, baja y movimiento de dirección.

Ver `openspec/specs/boletin-y-suscripcion/spec.md`.
"""

import uuid
from datetime import timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import EmailRecipientList, Subscriber, Subscription
from apps.core.subscriptions import (
    CONFIRM_EXPIRED,
    CONFIRM_INVALID,
    CONFIRM_OK,
    confirm,
    move_subscription,
    purge_stale_pending,
    remind_pending_subscribers,
    set_consent,
    subscribe,
    unsubscribe,
)
from apps.core.tasks import mantener_suscriptores_pendientes
from apps.core.tests.base import get_default_list
from config.huey import huey


class NewsletterFixtureMixin:
    @classmethod
    def setUpTestData(cls):
        cls.default_list = get_default_list()
        cls.other_list = EmailRecipientList.objects.create(name='Prensa')


class SubscribeTests(NewsletterFixtureMixin, TestCase):
    def test_alta_desde_landing_queda_pendiente_y_pide_confirmacion(self):
        subscriber, created, needs_confirmation = subscribe(
            'Nueva@Ejemplo.CU ', source=Subscriber.Source.LANDING
        )

        self.assertTrue(created)
        self.assertTrue(needs_confirmation)
        self.assertEqual(subscriber.email, 'nueva@ejemplo.cu')
        self.assertIsNone(subscriber.confirmed_at)
        self.assertIsNotNone(subscriber.confirmation_token)
        self.assertTrue(
            Subscription.objects.filter(
                subscriber=subscriber, recipient_list=self.default_list
            ).exists()
        )

    def test_un_suscriptor_confirmado_que_repite_no_recibe_otro_correo(self):
        subscriber, _created, _needs = subscribe(
            'repite@ejemplo.cu', source=Subscriber.Source.LANDING
        )
        confirm(subscriber.confirmation_token)

        _subscriber, created, needs_confirmation = subscribe(
            'repite@ejemplo.cu', source=Subscriber.Source.LANDING
        )

        self.assertFalse(created)
        self.assertFalse(needs_confirmation)

    def test_quien_se_dio_de_baja_debe_confirmar_de_nuevo(self):
        subscriber, _created, _needs = subscribe(
            'vuelve@ejemplo.cu', source=Subscriber.Source.LANDING
        )
        confirm(subscriber.confirmation_token)
        unsubscribe(subscriber.unsubscribe_token)

        _subscriber, _created, needs_confirmation = subscribe(
            'vuelve@ejemplo.cu', source=Subscriber.Source.LANDING
        )

        self.assertTrue(needs_confirmation)

    def test_sin_lista_principal_no_se_suscribe_y_no_revienta(self):
        EmailRecipientList.objects.filter(pk=self.default_list.pk).update(is_default=False)

        subscriber, created, needs_confirmation = subscribe(
            'sinlista@ejemplo.cu', source=Subscriber.Source.LANDING
        )

        self.assertIsNone(subscriber)
        self.assertFalse(created)
        self.assertFalse(needs_confirmation)

    def test_solo_una_lista_puede_ser_la_principal(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            EmailRecipientList.objects.create(name='Otra principal', is_default=True)


class ConfirmTests(NewsletterFixtureMixin, TestCase):
    def test_token_valido_confirma_y_se_anula(self):
        subscriber, _created, _needs = subscribe(
            'confirma@ejemplo.cu', source=Subscriber.Source.LANDING
        )
        token = subscriber.confirmation_token

        confirmed, status = confirm(token)

        self.assertEqual(status, CONFIRM_OK)
        self.assertIsNotNone(confirmed.confirmed_at)
        self.assertTrue(confirmed.active)
        self.assertIsNone(confirmed.confirmation_token)

        _again, second_status = confirm(token)
        self.assertEqual(second_status, CONFIRM_INVALID)

    def test_token_caducado_no_confirma(self):
        subscriber, _created, _needs = subscribe(
            'caduca@ejemplo.cu', source=Subscriber.Source.LANDING
        )
        Subscriber.objects.filter(pk=subscriber.pk).update(
            confirmation_sent_at=timezone.now() - timedelta(hours=73)
        )

        expired, status = confirm(subscriber.confirmation_token)

        self.assertEqual(status, CONFIRM_EXPIRED)
        self.assertIsNone(Subscriber.objects.get(pk=expired.pk).confirmed_at)

    def test_token_inexistente_es_invalido(self):
        _subscriber, status = confirm('00000000-0000-0000-0000-000000000000')
        self.assertEqual(status, CONFIRM_INVALID)


class UnsubscribeTests(NewsletterFixtureMixin, TestCase):
    def setUp(self):
        # El aviso de baja sale por `send_email_task`: sin `immediate` la tarea
        # queda en la cola de Huey y `mail.outbox` nunca la ve.
        self._old_immediate = huey.immediate
        huey.immediate = True

    def tearDown(self):
        huey.immediate = self._old_immediate

    def test_baja_desactiva_sin_borrar_la_fila(self):
        subscriber, _created, _needs = subscribe(
            'baja@ejemplo.cu', source=Subscriber.Source.LANDING
        )
        confirm(subscriber.confirmation_token)

        result = unsubscribe(subscriber.unsubscribe_token)

        self.assertIsNotNone(result)
        subscriber.refresh_from_db()
        self.assertFalse(subscriber.active)
        self.assertTrue(Subscriber.objects.filter(pk=subscriber.pk).exists())

    def test_baja_es_idempotente(self):
        subscriber, _created, _needs = subscribe(
            'idempotente@ejemplo.cu', source=Subscriber.Source.LANDING
        )
        unsubscribe(subscriber.unsubscribe_token)

        second = unsubscribe(subscriber.unsubscribe_token)

        self.assertIsNotNone(second)

    def test_la_baja_avisa_al_suscriptor_que_quedo_registrada(self):
        """Sin aviso, una baja silenciosa es indistinguible de un enlace roto."""
        subscriber, _created, _needs = subscribe(
            'aviso-baja@ejemplo.cu', source=Subscriber.Source.LANDING
        )
        confirm(subscriber.confirmation_token)
        mail.outbox = []

        unsubscribe(subscriber.unsubscribe_token)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['aviso-baja@ejemplo.cu'])
        self.assertIn('quedó fuera de la lista', mail.outbox[0].body)
        self.assertIn(reverse('home:newsletter_subscribe'), mail.outbox[0].body)

    def test_el_aviso_de_baja_no_ofrece_darse_de_baja_otra_vez(self):
        """El pie no puede enlazar una baja a quien acaba de darse de baja."""
        subscriber, _created, _needs = subscribe(
            'pie-baja@ejemplo.cu', source=Subscriber.Source.LANDING
        )
        confirm(subscriber.confirmation_token)
        mail.outbox = []

        unsubscribe(subscriber.unsubscribe_token)

        self.assertNotIn('Darse de baja', mail.outbox[0].body)

    def test_una_segunda_baja_no_reenvia_el_aviso(self):
        """El enlace es reabrible: el aviso dice «quedó registrada», no dos veces."""
        subscriber, _created, _needs = subscribe(
            'doble-aviso@ejemplo.cu', source=Subscriber.Source.LANDING
        )
        confirm(subscriber.confirmation_token)
        unsubscribe(subscriber.unsubscribe_token)
        mail.outbox = []

        unsubscribe(subscriber.unsubscribe_token)

        self.assertEqual(len(mail.outbox), 0)

    def test_baja_saca_al_suscriptor_de_la_lista_principal(self):
        subscriber, _created, _needs = subscribe(
            'lista@ejemplo.cu', source=Subscriber.Source.LANDING
        )
        self.assertTrue(
            Subscription.objects.filter(
                subscriber=subscriber, recipient_list=self.default_list
            ).exists()
        )

        unsubscribe(subscriber.unsubscribe_token)

        self.assertFalse(
            Subscription.objects.filter(
                subscriber=subscriber, recipient_list=self.default_list
            ).exists()
        )

    def test_baja_apaga_la_casilla_del_perfil(self):
        """Sin esto el perfil seguiría diciendo que acepta recibir novedades."""
        user = User.objects.create_user('casilla', 'casilla@ejemplo.cu', 'pass')
        profile = user.profile
        profile.newsletter = True
        profile.save()
        subscriber = Subscriber.objects.get(email='casilla@ejemplo.cu')

        unsubscribe(subscriber.unsubscribe_token)

        profile.refresh_from_db()
        self.assertFalse(profile.newsletter)

    def test_baja_no_vuelve_a_dar_de_alta_al_guardar_el_perfil(self):
        """La baja apaga la casilla, así que un guardado posterior no re-suscribe.

        Antes la baja solo ponía `active=False`: la casilla seguía marcada y
        cualquier guardado del perfil reaplicaba el consentimiento y devolvía a
        la persona a la lista sin que lo hubiera pedido.
        """
        user = User.objects.create_user('reaplica', 'reaplica@ejemplo.cu', 'pass')
        profile = user.profile
        profile.newsletter = True
        profile.save()
        subscriber = Subscriber.objects.get(email='reaplica@ejemplo.cu')

        unsubscribe(subscriber.unsubscribe_token)
        profile.refresh_from_db()
        profile.save()

        subscriber.refresh_from_db()
        self.assertFalse(subscriber.active)
        self.assertFalse(
            Subscription.objects.filter(
                subscriber=subscriber, recipient_list=self.default_list
            ).exists()
        )

    def test_token_de_baja_inexistente_no_borra_nada(self):
        self.assertIsNone(unsubscribe('00000000-0000-0000-0000-000000000000'))


class SetConsentTests(NewsletterFixtureMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            'cliente', 'cliente@ejemplo.cu', 'pass', first_name='Cli', last_name='Ente'
        )

    def test_marcar_la_casilla_confirma_sin_doble_opt_in(self):
        subscriber = set_consent(self.user, True, source=Subscriber.Source.PERFIL)

        self.assertIsNotNone(subscriber.confirmed_at)
        self.assertTrue(
            Subscription.objects.filter(
                subscriber=subscriber, recipient_list=self.default_list
            ).exists()
        )

    def test_desmarcar_quita_la_pertenencia_y_conserva_al_suscriptor(self):
        subscriber = set_consent(self.user, True, source=Subscriber.Source.PERFIL)

        set_consent(self.user, False, source=Subscriber.Source.PERFIL)

        self.assertFalse(
            Subscription.objects.filter(
                subscriber=subscriber, recipient_list=self.default_list
            ).exists()
        )
        self.assertTrue(Subscriber.objects.filter(pk=subscriber.pk).exists())

    def test_misma_direccion_en_dos_listas_no_duplica_la_persona(self):
        subscriber = set_consent(self.user, True, source=Subscriber.Source.PERFIL)
        Subscription.objects.create(
            subscriber=subscriber, recipient_list=self.other_list, source='admin'
        )

        self.assertEqual(Subscriber.objects.filter(email='cliente@ejemplo.cu').count(), 1)
        self.assertEqual(subscriber.subscriptions.count(), 2)


class MoveSubscriptionTests(NewsletterFixtureMixin, TestCase):
    def test_cambiar_el_correo_mueve_la_suscripcion(self):
        subscriber, _created, _needs = subscribe(
            'viejo@ejemplo.cu', source=Subscriber.Source.LANDING
        )
        confirm(subscriber.confirmation_token)

        moved = move_subscription('viejo@ejemplo.cu', 'nuevo@ejemplo.cu')

        self.assertIsNotNone(moved)
        self.assertEqual(moved.email, 'nuevo@ejemplo.cu')
        self.assertFalse(Subscriber.objects.filter(email='viejo@ejemplo.cu').exists())
        self.assertTrue(
            Subscription.objects.filter(subscriber=moved, recipient_list=self.default_list).exists()
        )

    def test_si_la_direccion_nueva_ya_existe_se_fusionan_las_pertenencias(self):
        original, _created, _needs = subscribe(
            'original@ejemplo.cu', source=Subscriber.Source.LANDING
        )
        confirm(original.confirmation_token)
        existing = Subscriber.objects.create(
            email='existente@ejemplo.cu', source='admin', confirmed_at=timezone.now()
        )

        moved = move_subscription('original@ejemplo.cu', 'existente@ejemplo.cu')

        self.assertEqual(moved.pk, existing.pk)
        self.assertTrue(
            Subscription.objects.filter(
                subscriber=existing, recipient_list=self.default_list
            ).exists()
        )
        self.assertFalse(Subscriber.objects.get(pk=original.pk).active)

    @override_settings(NEWSLETTER_CONFIRMATION_TTL_HOURS=1)
    def test_el_ttl_se_lee_de_settings(self):
        from apps.core.subscriptions import confirmation_ttl

        self.assertEqual(confirmation_ttl(), timedelta(hours=1))


class PendingSubscriberMaintenanceTests(NewsletterFixtureMixin, TestCase):
    """Recordatorio único a las 48 h y limpieza a los 7 días (S3).

    Las dos tareas existen porque un alta sin confirmar es invisible: la persona
    no recibe nada más y la fila se queda ahí. El recordatorio le da una segunda
    oportunidad; la limpieza evita que la tabla crezca con gente que nunca entró
    en la lista.
    """

    def setUp(self):
        # `send_confirmation` encola por Huey: sin `immediate` la tarea queda en
        # la cola y `mail.outbox` no ve nada.
        self._old_immediate = huey.immediate
        huey.immediate = True
        mail.outbox = []

    def tearDown(self):
        huey.immediate = self._old_immediate

    def _pending(self, email, *, hours_ago=0, days_ago=0):
        """Suscriptor sin confirmar, con su antigüedad fijada a mano.

        `created_at` es `auto_now_add`, así que se corrige con `update()`: el
        `save()` lo volvería a poner en «ahora» y el test no probaría nada.
        """
        subscriber = Subscriber.objects.create(email=email, source=Subscriber.Source.LANDING)
        Subscription.objects.create(
            subscriber=subscriber,
            recipient_list=self.default_list,
            source=Subscriber.Source.LANDING,
        )
        sent_at = timezone.now() - timedelta(hours=hours_ago, days=days_ago)
        Subscriber.objects.filter(pk=subscriber.pk).update(
            confirmation_token=uuid.uuid4(),
            confirmation_sent_at=sent_at,
            created_at=sent_at,
        )
        subscriber.refresh_from_db()
        return subscriber

    def test_el_pendiente_de_dos_dias_recibe_un_recordatorio(self):
        subscriber = self._pending('tarde@ejemplo.cu', hours_ago=49)

        sent = remind_pending_subscribers()

        self.assertEqual(sent, 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['tarde@ejemplo.cu'])
        self.assertIn('suscripcion/confirmar/', mail.outbox[0].body)
        subscriber.refresh_from_db()
        self.assertIsNotNone(subscriber.reminder_sent_at)

    def test_el_recordatorio_no_se_repite(self):
        self._pending('insistente@ejemplo.cu', hours_ago=49)
        remind_pending_subscribers()

        sent = remind_pending_subscribers()

        self.assertEqual(sent, 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_el_pendiente_reciente_no_recibe_recordatorio(self):
        self._pending('recien@ejemplo.cu', hours_ago=10)

        self.assertEqual(remind_pending_subscribers(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_un_confirmado_nunca_recibe_recordatorio(self):
        subscriber = self._pending('ya-confirmo@ejemplo.cu', hours_ago=49)
        Subscriber.objects.filter(pk=subscriber.pk).update(confirmed_at=timezone.now())

        self.assertEqual(remind_pending_subscribers(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_el_recordatorio_conserva_el_token_todavia_vigente(self):
        """Invalidar el enlace del correo original a las 48 h sería un defecto."""
        subscriber = self._pending('vigente@ejemplo.cu', hours_ago=49)
        token = subscriber.confirmation_token

        remind_pending_subscribers()

        subscriber.refresh_from_db()
        self.assertEqual(subscriber.confirmation_token, token)
        self.assertIn(str(token), mail.outbox[0].body)

    def test_el_pendiente_de_ocho_dias_se_borra(self):
        subscriber = self._pending('perdido@ejemplo.cu', days_ago=8)

        deleted = purge_stale_pending()

        self.assertEqual(deleted, 1)
        self.assertFalse(Subscriber.objects.filter(pk=subscriber.pk).exists())
        self.assertFalse(Subscription.objects.filter(subscriber_id=subscriber.pk).exists())

    def test_un_confirmado_viejo_no_se_borra(self):
        subscriber = self._pending('antiguo@ejemplo.cu', days_ago=30)
        Subscriber.objects.filter(pk=subscriber.pk).update(confirmed_at=timezone.now())

        self.assertEqual(purge_stale_pending(), 0)
        self.assertTrue(Subscriber.objects.filter(pk=subscriber.pk).exists())

    def test_una_baja_sin_confirmar_no_se_borra_por_antigua(self):
        """Quien pidió la baja no es un alta olvidada: se conserva el rastro."""
        subscriber = self._pending('se-fue@ejemplo.cu', days_ago=30)
        Subscriber.objects.filter(pk=subscriber.pk).update(active=False)

        self.assertEqual(purge_stale_pending(), 0)
        self.assertTrue(Subscriber.objects.filter(pk=subscriber.pk).exists())

    def test_la_tarea_limpia_antes_de_recordar(self):
        """Al que se borra no se le manda un enlace que ya no existe."""
        borrado = self._pending('borrado@ejemplo.cu', days_ago=8)
        self._pending('recordado@ejemplo.cu', hours_ago=49)

        # `huey.immediate` hace que la tarea corra en el acto, pero devuelve un
        # `Result` y no el valor de retorno: lo que se comprueba son los efectos.
        mantener_suscriptores_pendientes()

        self.assertFalse(Subscriber.objects.filter(pk=borrado.pk).exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['recordado@ejemplo.cu'])
