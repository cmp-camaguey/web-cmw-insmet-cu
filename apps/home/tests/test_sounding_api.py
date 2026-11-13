"""Sondeo servido por la API externa de modelos (034-sondeo-api-externa)."""

import base64
import json
from unittest.mock import Mock, patch

import requests
from django.test import TestCase
from django.test.client import RequestFactory

from apps.home.views.modelos.views import SoundingExportView, SoundingView
from apps.meteo.models import Town

PNG_BYTES = b'\x89PNG\r\n\x1a\nfake-skewt'
PNG_B64 = base64.b64encode(PNG_BYTES).decode('ascii')
REQUESTS_GET = 'apps.home.views.modelos.views.requests.get'


def _json(response):
    """Deserializar el cuerpo de una respuesta de vista invocada sin el cliente de test."""
    return json.loads(response.content)


def _api_response(*, content=b'', content_type='image/png', status_code=200, json_data=None):
    """Construir una respuesta simulada de la API externa de sondeos."""
    response = Mock()
    response.status_code = status_code
    response.headers = {'Content-Type': content_type}
    response.content = content
    response.json.return_value = json_data
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
    else:
        response.raise_for_status.return_value = None
    return response


class SoundingViewProxyTests(TestCase):
    """S1-S3: la vista reenvía la petición y normaliza el gráfico a base64."""

    def setUp(self):
        self.town = Town.objects.create(name='Camagüey', latitude=21.3786, longitude=-77.9186)
        self.factory = RequestFactory()

    def _post(self, **overrides):
        data = {'datetime_init': '2026111300', 'town': self.town.id, 't_index': 1}
        data.update(overrides)
        request = self.factory.post('/modelo/sounding/', data=data)
        return SoundingView.as_view()(request)

    @patch(REQUESTS_GET)
    def test_json_plot_is_returned_as_base64(self, mock_get):
        mock_get.return_value = _api_response(
            content_type='application/json',
            json_data={
                'status': 'success',
                'plot_image': PNG_B64,
                'datetime': '2026-11-13T00:00:00',
            },
        )

        response = self._post()

        self.assertEqual(response.status_code, 200)
        payload = _json(response)
        self.assertEqual(payload['status'], 'success')
        self.assertEqual(payload['plot_image'], PNG_B64)
        self.assertEqual(payload['datetime'], '2026-11-13T00:00:00')

    @patch(REQUESTS_GET)
    def test_raw_png_is_encoded_as_base64(self, mock_get):
        mock_get.return_value = _api_response(content=PNG_BYTES, content_type='image/png')

        response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(_json(response)['plot_image'], PNG_B64)

    @patch(REQUESTS_GET)
    def test_data_uri_plot_is_decoded_and_reencoded(self, mock_get):
        mock_get.return_value = _api_response(
            content_type='application/json',
            json_data={'status': 'success', 'plot_image': f'data:image/png;base64,{PNG_B64}'},
        )

        response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(_json(response)['plot_image'], PNG_B64)

    @patch(REQUESTS_GET)
    def test_forwards_zero_based_time_index_and_town_coordinates(self, mock_get):
        mock_get.return_value = _api_response(content=PNG_BYTES)

        self._post(t_index=3)

        url = mock_get.call_args[0][0]
        self.assertIn('/api/sounding/', url)
        self.assertIn('datetime_init=2026111300', url)
        self.assertIn('lat=21.3786', url)
        self.assertIn('long=-77.9186', url)
        self.assertIn('t_index=2', url)

    @patch(REQUESTS_GET)
    def test_upstream_404_reports_no_data(self, mock_get):
        mock_get.return_value = _api_response(status_code=404)

        response = self._post()

        self.assertEqual(response.status_code, 404)
        self.assertIn('datos', _json(response)['message'])

    @patch(REQUESTS_GET)
    def test_upstream_json_error_returns_502(self, mock_get):
        mock_get.return_value = _api_response(
            content_type='application/json',
            json_data={'status': 'error', 'message': 'boom'},
        )

        response = self._post()

        self.assertEqual(response.status_code, 502)
        self.assertIn('message', _json(response))

    @patch(REQUESTS_GET)
    def test_connection_error_returns_502(self, mock_get):
        mock_get.side_effect = requests.ConnectionError('sin red')

        response = self._post()

        self.assertEqual(response.status_code, 502)
        self.assertIn('message', _json(response))

    @patch(REQUESTS_GET)
    def test_invalid_form_returns_400_without_calling_the_api(self, mock_get):
        response = self._post(datetime_init='13/11/2026')

        self.assertEqual(response.status_code, 400)
        self.assertIn('errors', _json(response))
        mock_get.assert_not_called()


class SoundingExportViewTests(TestCase):
    """S4: la exportación la sirve la API externa."""

    def setUp(self):
        Town.objects.create(name='Camagüey', latitude=21.3786, longitude=-77.9186)
        self.factory = RequestFactory()

    def _get(self, fmt):
        request = self.factory.get('/modelo/sounding/export/')
        return SoundingExportView.as_view()(request, fmt=fmt)

    @patch(REQUESTS_GET)
    def test_rejects_format_outside_whitelist(self, mock_get):
        response = self._get('gif')

        self.assertEqual(response.status_code, 400)
        mock_get.assert_not_called()

    @patch(REQUESTS_GET)
    def test_forwards_format_to_the_api(self, mock_get):
        mock_get.return_value = _api_response(content=b'%PDF-1.4', content_type='application/pdf')

        self._get('pdf')

        self.assertIn('fmt=pdf', mock_get.call_args[0][0])

    @patch(REQUESTS_GET)
    def test_serves_pdf_as_attachment(self, mock_get):
        mock_get.return_value = _api_response(content=b'%PDF-1.4', content_type='application/pdf')

        response = self._get('pdf')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(
            response['Content-Disposition'].startswith('attachment; filename="sounding_')
        )
        self.assertTrue(response['Content-Disposition'].endswith('.pdf"'))
        self.assertEqual(response.content, b'%PDF-1.4')

    @patch(REQUESTS_GET)
    def test_connection_error_returns_502(self, mock_get):
        mock_get.side_effect = requests.ConnectionError('sin red')

        response = self._get('png')

        self.assertEqual(response.status_code, 502)
