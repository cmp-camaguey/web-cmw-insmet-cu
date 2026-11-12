import json

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.core.models import SiteConfiguration
from apps.monitoring.models import JavaScriptError, PageView, WebVital

URL = '/rum/collect/'

IPHONE_UA = (
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 '
    '(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
)


def post(client, payload, **extra):
    return client.post(
        URL,
        data=json.dumps(payload),
        content_type='application/json',
        **extra,
    )


@override_settings(MONITORING_ENABLED=True)
class RUMCollectTests(TestCase):
    def setUp(self):
        cache.clear()
        SiteConfiguration.objects.get_or_create(defaults={'maintenance_mode': False})

    def test_pageview_guarda_dispositivo_navegador_y_peso(self):
        response = post(
            self.client,
            {
                'kind': 'pageview',
                'sid': 'abc-123',
                'path': '/tiempo/hoy/',
                'referrer': 'https://www.google.com/search?q=tiempo+camaguey',
                'weight_kb': 812,
                'slow_resources': 3,
            },
            HTTP_USER_AGENT=IPHONE_UA,
        )
        self.assertEqual(response.status_code, 204)
        row = PageView.objects.get()
        self.assertEqual(row.path, '/tiempo/hoy/')
        self.assertEqual(row.device_type, PageView.DEVICE_MOBILE)
        self.assertEqual(row.browser, 'Safari')
        self.assertEqual(row.os, 'iOS')
        self.assertEqual(row.referrer_type, PageView.REFERRER_SEARCH)
        self.assertEqual(row.referrer_host, 'google.com')
        self.assertEqual(row.page_weight_kb, 812)
        self.assertEqual(row.slow_resources, 3)
        self.assertEqual(len(row.session_hash), 32)

    def test_el_hash_de_sesion_no_guarda_el_valor_crudo(self):
        post(self.client, {'kind': 'pageview', 'sid': 'abc-123', 'path': '/'})
        row = PageView.objects.get()
        self.assertNotEqual(row.session_hash, 'abc-123')
        self.assertNotIn('abc-123', row.session_hash)

    def test_referrer_interno_y_directo(self):
        post(
            self.client,
            {'kind': 'pageview', 'sid': 's1', 'path': '/', 'referrer': 'http://testserver/avisos/'},
        )
        post(self.client, {'kind': 'pageview', 'sid': 's2', 'path': '/', 'referrer': ''})
        tipos = list(
            PageView.objects.order_by('created_at').values_list('referrer_type', flat=True)
        )
        self.assertEqual(tipos, [PageView.REFERRER_INTERNAL, PageView.REFERRER_DIRECT])

    def test_vitals_calculan_la_calificacion_en_el_servidor(self):
        response = post(
            self.client,
            {
                'kind': 'vitals',
                'sid': 's1',
                'path': '/',
                'metrics': {'lcp': 3000, 'fcp': 1200, 'inp': 250, 'cls': 0.05},
                'rating': 'good',
            },
        )
        self.assertEqual(response.status_code, 204)
        ratings = {row.metric: row.rating for row in WebVital.objects.all().order_by('metric')}
        self.assertEqual(ratings[WebVital.LCP], WebVital.RATING_NEEDS_IMPROVEMENT)
        self.assertEqual(ratings[WebVital.FCP], WebVital.RATING_GOOD)
        self.assertEqual(ratings[WebVital.INP], WebVital.RATING_NEEDS_IMPROVEMENT)
        self.assertEqual(ratings[WebVital.CLS], WebVital.RATING_GOOD)

    def test_vitals_ignora_valores_fuera_de_rango(self):
        post(
            self.client,
            {
                'kind': 'vitals',
                'sid': 's1',
                'path': '/',
                'metrics': {'lcp': -50, 'cls': 'mucho', 'fcp': 1e12},
            },
        )
        self.assertEqual(WebVital.objects.count(), 0)

    def test_error_javascript_se_guarda(self):
        response = post(
            self.client,
            {
                'kind': 'error',
                'sid': 's1',
                'path': '/tiempo/hoy/',
                'message': 'TypeError: x is undefined',
                'source': 'https://testserver/static/dist/js/app.js',
                'lineno': 42,
            },
        )
        self.assertEqual(response.status_code, 204)
        row = JavaScriptError.objects.get()
        self.assertEqual(row.message, 'TypeError: x is undefined')
        self.assertEqual(row.lineno, 42)

    def test_payload_invalido_se_rechaza_sin_escribir(self):
        self.assertEqual(
            self.client.post(URL, data='no es json', content_type='application/json').status_code,
            400,
        )
        self.assertEqual(
            post(self.client, {'kind': 'desconocido', 'sid': 's1'}).status_code,
            400,
        )
        self.assertEqual(post(self.client, {'kind': 'pageview', 'path': '/'}).status_code, 400)
        self.assertEqual(PageView.objects.count(), 0)

    def test_payload_demasiado_grande_se_rechaza_antes_de_parsear(self):
        response = post(
            self.client,
            {'kind': 'pageview', 'sid': 's1', 'path': '/' + 'a' * 9000},
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(PageView.objects.count(), 0)

    def test_un_pageview_por_sesion_cada_dos_segundos(self):
        payload = {'kind': 'pageview', 'sid': 's1', 'path': '/'}
        post(self.client, payload)
        post(self.client, payload)
        self.assertEqual(PageView.objects.count(), 1)

    def test_solo_acepta_post(self):
        self.assertEqual(self.client.get(URL).status_code, 405)


@override_settings(MONITORING_ENABLED=False)
class RUMDisabledTests(TestCase):
    def setUp(self):
        cache.clear()
        SiteConfiguration.objects.get_or_create(defaults={'maintenance_mode': False})

    def test_apagado_no_escribe_nada(self):
        response = post(self.client, {'kind': 'pageview', 'sid': 's1', 'path': '/'})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(PageView.objects.count(), 0)
