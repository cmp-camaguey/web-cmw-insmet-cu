"""Franja de aviso meteorológico vigente (context processor `menu_notifications`).

La franja no es una notificación: se sirve en el HTML, sin sesión y sin
JavaScript, para que un visitante anónimo vea una emergencia. Por eso la
decisión de qué aviso encabeza la franja vive en el context processor y se
prueba acá, sin plantilla de por medio.
"""

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings
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

    def test_sin_avisos_no_hay_franja(self):
        self.assertIsNone(self._banner())

    def test_un_aviso_vencido_no_enciende_la_franja(self):
        self._warning(hours=-1)

        self.assertIsNone(self._banner())

    def test_un_aviso_vigente_encabeza_la_franja(self):
        warning = self._warning(title='Lluvias intensas')

        banner = self._banner()

        self.assertEqual(banner['warning'], warning)
        self.assertEqual(banner['total'], 1)
        self.assertEqual(banner['extra'], 0)

    def test_el_ciclon_gana_a_una_alerta_temprana_mas_reciente(self):
        """La franja lleva el hecho más grave, no el último publicado."""
        self._warning('early', hours=48)
        cyclone = self._warning('tropical_cyclone', hours=24)

        self.assertEqual(self._banner()['warning'], cyclone)

    def test_entre_dos_de_la_misma_gravedad_gana_el_mas_reciente(self):
        self._warning('storm', hours=24, title='Vieja')
        newest = self._warning('storm', hours=12, title='Nueva')

        self.assertEqual(self._banner()['warning'], newest)

    def test_la_franja_cuenta_los_avisos_que_no_encabezan(self):
        self._warning('early')
        self._warning('tropical_cyclone')

        banner = self._banner()

        self.assertEqual(banner['total'], 2)
        self.assertEqual(banner['extra'], 1)

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

                self.assertEqual(self._banner()['url_name'], url_name)

    @override_settings(EMERGENCY_BANNER_ENABLED=False)
    def test_la_franja_se_apaga_por_configuracion(self):
        self._warning('tropical_cyclone')

        self.assertIsNone(self._banner())


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
