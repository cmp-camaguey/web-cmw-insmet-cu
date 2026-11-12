from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.monitoring.models import JavaScriptError, PageView, RequestMetric, WebVital
from apps.monitoring.prune import prune_monitoring


class PruneTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def test_borra_solo_lo_que_paso_la_retencion(self):
        old = self.now - timedelta(days=31)
        recent = self.now - timedelta(days=1)
        PageView.objects.create(session_hash='s1', path='/', created_at=old)
        PageView.objects.create(session_hash='s2', path='/', created_at=recent)
        WebVital.objects.create(
            session_hash='s1', metric=WebVital.LCP, value=2000, rating='good', created_at=old
        )
        JavaScriptError.objects.create(session_hash='s1', message='x', created_at=old)

        deleted = prune_monitoring(now=self.now)

        self.assertEqual(deleted['pageviews'], 1)
        self.assertEqual(deleted['web_vitals'], 1)
        self.assertEqual(deleted['javascript_errors'], 1)
        self.assertEqual(PageView.objects.count(), 1)

    def test_las_peticiones_tienen_su_propia_ventana(self):
        RequestMetric.objects.create(
            method='GET',
            path='/',
            status_code=200,
            duration_ms=5,
            created_at=self.now - timedelta(days=15),
        )
        RequestMetric.objects.create(
            method='GET',
            path='/',
            status_code=200,
            duration_ms=5,
            created_at=self.now - timedelta(days=2),
        )
        deleted = prune_monitoring(now=self.now)
        self.assertEqual(deleted['request_metrics'], 1)
        self.assertEqual(RequestMetric.objects.count(), 1)

    @override_settings(MONITORING_RETENTION_DAYS=7, MONITORING_HTTP_RETENTION_DAYS=1)
    def test_las_ventanas_se_configuran_por_settings(self):
        PageView.objects.create(
            session_hash='s1', path='/', created_at=self.now - timedelta(days=8)
        )
        RequestMetric.objects.create(
            method='GET',
            path='/',
            status_code=200,
            duration_ms=5,
            created_at=self.now - timedelta(days=2),
        )
        deleted = prune_monitoring(now=self.now)
        self.assertEqual(deleted['pageviews'], 1)
        self.assertEqual(deleted['request_metrics'], 1)

    def test_comando_de_gestion(self):
        PageView.objects.create(
            session_hash='s1', path='/', created_at=self.now - timedelta(days=40)
        )
        out = StringIO()
        call_command('prune_monitoring', stdout=out)
        self.assertIn('Registros borrados', out.getvalue())
        self.assertEqual(PageView.objects.count(), 0)
