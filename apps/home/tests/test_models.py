from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.meteo.models import (
    Forecasts,
    WeatherReport,
)
from apps.meteo.models import (
    Warning as MeteoWarning,
)
from apps.publications.models import Author, ScientificPublication


class IndexViewContextTests(TestCase):
    def test_context_contains_title(self):
        response = self.client.get(reverse('home:index'))
        self.assertEqual(response.context['title'], 'Inicio')

    def test_context_contains_segment_and_parent(self):
        response = self.client.get(reverse('home:index'))
        self.assertEqual(response.context['segment'], 'index')
        self.assertEqual(response.context['parent'], '')

    def test_context_latest_forecast_none_when_no_data(self):
        response = self.client.get(reverse('home:index'))
        self.assertIsNone(response.context['latest_forecast'])

    def test_context_latest_forecast_when_exists(self):
        Forecasts.objects.create(
            date=date.today(),
            lp='Luna Nueva',
            nlp='Cuarto Creciente',
            nlpd=date.today(),
            sunrise=time(6, 30),
            sunset=time(18, 30),
            uv_index=5,
        )
        response = self.client.get(reverse('home:index'))
        self.assertIsNotNone(response.context['latest_forecast'])
        self.assertEqual(response.context['latest_forecast'].date, date.today())


class WeatherReportDetailViewContextTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            'ruser', 'r@t.com', 'pass', first_name='R', last_name='U'
        )

    def _create_report(self, report_type):
        return WeatherReport.objects.create(
            user=self.user,
            summary=f'Test {report_type}',
            report_type=report_type,
        )

    def test_today_context_title(self):
        self._create_report('today')
        response = self.client.get(reverse('home:weather_today'))
        self.assertEqual(response.context['title'], 'El Tiempo para Hoy')
        self.assertEqual(response.context['segment'], 'weather_today')
        self.assertEqual(response.context['parent'], 'tiempo')

    def test_today_context_contains_objects_key(self):
        self._create_report('today')
        response = self.client.get(reverse('home:weather_today'))
        self.assertIn('objects', response.context)

    def test_encuentra_el_reporte_del_dia_sin_depender_del_tzdata_de_la_base(self):
        """El dia local se resuelve en Python, no dentro de PostgreSQL.

        `date__date` resuelve America/Havana con el tzdata del servidor de base.
        Si ese tzdata y el de Python discrepan (el paquete `tzdata` de PyPI y la
        imagen de PostgreSQL se mueven a distinto ritmo), el reporte recien
        creado deja de encontrarse y las cuatro paginas responden "Nada que
        mostrar". El instante congelado es el de una corrida real de CI que
        fallo exactamente por esto.
        """
        instante = datetime(2026, 11, 8, 4, 0, 15, tzinfo=UTC)
        with patch('django.utils.timezone.now', return_value=instante):
            self._create_report('today')
            response = self.client.get(reverse('home:weather_today'))
            # Django omite la clave del contexto cuando `get_object()` devuelve
            # None, y la pagina cae en el estado vacio que vio el usuario.
            self.assertIsNotNone(response.context.get('weather_today'))
            self.assertNotContains(response, 'Nada que mostrar')

    def test_tomorrow_context_title(self):
        self._create_report('tomorrow')
        response = self.client.get(reverse('home:weather_tomorrow'))
        self.assertEqual(response.context['title'], 'El Tiempo para Mañana')
        self.assertEqual(response.context['segment'], 'weather_tomorrow')
        self.assertEqual(response.context['parent'], 'tiempo')

    def test_commentary_context_title(self):
        self._create_report('commentary')
        response = self.client.get(reverse('home:weather_commentary'))
        self.assertEqual(response.context['title'], 'Comentario del Tiempo')
        self.assertEqual(response.context['segment'], 'weather_commentary')
        self.assertEqual(response.context['parent'], 'tiempo')

    def test_note_context_title(self):
        self._create_report('note')
        response = self.client.get(reverse('home:weather_note'))
        self.assertEqual(response.context['title'], 'Nota Meteorológica')
        self.assertEqual(response.context['segment'], 'weather_note')
        self.assertEqual(response.context['parent'], 'tiempo')


class EarlyWarningViewContextTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            'ewarn', 'ew@t.com', 'pass', first_name='E', last_name='W'
        )

    def test_context_title(self):
        MeteoWarning.objects.create(
            user=self.user,
            warning_type='early',
            summary='Test warning',
            valid_until=timezone.now() + timedelta(days=1),
        )
        response = self.client.get(reverse('home:warnings_early'))
        self.assertEqual(response.context['title'], 'Aviso de Alerta Temprana')

    def test_context_parent_segment(self):
        response = self.client.get(reverse('home:warnings_early'))
        self.assertEqual(response.context['parent'], 'aviso')
        self.assertEqual(response.context['segment'], 'warnings_early')

    def test_context_objects_filtered_by_validity(self):
        expired = MeteoWarning.objects.create(
            user=self.user,
            warning_type='early',
            summary='Expired',
            valid_until=timezone.now() - timedelta(days=1),
        )
        active = MeteoWarning.objects.create(
            user=self.user,
            warning_type='early',
            summary='Active',
            valid_until=timezone.now() + timedelta(days=1),
        )
        response = self.client.get(reverse('home:warnings_early'))
        self.assertIn(active, response.context['object_list'])
        self.assertNotIn(expired, response.context['object_list'])


class TropicalCycloneViewContextTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            'tcuser', 'tc@t.com', 'pass', first_name='T', last_name='C'
        )

    def test_context_title(self):
        response = self.client.get(reverse('home:warnings_tropical'))
        self.assertEqual(response.context['title'], 'Aviso de Ciclón Tropical')

    def test_context_parent_segment(self):
        response = self.client.get(reverse('home:warnings_tropical'))
        self.assertEqual(response.context['parent'], 'aviso')
        self.assertEqual(response.context['segment'], 'warnings_tropical')


class StormWarningViewContextTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            'suser', 's@t.com', 'pass', first_name='S', last_name='T'
        )

    def test_context_title(self):
        response = self.client.get(reverse('home:warnings_storm'))
        self.assertEqual(response.context['title'], 'Aviso de Tormenta')

    def test_context_parent_segment(self):
        response = self.client.get(reverse('home:warnings_storm'))
        self.assertEqual(response.context['parent'], 'aviso')
        self.assertEqual(response.context['segment'], 'warnings_storm')


class PublicServicesViewContextTests(TestCase):
    def test_context_title(self):
        response = self.client.get(reverse('home:services_public'))
        self.assertEqual(response.context['title'], 'Públicos')

    def test_context_parent_segment(self):
        response = self.client.get(reverse('home:services_public'))
        self.assertEqual(response.context['parent'], 'servicios')
        self.assertEqual(response.context['segment'], 'publicos')


class ScientificPublicationViewContextTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = Author.objects.create(first_name='Test', last_name='Author')

    def test_context_title(self):
        response = self.client.get(reverse('home:publications'))
        self.assertEqual(response.context['title'], 'Publicaciones Científicas')

    def test_context_parent_segment(self):
        response = self.client.get(reverse('home:publications'))
        self.assertEqual(response.context['parent'], 'institucion')
        self.assertEqual(response.context['segment'], 'publicaciones_cientificas')

    def test_context_objects_returns_publications(self):
        pdf = SimpleUploadedFile('test.pdf', b'%PDF-1.4 test', content_type='application/pdf')
        pub = ScientificPublication.objects.create(
            title='Test Pub',
            author=self.author,
            summary='Summary',
            publication_date=date.today(),
            pdf=pdf,
        )
        response = self.client.get(reverse('home:publications'))
        self.assertIn(pub, response.context['object_list'])

    def test_context_objects_empty_when_no_data(self):
        response = self.client.get(reverse('home:publications'))
        self.assertEqual(list(response.context['object_list']), [])


class PagoViewContextTests(TestCase):
    def test_context_title(self):
        response = self.client.get(reverse('home:payment'))
        self.assertEqual(response.context['title'], 'Pagos en linea')

    def test_context_parent_segment(self):
        response = self.client.get(reverse('home:payment'))
        self.assertEqual(response.context['parent'], 'pago')
        self.assertEqual(response.context['segment'], 'pago_qr')


class SateliteViewContextTests(TestCase):
    @patch('apps.home.views.satelites.views.requests.get')
    def test_context_title(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {'products': []}
        response = self.client.get(reverse('home:satellite'))
        self.assertEqual(response.context['title'], 'Mapas Satelitales')

    @patch('apps.home.views.satelites.views.requests.get')
    def test_context_parent_segment(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {'products': []}
        response = self.client.get(reverse('home:satellite'))
        self.assertEqual(response.context['parent'], 'imágenes')
        self.assertEqual(response.context['segment'], 'satelitales')


class MapaViewContextTests(TestCase):
    def test_context_title(self):
        response = self.client.get(reverse('home:models_maps'))
        self.assertEqual(response.context['title'], 'Modelo de pronóstico WRF')

    def test_context_contains_forms(self):
        response = self.client.get(reverse('home:models_maps'))
        self.assertIn('form', response.context)
        self.assertIn('gif_form', response.context)

    def test_context_parent_segment(self):
        response = self.client.get(reverse('home:models_maps'))
        self.assertEqual(response.context['parent'], 'modelos')
        self.assertEqual(response.context['segment'], 'models_maps')

    def test_context_initial_date(self):
        response = self.client.get(reverse('home:models_maps'))
        self.assertIsNotNone(response.context.get('initial_date'))


class MeteogramViewContextTests(TestCase):
    def test_context_title(self):
        response = self.client.get(reverse('home:models_meteogram'))
        self.assertEqual(response.context['title'], 'Meteorama')

    def test_context_contains_form(self):
        response = self.client.get(reverse('home:models_meteogram'))
        self.assertIn('form', response.context)

    def test_context_parent_segment(self):
        response = self.client.get(reverse('home:models_meteogram'))
        self.assertEqual(response.context['parent'], 'modelos')
        self.assertEqual(response.context['segment'], 'models_meteogram')


class SoundingViewContextTests(TestCase):
    def test_context_title(self):
        response = self.client.get(reverse('home:models_sounding'))
        self.assertEqual(response.context['title'], 'Sondeos')

    def test_context_contains_form(self):
        response = self.client.get(reverse('home:models_sounding'))
        self.assertIn('form', response.context)

    def test_context_parent_segment(self):
        response = self.client.get(reverse('home:models_sounding'))
        self.assertEqual(response.context['parent'], 'modelos')
        self.assertEqual(response.context['segment'], 'models_sounding')
