from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.monitoring.models import JavaScriptError, PageView, WebVital
from apps.monitoring.services import traffic


def pageview(now, path='/', session='s1', minutes_ago=1, **kwargs):
    data = {
        'session_hash': session,
        'path': path,
        'created_at': now - timedelta(minutes=minutes_ago),
    }
    data.update(kwargs)
    return PageView.objects.create(**data)


def vital(now, metric, value, rating, minutes_ago=1):
    return WebVital.objects.create(
        session_hash='s1',
        path='/',
        metric=metric,
        value=value,
        rating=rating,
        created_at=now - timedelta(minutes=minutes_ago),
    )


class TrafficSnapshotTests(TestCase):
    def setUp(self):
        self.now = timezone.now().replace(second=0, microsecond=0)

    def test_sin_datos_no_hay_visitantes(self):
        data = traffic.traffic_snapshot(now=self.now)
        self.assertFalse(data['has_data'])
        self.assertEqual(data['visitors_5m'], 0)
        self.assertIsNone(data['pages_per_visitor'])

    def test_visitantes_por_ventana_y_distintos(self):
        pageview(self.now, session='s1', minutes_ago=1)
        pageview(self.now, path='/otra/', session='s1', minutes_ago=2)
        pageview(self.now, session='s2', minutes_ago=10)
        data = traffic.traffic_snapshot(now=self.now)
        self.assertEqual(data['visitors_5m'], 1)
        self.assertEqual(data['visitors_30m'], 2)
        self.assertEqual(data['pageviews_30m'], 3)
        self.assertEqual(data['pages_per_visitor'], 1.5)

    def test_sesiones_nuevas_solo_cuentan_la_primera_visita(self):
        pageview(self.now, session='vieja', minutes_ago=45)
        pageview(self.now, path='/otra/', session='vieja', minutes_ago=1)
        pageview(self.now, session='nueva', minutes_ago=1)
        self.assertEqual(traffic.new_sessions(30, now=self.now), 1)

    def test_paginas_mas_activas_ordenadas(self):
        pageview(self.now, path='/a/', session='s1')
        pageview(self.now, path='/b/', session='s2')
        pageview(self.now, path='/b/', session='s3')
        top = traffic.top_pages(now=self.now)
        self.assertEqual(top[0]['path'], '/b/')
        self.assertEqual(top[0]['views'], 2)

    def test_fuentes_y_etiquetas(self):
        pageview(self.now, session='s1', referrer_host='', referrer_type='direct')
        pageview(
            self.now,
            session='s2',
            referrer_host='google.com',
            referrer_type='search',
        )
        data = traffic.referrers(now=self.now)
        labels = [row['label'] for row in data['hosts']]
        self.assertIn('Directo', labels)
        self.assertIn('google.com', labels)
        types = {row['type']: row['count'] for row in data['types']}
        self.assertEqual(types['direct'], 1)
        self.assertEqual(types['search'], 1)

    def test_dispositivos_y_navegadores(self):
        pageview(self.now, session='s1', device_type='mobile', browser='Chrome')
        pageview(self.now, session='s2', device_type='mobile', browser='Firefox')
        pageview(self.now, session='s3', device_type='desktop', browser='Chrome')
        devices = traffic.devices(now=self.now)
        self.assertEqual(devices[0]['label'], 'Móvil')
        self.assertEqual(devices[0]['count'], 2)
        browsers = traffic.browsers(now=self.now)
        self.assertEqual(browsers[0]['browser'], 'Chrome')
        self.assertEqual(browsers[0]['count'], 2)

    def test_serie_rellena_minutos_vacios(self):
        pageview(self.now, session='s1', minutes_ago=0)
        series = traffic.pageview_series(30, now=self.now)
        self.assertEqual(len(series), 31)
        self.assertEqual(series[-1]['count'], 1)
        self.assertEqual(series[0]['count'], 0)


class WebVitalsTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def test_p75_y_calificacion(self):
        for value, rating in (
            (1000, WebVital.RATING_GOOD),
            (2000, WebVital.RATING_GOOD),
            (3000, WebVital.RATING_NEEDS_IMPROVEMENT),
            (5000, WebVital.RATING_POOR),
        ):
            vital(self.now, WebVital.LCP, value, rating)
        data = traffic.web_vitals(now=self.now)
        lcp = data['metrics'][WebVital.LCP]
        self.assertEqual(lcp['samples'], 4)
        self.assertEqual(lcp['p75'], 3000)
        self.assertEqual(lcp['rating'], WebVital.RATING_NEEDS_IMPROVEMENT)
        self.assertEqual(lcp['distribution']['good'], 2)
        self.assertEqual(lcp['distribution']['needs_improvement'], 1)
        self.assertEqual(lcp['distribution']['poor'], 1)

    def test_metricas_sin_muestras_no_fingen_un_valor(self):
        data = traffic.web_vitals(now=self.now)
        lcp = data['metrics'][WebVital.LCP]
        self.assertIsNone(lcp['p75'])
        self.assertIsNone(lcp['rating'])
        self.assertEqual(lcp['samples'], 0)

    def test_umbrales_por_metrica(self):
        self.assertEqual(traffic.rating_for(WebVital.CLS, 0.05), WebVital.RATING_GOOD)
        self.assertEqual(traffic.rating_for(WebVital.CLS, 0.2), WebVital.RATING_NEEDS_IMPROVEMENT)
        self.assertEqual(traffic.rating_for(WebVital.CLS, 0.5), WebVital.RATING_POOR)
        self.assertEqual(traffic.rating_for(WebVital.INP, 150), WebVital.RATING_GOOD)
        self.assertEqual(traffic.rating_for(WebVital.INP, 300), WebVital.RATING_NEEDS_IMPROVEMENT)
        self.assertEqual(traffic.rating_for(WebVital.INP, 900), WebVital.RATING_POOR)
        self.assertIsNone(traffic.rating_for('otra', 10))


class ErrorAndWeightTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def test_errores_listados_del_mas_reciente_al_mas_viejo(self):
        JavaScriptError.objects.create(
            session_hash='s1', path='/', message='viejo', created_at=self.now - timedelta(hours=2)
        )
        JavaScriptError.objects.create(
            session_hash='s1', path='/', message='nuevo', created_at=self.now
        )
        data = traffic.javascript_errors(now=self.now)
        self.assertEqual(data['count'], 2)
        self.assertEqual(data['items'][0]['message'], 'nuevo')

    def test_peso_de_pagina_promedio(self):
        pageview(self.now, session='s1', page_weight_kb=400, slow_resources=2)
        pageview(self.now, session='s2', path='/b/', page_weight_kb=800, slow_resources=4)
        pageview(self.now, session='s3', path='/c/', page_weight_kb=None, slow_resources=0)
        data = traffic.page_weight(now=self.now)
        self.assertEqual(data['samples'], 2)
        self.assertEqual(data['avg_kb'], 600.0)
        self.assertEqual(data['avg_slow'], 3.0)
