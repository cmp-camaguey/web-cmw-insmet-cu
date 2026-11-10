"""Franja de aviso meteorológico vigente (context processor `menu_notifications`).

La franja no es una notificación: se sirve en el HTML, sin sesión y sin
JavaScript, para que un visitante anónimo vea una emergencia. Por eso la
decisión de qué aviso encabeza la franja vive en el context processor y se
prueba acá, sin plantilla de por medio.
"""

import re
from datetime import timedelta

from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.context_processors import menu_notifications
from apps.meteo.models import Warning


class EmergencyBannerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = User.objects.create_user(
            'autor-aviso',
            'autor@example.com',
            'pass',
            first_name='Ada',
            last_name='Norte',
        )

    def _warning(self, warning_type='early', *, hours=24, title='', summary='Resumen del aviso'):
        return Warning.objects.create(
            warning_type=warning_type,
            title=title or None,
            user=self.author,
            summary=summary,
            valid_until=timezone.now() + timedelta(hours=hours),
        )

    def _banner(self):
        return menu_notifications(RequestFactory().get('/'))['emergency_banner']

    def _top(self):
        banner = self._banner()
        return banner['candidates'][0] if banner else None

    def test_sin_avisos_no_hay_franja(self):
        self.assertIsNone(self._banner())

    def test_un_aviso_vencido_no_enciende_la_franja(self):
        self._warning(hours=-1)

        self.assertIsNone(self._banner())

    def test_un_aviso_vigente_encabeza_la_franja(self):
        warning = self._warning(title='Lluvias intensas')

        top = self._top()

        self.assertEqual(top['warning'], warning)
        self.assertEqual(top['total'], 1)
        self.assertEqual(top['extra'], 0)

    def test_el_ciclon_gana_a_una_alerta_temprana_mas_reciente(self):
        """La franja lleva el hecho más grave, no el último publicado."""
        self._warning('early', hours=48)
        cyclone = self._warning('tropical_cyclone', hours=24)

        self.assertEqual(self._top()['warning'], cyclone)

    def test_entre_dos_de_la_misma_gravedad_gana_el_mas_reciente(self):
        self._warning('storm', hours=24, title='Vieja')
        newest = self._warning('storm', hours=12, title='Nueva')

        self.assertEqual(self._top()['warning'], newest)

    def test_la_franja_cuenta_los_avisos_que_no_encabezan(self):
        self._warning('early')
        self._warning('tropical_cyclone')

        top = self._top()

        self.assertEqual(top['total'], 2)
        self.assertEqual(top['extra'], 1)

    def test_los_candidatos_van_del_mas_grave_al_menos_grave(self):
        """El navegador muestra el primero; el orden tiene que ser el de la franja."""
        early = self._warning('early', hours=72)
        storm = self._warning('storm', hours=48)
        cyclone = self._warning('tropical_cyclone', hours=24)

        warnings = [candidate['warning'] for candidate in self._banner()['candidates']]

        self.assertEqual(warnings, [cyclone, storm, early])

    def test_el_landing_recibe_solo_los_avisos_graves(self):
        self._warning('early', title='Lluvias rutinarias')
        storm = self._warning('storm', title='Tormenta')

        banner = self._banner()

        self.assertEqual(
            [candidate['warning'] for candidate in banner['severe_candidates']],
            [storm],
        )
        self.assertEqual(len(banner['candidates']), 2)

    def test_cada_tipo_enlaza_a_su_propia_lista(self):
        esperado = {
            'early': 'home:warnings_early',
            'storm': 'home:warnings_storm',
            'tropical_cyclone': 'home:warnings_tropical',
        }
        for warning_type, url_name in esperado.items():
            with self.subTest(warning_type=warning_type):
                Warning.objects.all().delete()
                self._warning(warning_type)

                self.assertEqual(self._top()['url_name'], url_name)

    @override_settings(EMERGENCY_BANNER_ENABLED=False)
    def test_la_franja_se_apaga_por_configuracion(self):
        self._warning('tropical_cyclone')

        self.assertIsNone(self._banner())


