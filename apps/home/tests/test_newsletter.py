"""Suscripción pública al boletín: alta, confirmación y baja sin cuenta.

Ver `odd/tasks/030-boletin-y-suscripcion.md` S5–S8 y S12.
"""

from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Subscriber, Subscription
from apps.core.tests.base import get_default_list
from apps.user_auth.models import Profile
from config.huey import huey

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


class NewsletterViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.default_list = get_default_list()

    def setUp(self):
        cache.clear()
        self._old_immediate = huey.immediate
        huey.immediate = True

    def tearDown(self):
        huey.immediate = self._old_immediate

    @override_settings(EMAIL_BACKEND=LOCMEM, SITE_URL='http://testserver/')
    def test_alta_valida_crea_pendiente_y_manda_confirmacion(self):
        response = self.client.post(
            reverse('home:newsletter_subscribe'), {'email': 'nueva@ejemplo.cu'}
        )

        self.assertEqual(response.status_code, 302)
        subscriber = Subscriber.objects.get(email='nueva@ejemplo.cu')
        self.assertIsNone(subscriber.confirmed_at)
        self.assertTrue(
            Subscription.objects.filter(
                subscriber=subscriber, recipient_list=self.default_list
            ).exists()
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('suscripcion/confirmar/', mail.outbox[0].body)

    def test_correo_invalido_no_crea_nada(self):
        response = self.client.post(reverse('home:newsletter_subscribe'), {'email': 'no-es-correo'})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Subscriber.objects.exists())

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_el_limite_por_ip_corta_los_intentos(self):
        # Un suscriptor pendiente regenera token en cada alta, así que cada
        # petición permitida deja un correo. El tercer intento no debe dejarlo.
        with override_settings(NEWSLETTER_SUBSCRIBE_LIMIT=2):
            for _ in range(3):
                self.client.post(
                    reverse('home:newsletter_subscribe'), {'email': 'limite@ejemplo.cu'}
                )

        self.assertEqual(len(mail.outbox), 2)

    def test_confirmar_con_token_valido_confirma(self):
        self.client.post(reverse('home:newsletter_subscribe'), {'email': 'confirma@ejemplo.cu'})
        subscriber = Subscriber.objects.get(email='confirma@ejemplo.cu')

        response = self.client.get(
            reverse('home:newsletter_confirm', args=[subscriber.confirmation_token])
        )

        self.assertEqual(response.status_code, 200)
        subscriber.refresh_from_db()
        self.assertIsNotNone(subscriber.confirmed_at)

    def test_confirmar_con_token_usado_explica_sin_404(self):
        self.client.post(reverse('home:newsletter_subscribe'), {'email': 'usado@ejemplo.cu'})
        subscriber = Subscriber.objects.get(email='usado@ejemplo.cu')
        url = reverse('home:newsletter_confirm', args=[subscriber.confirmation_token])
        self.client.get(url)

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Enlace no válido')

    def test_la_pagina_de_confirmacion_usa_el_mismo_layout_que_la_baja(self):
        """Confirmar y darse de baja son el mismo trámite: misma página, sin menús."""
        self.client.post(reverse('home:newsletter_subscribe'), {'email': 'mismo@ejemplo.cu'})
        subscriber = Subscriber.objects.get(email='mismo@ejemplo.cu')

        response = self.client.get(
            reverse('home:newsletter_confirm', args=[subscriber.confirmation_token])
        )

        self.assertContains(response, 'Suscripción confirmada')
        self.assertContains(response, 'dist/img/webp/good-news.webp')
        self.assertNotContains(response, 'class="navbar')

    def test_confirmar_desde_el_portal_marca_la_casilla_del_perfil(self):
        """Un usuario registrado no puede quedar suscrito y con la casilla apagada.

        La suscripción pública y la casilla `Profile.newsletter` son dos vistas
        del mismo consentimiento: si la persona confirma desde el landing, su
        perfil tiene que decir lo mismo que la lista de correo.
        """
        user = User.objects.create_user(
            'registrado', 'registrado@ejemplo.cu', 'pass', first_name='Re', last_name='Gistrado'
        )
        self.assertFalse(Profile.objects.get(user=user).newsletter)

        self.client.post(reverse('home:newsletter_subscribe'), {'email': 'registrado@ejemplo.cu'})
        subscriber = Subscriber.objects.get(email='registrado@ejemplo.cu')
        self.client.get(reverse('home:newsletter_confirm', args=[subscriber.confirmation_token]))

        self.assertTrue(Profile.objects.get(user=user).newsletter)

    def test_confirmar_un_correo_ajeno_no_toca_ningun_perfil(self):
        """Suscribir la dirección del trabajo no marca la casilla de otra cuenta."""
        user = User.objects.create_user(
            'otro', 'otro@ejemplo.cu', 'pass', first_name='Otro', last_name='Usuario'
        )

        self.client.post(reverse('home:newsletter_subscribe'), {'email': 'ajeno@ejemplo.cu'})
        subscriber = Subscriber.objects.get(email='ajeno@ejemplo.cu')
        self.client.get(reverse('home:newsletter_confirm', args=[subscriber.confirmation_token]))

        self.assertFalse(Profile.objects.get(user=user).newsletter)

    def test_baja_funciona_sin_login_y_es_idempotente(self):
        subscriber = Subscriber.objects.create(
            email='baja@ejemplo.cu', source='landing', confirmed_at=timezone.now()
        )
        url = reverse('home:newsletter_unsubscribe', args=[subscriber.unsubscribe_token])

        first = self.client.get(url)
        second = self.client.get(url)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        subscriber.refresh_from_db()
        self.assertFalse(subscriber.active)
        self.assertTrue(Subscriber.objects.filter(pk=subscriber.pk).exists())

    def test_baja_con_token_inexistente_no_da_404(self):
        response = self.client.get(
            reverse(
                'home:newsletter_unsubscribe',
                args=['00000000-0000-0000-0000-000000000000'],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Enlace no válido')

    def test_la_pagina_de_baja_usa_el_layout_sin_menus(self):
        """La baja se abre desde un correo y no debe ofrecer navegación."""
        subscriber = Subscriber.objects.create(
            email='sinmenus@ejemplo.cu', source='landing', confirmed_at=timezone.now()
        )

        response = self.client.get(
            reverse('home:newsletter_unsubscribe', args=[subscriber.unsubscribe_token])
        )

        self.assertContains(response, 'Baja confirmada')
        self.assertContains(response, 'dist/img/webp/exit.webp')
        self.assertNotContains(response, 'class="navbar')

    def test_la_pagina_de_baja_invalida_usa_el_layout_sin_menus(self):
        response = self.client.get(
            reverse(
                'home:newsletter_unsubscribe',
                args=['00000000-0000-0000-0000-000000000000'],
            )
        )

        self.assertContains(response, 'dist/img/webp/error.webp')
        self.assertNotContains(response, 'class="navbar')


class LandingFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        get_default_list()

    def test_el_landing_tiene_un_formulario_real(self):
        response = self.client.get(reverse('home:landing'))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('action="/suscripcion/"', html)
        self.assertIn('method="post"', html)
        self.assertIn('name="email"', html)
        self.assertIn('csrfmiddlewaretoken', html)
