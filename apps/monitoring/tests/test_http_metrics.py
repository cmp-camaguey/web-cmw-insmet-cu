from datetime import timedelta

from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from apps.monitoring.models import RequestMetric
from apps.monitoring.services import http


def metric(
    path='/meteo/estaciones/', status_code=200, duration_ms=20, created_at=None, method='GET'
):
    data = {
        'method': method,
        'path': path,
        'status_code': status_code,
        'duration_ms': duration_ms,
    }
    if created_at is not None:
        data['created_at'] = created_at
    return RequestMetric.objects.create(**data)


class RecordRequestTests(TestCase):
    @override_settings(MONITORING_ENABLED=True)
    def test_registra_una_peticion_normal(self):
        request = RequestFactory().get('/meteo/estaciones/?page=2')
        self.assertTrue(http.record_request(request, 200, 12))
        row = RequestMetric.objects.get()
        self.assertEqual(row.path, '/meteo/estaciones/')
        self.assertEqual(row.method, 'GET')
        self.assertEqual(row.status_code, 200)
        self.assertEqual(row.duration_ms, 12)

    @override_settings(MONITORING_ENABLED=True)
    def test_duraciones_negativas_se_acotan_a_cero(self):
        request = RequestFactory().get('/tiempo/hoy/')
        http.record_request(request, 200, -3)
        self.assertEqual(RequestMetric.objects.get().duration_ms, 0)

    @override_settings(MONITORING_ENABLED=True)
    def test_no_registra_rutas_excluidas(self):
        for path in (
            '/static/dist/js/tabler.min.js',
            '/media/files/aviso.pdf',
            '/favicon.ico',
            '/rum/collect/',
            '/dashboard/monitoring/data/',
        ):
            request = RequestFactory().get(path)
            self.assertFalse(http.record_request(request, 200, 5), path)
        self.assertEqual(RequestMetric.objects.count(), 0)

    @override_settings(MONITORING_ENABLED=False)
    def test_interruptor_apagado_no_escribe(self):
        request = RequestFactory().get('/tiempo/hoy/')
        self.assertFalse(http.record_request(request, 200, 5))
        self.assertEqual(RequestMetric.objects.count(), 0)

    @override_settings(MONITORING_ENABLED=True)
    def test_un_fallo_de_escritura_no_propaga(self):
        request = RequestFactory().get('/tiempo/hoy/')
        original = RequestMetric.objects.create

        def boom(**kwargs):
            raise RuntimeError('base caída')

        RequestMetric.objects.create = boom
        try:
            self.assertFalse(http.record_request(request, 200, 5))
        finally:
            RequestMetric.objects.create = original


class SummaryTests(TestCase):
    def setUp(self):
        self.now = timezone.now().replace(second=0, microsecond=0)

    def test_cuenta_por_clase_de_estado(self):
        for status in (200, 201, 301, 404, 500):
            metric(status_code=status, created_at=self.now)
        data = http.http_summary(minutes=60, now=self.now)
        self.assertEqual(data['total'], 5)
        self.assertEqual(data['status']['2xx'], 2)
        self.assertEqual(data['status']['3xx'], 1)
        self.assertEqual(data['status']['4xx'], 1)
        self.assertEqual(data['status']['5xx'], 1)
        self.assertEqual(data['error_rate'], 20.0)

    def test_fuera_de_ventana_no_cuenta(self):
        metric(created_at=self.now - timedelta(hours=2))
        metric(created_at=self.now)
        data = http.http_summary(minutes=60, now=self.now)
        self.assertEqual(data['total'], 1)

    def test_percentiles_sobre_la_muestra(self):
        for duration in range(1, 101):
            metric(duration_ms=duration, created_at=self.now)
        data = http.http_summary(minutes=60, now=self.now)
        self.assertEqual(data['p50_ms'], 51)
        self.assertEqual(data['p95_ms'], 95)
        self.assertEqual(data['max_ms'], 100)

    def test_sin_peticiones_no_hay_tasa_de_error(self):
        data = http.http_summary(minutes=60, now=self.now)
        self.assertEqual(data['total'], 0)
        self.assertIsNone(data['error_rate'])
        self.assertIsNone(data['p95_ms'])