class EmergencyBannerCloseTests(TestCase):
    """El botón de cerrar y la memoria por aviso, probados donde se puede probar.

    La interacción (localStorage) vive en el navegador y no se puede ejecutar
    aquí; lo que sí se fija es el contrato del HTML: que exista el botón, que
    lleve el aviso y su vigencia, y que el script guarde una lista de cierres,
    que es lo que hace que cerrar uno deje ver el siguiente.
    """

    @classmethod
    def setUpTestData(cls):
        cls.author = User.objects.create_user(
            'autor-cierre',
            'cierre@example.com',
            'pass',
            first_name='Ada',
            last_name='Cierre',
        )

    def _render(self):
        warning = Warning.objects.create(
            warning_type='tropical_cyclone',
            title='Ciclón de prueba',
            user=self.author,
            summary='Resumen',
            valid_until=timezone.now() + timedelta(hours=12),
        )
        context = menu_notifications(RequestFactory().get('/'))
        return warning, render_to_string(
            'includes/home/emergency_banner.html', {'emergency_banner': context['emergency_banner']}
        )

    def test_la_franja_trae_el_boton_de_cerrar(self):
        _warning, html = self._render()

        self.assertIn('data-emergency-banner-close', html)
        self.assertIn('Ocultar este aviso', html)

    def test_el_boton_guarda_el_aviso_y_su_vigencia_en_una_lista(self):
        warning, html = self._render()

        self.assertIn(f'data-warning-id="{warning.uuid}"', html)
        self.assertIn('data-warning-valid-until=', html)
        self.assertIn('cmp.emergencyBanner.dismissed', html)
        # Lista, no un objeto único: cerrar el más grave no puede borrar el
        # cierre anterior ni esconder el que venga después.
        self.assertIn('Array.isArray(stored)', html)

    def test_el_landing_carga_el_script_del_boton(self):
        self._render()

        response = self.client.get(reverse('home:landing'))

        self.assertContains(response, 'dist/js/emergency-banner.js')


class EmergencyBannerMarkupTests(TestCase):
    """Sin JavaScript se ve la primera franja, que es la más grave.

    La lista completa viaja en el HTML para que el script en línea elija cuál
    mostrar según los cierres guardados; las demás quedan con `hidden`.
    """

    @classmethod
    def setUpTestData(cls):
        cls.author = User.objects.create_user(
            'autor-markup',
            'markup@example.com',
            'pass',
            first_name='Ada',
            last_name='Markup',
        )

    def _warning(self, warning_type, *, hours=24, title=''):
        return Warning.objects.create(
            warning_type=warning_type,
            title=title or None,
            user=self.author,
            summary='Resumen',
            valid_until=timezone.now() + timedelta(hours=hours),
        )

    def _render(self):
        context = menu_notifications(RequestFactory().get('/'))
        return render_to_string(
            'includes/home/emergency_banner.html',
            {'emergency_banner': context['emergency_banner']},
        )

    def _asides(self, html):
        return re.findall(r'<aside[^>]*data-emergency-banner[^>]*>', html)

    def test_renderiza_todos_los_avisos_y_solo_el_primero_queda_visible(self):
        cyclone = self._warning('tropical_cyclone', title='Ciclón')
        storm = self._warning('storm', title='Tormenta')
        early = self._warning('early', title='Temprana')

        asides = self._asides(self._render())

        self.assertEqual(len(asides), 3)
        self.assertNotIn('hidden', asides[0])
        self.assertIn('hidden', asides[1])
        self.assertIn('hidden', asides[2])
        for aside, warning in zip(asides, (cyclone, storm, early), strict=True):
            self.assertIn(f'data-warning-id="{warning.uuid}"', aside)

    def test_el_landing_no_manda_avisos_tempranos_ni_siquiera_ocultos(self):
        self._warning('early', title='Lluvias rutinarias')
        self._warning('tropical_cyclone', title='Ciclón')

        html = self.client.get(reverse('home:landing')).content.decode()

        self.assertIn('Ciclón', html)
        self.assertNotIn('Lluvias rutinarias', html)
        self.assertEqual(len(self._asides(html)), 1)


class MenuWarningCountsTests(TestCase):
    """Los badges del menú siguen saliendo de la misma consulta que la franja.

    El context processor traía tres `COUNT(*)` y ahora trae las filas vigentes
    una sola vez. Los conteos no pueden cambiar por eso.
    """

    @classmethod
    def setUpTestData(cls):
        cls.author = User.objects.create_user(
            'autor-conteos',
            'conteos@example.com',
            'pass',
            first_name='Ada',
            last_name='Sur',
        )

    def _warning(self, warning_type, *, hours=24):
        return Warning.objects.create(
            warning_type=warning_type,
            user=self.author,
            summary='Resumen',
            valid_until=timezone.now() + timedelta(hours=hours),
        )

    def test_los_conteos_por_tipo_siguen_correctos(self):
        self._warning('early')
        self._warning('early')
        self._warning('storm')
        self._warning('tropical_cyclone')
        self._warning('storm', hours=-1)

        context = menu_notifications(RequestFactory().get('/'))

        self.assertEqual(context['early_warning_count'], 2)
        self.assertEqual(context['storm_warning_count'], 1)
        self.assertEqual(context['tropical_cyclone_count'], 1)
