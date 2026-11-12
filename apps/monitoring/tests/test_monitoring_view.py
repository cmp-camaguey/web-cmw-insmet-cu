from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import SiteConfiguration
from apps.monitoring.models import RequestMetric


def superuser(username='root'):
    return User.objects.create_superuser(
        username, f'{username}@example.com', 'pass', first_name='Root', last_name='User'
    )


class SiteMonitoringAccessTests(TestCase):
    def setUp(self):
        SiteConfiguration.objects.get_or_create(defaults={'maintenance_mode': False})

    def test_anonimo_va_al_login(self):
        response = self.client.get(reverse('dashboard:monitoring'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/', response['Location'])

    def test_staff_sin_superusuario_no_entra(self):
        # El perfil completo evita el redirect de `CheckUserProfileMiddleware`:
        # lo que se prueba es el rechazo de la vista, no el middleware de perfil.
        user = User.objects.create_user(
            'staff',
            'staff@example.com',
            'pass',
            first_name='Staff',
            last_name='User',
            is_staff=True,
        )
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('dashboard:monitoring')).status_code, 403)
        self.assertEqual(self.client.get(reverse('dashboard:monitoring_data')).status_code, 403)

    def test_superusuario_ve_el_panel(self):
        self.client.force_login(superuser())
        response = self.client.get(reverse('dashboard:monitoring'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['title'], 'Monitoreo del sitio')
        self.assertEqual(response.context['segment'], 'site_monitoring')
        self.assertIn('snapshot', response.context)
        self.assertContains(response, 'Recursos del servidor')
        self.assertContains(response, 'Core Web Vitals')
        self.assertContains(response, 'Tráfico en vivo')

    def test_endpoint_json_trae_el_snapshot_completo(self):
        self.client.force_login(superuser())
        response = self.client.get(reverse('dashboard:monitoring_data'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        for key in (
            'generated_at',
            'enabled',
            'status',
            'system',
            'health',
            'http',
            'uptime',
            'incident',
            'traffic',
            'vitals',
            'errors',
            'weight',
        ):
            self.assertIn(key, data)
        self.assertIn('cpu', data['system'])
        self.assertIn('slots', data['uptime'])
        self.assertEqual(len(data['uptime']['slots']), 96)

    @override_settings(MONITORING_ENABLED=True)
    def test_sin_captura_no_se_muestra_el_aviso_de_apagado(self):
        self.client.force_login(superuser())
        response = self.client.get(reverse('dashboard:monitoring'))
        self.assertNotContains(response, 'MONITORING_ENABLED=False')

    def test_con_captura_apagada_se_avisa(self):
        self.client.force_login(superuser())
        response = self.client.get(reverse('dashboard:monitoring'))
        self.assertContains(response, 'MONITORING_ENABLED=False')


class RequestMetricsMiddlewareTests(TestCase):
    def setUp(self):
        SiteConfiguration.objects.get_or_create(defaults={'maintenance_mode': False})

    @override_settings(MONITORING_ENABLED=True)
    def test_registra_la_peticion_servida(self):
        self.client.get('/tiempo/hoy/')
        self.assertTrue(RequestMetric.objects.filter(path='/tiempo/hoy/').exists())

    @override_settings(MONITORING_ENABLED=True)
    def test_no_registra_su_propio_endpoint(self):
        self.client.force_login(superuser())
        self.client.get(reverse('dashboard:monitoring_data'))
        self.assertFalse(RequestMetric.objects.filter(path='/dashboard/monitoring/data/').exists())

    def test_con_la_captura_apagada_no_escribe(self):
        self.client.get('/tiempo/hoy/')
        self.assertEqual(RequestMetric.objects.count(), 0)


class BeaconInjectionTests(TestCase):
    def setUp(self):
        SiteConfiguration.objects.get_or_create(defaults={'maintenance_mode': False})

    @override_settings(MONITORING_ENABLED=True)
    def test_las_paginas_publicas_incluyen_el_beacon(self):
        response = self.client.get('/tiempo/hoy/')
        self.assertContains(response, 'rum-beacon.js')
        self.assertContains(response, '/rum/collect/')

    def test_con_la_captura_apagada_no_se_inyecta(self):
        response = self.client.get('/tiempo/hoy/')
        self.assertNotContains(response, 'rum-beacon.js')