class UptimeTests(TestCase):
    def setUp(self):
        self.now = timezone.now().replace(second=0, microsecond=0)

    def test_uptime_es_el_porcentaje_sin_5xx(self):
        for _ in range(19):
            metric(status_code=200, created_at=self.now)
        metric(status_code=500, created_at=self.now)
        data = http.uptime(hours=24, now=self.now)
        self.assertEqual(data['samples'], 20)
        self.assertEqual(data['percent'], 95.0)

    def test_pocas_muestras_no_reportan_porcentaje(self):
        for _ in range(19):
            metric(status_code=200, created_at=self.now)
        data = http.uptime(hours=24, now=self.now)
        self.assertIsNone(data['percent'])
        self.assertEqual(data['samples'], 19)

    def test_bloques_de_disponibilidad(self):
        metric(status_code=200, created_at=self.now.replace(second=0))
        metric(status_code=500, created_at=self.now.replace(second=0))
        slots = http.uptime_slots(hours=1, now=self.now, slot_minutes=15)
        self.assertEqual(len(slots), 4)
        self.assertEqual(slots[-1]['total'], 2)
        self.assertEqual(slots[-1]['errors'], 1)
        self.assertEqual(slots[-1]['state'], 'degraded')
        self.assertEqual(slots[0]['state'], 'nodata')

    def test_bloque_con_todo_5xx_esta_caido(self):
        metric(status_code=500, created_at=self.now.replace(second=0))
        slots = http.uptime_slots(hours=1, now=self.now, slot_minutes=15)
        self.assertEqual(slots[-1]['state'], 'down')


class IncidentTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def test_sin_errores_no_hay_incidente(self):
        metric(status_code=200, created_at=self.now)
        data = http.active_incident(now=self.now)
        self.assertFalse(data['active'])

    def test_racha_en_curso_mide_desde_el_primer_5xx(self):
        metric(status_code=200, created_at=self.now - timedelta(minutes=5))
        metric(status_code=500, created_at=self.now - timedelta(minutes=3))
        metric(status_code=502, created_at=self.now - timedelta(minutes=1))
        data = http.active_incident(now=self.now)
        self.assertTrue(data['active'])
        self.assertEqual(data['count'], 2)
        self.assertEqual(data['duration_s'], 180)

    def test_un_5xx_viejo_no_es_incidente_si_despues_hubo_exito(self):
        metric(status_code=500, created_at=self.now - timedelta(minutes=3))
        metric(status_code=200, created_at=self.now - timedelta(minutes=1))
        data = http.active_incident(now=self.now)
        self.assertFalse(data['active'])


class ConcurrencyTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def test_dos_peticiones_solapadas_cuentan_dos(self):
        metric(duration_ms=5000, created_at=self.now)
        metric(duration_ms=5000, created_at=self.now)
        self.assertEqual(http.concurrency(now=self.now), 2)

    def test_peticiones_secuenciales_cuentan_una(self):
        metric(duration_ms=10, created_at=self.now - timedelta(seconds=10))
        metric(duration_ms=10, created_at=self.now)
        self.assertEqual(http.concurrency(now=self.now), 1)


class SeriesTests(TestCase):
    def test_rellena_los_minutos_sin_trafico(self):
        now = timezone.now().replace(second=0, microsecond=0)
        metric(created_at=now)
        series = http.requests_per_minute(minutes=30, now=now)
        self.assertEqual(len(series), 31)
        self.assertEqual(series[-1]['count'], 1)
        self.assertEqual(series[0]['count'], 0)
        self.assertEqual(series[-1]['time'], now.strftime('%H:%M'))
