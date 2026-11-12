from django.test import SimpleTestCase, override_settings

from apps.monitoring.services import system


class SystemSnapshotTests(SimpleTestCase):
    def test_snapshot_trae_todas_las_secciones(self):
        data = system.system_snapshot()
        for key in (
            'cpu',
            'memory',
            'swap',
            'disk',
            'disk_io',
            'network',
            'load',
            'process',
            'host_uptime_s',
            'host_uptime_display',
        ):
            self.assertIn(key, data)

    def test_la_primera_lectura_de_cpu_no_inventa_un_cero(self):
        system._cpu_primed = False
        try:
            first = system.system_snapshot()
            self.assertIsNone(first['cpu']['percent'])
            self.assertEqual(first['cpu']['cores'], [])
            second = system.system_snapshot()
            self.assertIsInstance(second['cpu']['percent'], float)
            self.assertEqual(len(second['cpu']['cores']), second['cpu']['count'])
        finally:
            system._cpu_primed = True

    def test_memoria_y_disco_estan_en_rango(self):
        data = system.system_snapshot()
        self.assertGreaterEqual(data['memory']['percent'], 0)
        self.assertLessEqual(data['memory']['percent'], 100)
        self.assertIsNotNone(data['memory']['total'])
        self.assertGreaterEqual(data['disk']['percent'], 0)
        self.assertLessEqual(data['disk']['percent'], 100)

    @override_settings(MONITORING_DISK_PATH='/nonexistent-path-for-monitoring')
    def test_disco_inexistente_no_rompe_la_foto(self):
        data = system.system_snapshot()
        self.assertIsNone(data['disk']['percent'])
        self.assertEqual(data['disk']['path'], '/nonexistent-path-for-monitoring')

    def test_tamanos_legibles(self):
        self.assertEqual(system._bytes_human(0), '0 B')
        self.assertEqual(system._bytes_human(1024), '1.0 KB')
        self.assertEqual(system._bytes_human(1024 * 1024), '1.0 MB')
        self.assertEqual(system._bytes_human(1024 * 1024 * 1024), '1.0 GB')
        self.assertIsNone(system._bytes_human(None))

    def test_carga_y_uptime_del_host(self):
        data = system.system_snapshot()
        self.assertIsNotNone(data['host_uptime_s'])
        self.assertIsNotNone(data['host_uptime_display'])
        self.assertIn('pid', data['process'])
