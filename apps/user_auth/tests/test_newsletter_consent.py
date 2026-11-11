"""Consentimiento del boletín desde el perfil: casilla y cambio de correo.

El signal de `Profile` tiene que estar registrado; si no, la casilla del perfil
no aplica nada. El registro se hace en `apps/user_auth/apps.py`, porque Django
no importa `signals.py` por su cuenta.
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from apps.core.models import Subscriber, Subscription
from apps.core.tests.base import get_default_list
from apps.user_auth.forms.profile import ProfileForm


class NewsletterProfileSignalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.default_list = get_default_list()

    def test_crear_un_usuario_no_crea_suscriptor(self):
        User.objects.create_user('nuevo', 'nuevo@ejemplo.cu', 'pass')

        self.assertFalse(Subscriber.objects.filter(email='nuevo@ejemplo.cu').exists())

    def test_marcar_la_casilla_en_el_perfil_crea_la_suscripcion(self):
        user = User.objects.create_user('marca', 'marca@ejemplo.cu', 'pass')
        profile = user.profile

        profile.newsletter = True
        profile.save()

        subscriber = Subscriber.objects.get(email='marca@ejemplo.cu')
        self.assertIsNotNone(subscriber.confirmed_at)
        self.assertTrue(subscriber.active)
        self.assertTrue(
            Subscription.objects.filter(
                subscriber=subscriber, recipient_list=self.default_list
            ).exists()
        )

    def test_desmarcar_la_casilla_quita_la_pertenencia_y_apaga_el_activo(self):
        user = User.objects.create_user('desmarca', 'desmarca@ejemplo.cu', 'pass')
        profile = user.profile
        profile.newsletter = True
        profile.save()
        subscriber = Subscriber.objects.get(email='desmarca@ejemplo.cu')

        profile.newsletter = False
        profile.save()

        subscriber.refresh_from_db()
        self.assertFalse(subscriber.active)
        self.assertFalse(
            Subscription.objects.filter(
                subscriber=subscriber, recipient_list=self.default_list
            ).exists()
        )

    def test_guardar_el_perfil_sin_cambiar_la_casilla_no_toca_el_consentimiento(self):
        """Un guardado cualquiera no puede dar de baja a un suscriptor del portal."""
        user = User.objects.create_user('intacto', 'intacto@ejemplo.cu', 'pass')
        subscriber = Subscriber.objects.create(
            email='intacto@ejemplo.cu', source='landing', confirmed_at=timezone.now()
        )
        Subscription.objects.create(
            subscriber=subscriber, recipient_list=self.default_list, source='landing'
        )

        profile = user.profile
        profile.first_name = 'Otro'
        profile.save()

        subscriber.refresh_from_db()
        self.assertTrue(subscriber.active)
        self.assertTrue(
            Subscription.objects.filter(
                subscriber=subscriber, recipient_list=self.default_list
            ).exists()
        )


class ProfileFormEmailMoveTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.default_list = get_default_list()

    def test_cambiar_el_correo_mueve_la_suscripcion(self):
        user = User.objects.create_user('mueve', 'viejo@ejemplo.cu', 'pass')
        profile = user.profile
        profile.newsletter = True
        profile.save()

        form = ProfileForm(
            data={
                'first_name': 'Nombre',
                'last_name': 'Apellido',
                'email': 'nuevo@ejemplo.cu',
                'newsletter': 'on',
            },
            instance=profile,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        self.assertFalse(Subscriber.objects.filter(email='viejo@ejemplo.cu').exists())
        subscriber = Subscriber.objects.get(email='nuevo@ejemplo.cu')
        self.assertTrue(subscriber.active)
        self.assertTrue(
            Subscription.objects.filter(
                subscriber=subscriber, recipient_list=self.default_list
            ).exists()
        )
