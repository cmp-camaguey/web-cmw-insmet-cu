"""La franja de emergencia se pinta en el portal público (cambio 031).

La plantilla cuelga de `layouts/home.html`, que envuelve las páginas públicas
del portal. Se prueba sobre una de ellas, no sobre un template aislado: lo que
importa es que el visitante anónimo la reciba servida en el HTML.
"""

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import SiteConfiguration
from apps.meteo.models import Warning


class EmergencyBannerUiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = User.objects.create_user(
            'autor-franja',
            'autor-franja@example.com',
            'pass',
            first_name='Ada',
            last_name='Este',
        )
        SiteConfiguration.objects.update_or_create(defaults={'maintenance_mode': False})

    def _warning(self, warning_type='tropical_cyclone', *, hours=24, title='Huracán categoría 3'):
        return Warning.objects.create(
            warning_type=warning_type,
            title=title,
            user=self.author,
            summary='Se aproxima a la costa norte en las próximas horas.',
            valid_until=timezone.now() + timedelta(hours=hours),
        )

    def _html(self):
        return self.client.get(reverse('home:warnings_early')).content.decode()

    def test_la_franja_se_pinta_sobre_la_pagina_publica(self):
        self._warning()

        html = self._html()

        self.assertIn('emergency-banner emergency-banner--tropical_cyclone', html)
        self.assertIn('Aviso vigente · Ciclón Tropical', html)
        self.assertIn('Huracán categoría 3', html)

    def test_el_enlace_lleva_a_la_lista_del_tipo(self):
        self._warning()

        html = self._html()

        self.assertIn(f'href="{reverse("home:warnings_tropical")}"', html)
        self.assertIn('Ver detalles', html)

    def test_con_varios_avisos_el_enlace_dice_cuantos_son(self):
        self._warning()
        self._warning(warning_type='early', title='Lluvias intensas')

        html = self._html()

        self.assertIn('Ver los 2 avisos', html)

    def test_sin_avisos_no_hay_franja(self):
        # `data-emergency-banner` es el atributo del `<aside>`: la cadena
        # `emergency-banner` sola no sirve porque `scripts.html` carga
        # `emergency-banner.js` en todas las páginas.
        self.assertNotIn('data-emergency-banner', self._html())

    def test_un_aviso_vencido_no_pinta_la_franja(self):
        self._warning(hours=-1)

        self.assertNotIn('data-emergency-banner', self._html())
