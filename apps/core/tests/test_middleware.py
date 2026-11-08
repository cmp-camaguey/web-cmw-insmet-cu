from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.commercial.models import Customer
from apps.commercial.tests.factories import next_identity_document
from apps.core.models import SiteConfiguration


def disable_maintenance_mode():
    SiteConfiguration.objects.get_or_create(defaults={'maintenance_mode': False})


def set_maintenance(value):
    SiteConfiguration.objects.update_or_create(defaults={'maintenance_mode': value})


def _make_user(username, **kwargs):
    data = {'first_name': 'Test', 'last_name': 'User'}
    data.update(kwargs)
    return User.objects.create_user(username, f'{username}@example.com', 'pass', **data)


class CheckUserProfileMiddlewareTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.dashboard_url = reverse('dashboard:index')
        cls.profile_update_url = reverse('user_auth:profile_update')

    def test_incomplete_personal_redirects(self):
        user = _make_user('incomplete', first_name='')
        self.client.force_login(user)
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.profile_update_url)

    def test_complete_user_passes(self):
        user = _make_user('complete', is_staff=True)
        self.client.force_login(user)
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 200)

    def test_incomplete_profile_can_still_log_out(self):
        """El redirect al perfil no puede atrapar la baja de sesión.

        `MaintenanceModeMiddleware` exime `/logout/` para que quien quedó con
        sesión abierta pueda cerrarla, pero este middleware corre ANTES: si no
        exime la ruta, la excepción del mantenimiento nunca llega y el usuario
        con el perfil a medias queda encerrado.
        """
        user = _make_user('incomplete_logout', first_name='')
        self.client.force_login(user)

        response = self.client.post(reverse('user_auth:logout'))

        self.assertNotEqual(response.url, self.profile_update_url)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_no_redirect_when_already_on_profile_update(self):
        user = _make_user('onprofile', first_name='', is_staff=True)
        self.client.force_login(user)
        response = self.client.get(self.profile_update_url)
        self.assertEqual(response.status_code, 200)

    def test_natural_customer_missing_fields_redirects(self):
        user = _make_user('natmissing', is_staff=True)
        # El test necesita un cliente incompleto a propósito: la validación de
        # `save()` exige identidad y acá el objeto es justamente el caso roto
        # que el middleware tiene que detectar.
        Customer(
            client_type='natural',
            user=user,
            account='',
            agency_bank='',
            address='',
            phone='',
        ).save(run_validators=False)
        self.client.force_login(user)
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.profile_update_url)

    def test_natural_customer_complete_passes(self):
        user = _make_user('natok', is_staff=True)
        Customer.objects.create(
            client_type='natural',
            user=user,
            identity_document=next_identity_document(),
            account='1234567890123456',
            agency_bank='Banco Test',
            address='Calle 1',
            phone='12345678',
        )
        self.client.force_login(user)
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 200)

    def test_juridica_customer_missing_company_fields_redirects(self):
        user = _make_user('jurmissing', is_staff=True)
        Customer(
            client_type='juridica',
            user=user,
            account='1234567890123456',
            agency_bank='Banco Test',
            address='Calle 1',
            phone='12345678',
            company_name='',
            reeup='',
            nit='',
        ).save(run_validators=False)
        self.client.force_login(user)
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.profile_update_url)


class MaintenanceModeMiddlewareTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.dashboard_url = reverse('dashboard:index')
        cls.login_url = reverse('user_auth:login')

    def test_maintenance_blocks_non_superuser(self):
        set_maintenance(True)
        user = _make_user('blocked', is_staff=True)
        self.client.force_login(user)
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, 'Temporalmente en mantenimiento', status_code=503)

    def test_maintenance_blocks_anonymous_user(self):
        set_maintenance(True)
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, 'Temporalmente en mantenimiento', status_code=503)
        self.assertContains(response, 'Iniciar sesión', status_code=503)

    def test_maintenance_allows_superuser(self):
        set_maintenance(True)
        admin = User.objects.create_superuser(
            'maintadmin',
            'maintadmin@example.com',
            'pass',
            first_name='Admin',
            last_name='Super',
        )
        self.client.force_login(admin)
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 200)

    def test_maintenance_allows_login_path(self):
        set_maintenance(True)
        user = _make_user('loginok')
        self.client.force_login(user)
        response = self.client.get(self.login_url)
        self.assertEqual(response.status_code, 200)

    def test_incomplete_profile_can_log_out_during_maintenance(self):
        """Las dos excepciones tienen que alinearse, no sólo la del mantenimiento."""
        set_maintenance(True)
        user = _make_user('maint_logout', first_name='')
        self.client.force_login(user)

        self.client.post(reverse('user_auth:logout'))

        self.assertNotIn('_auth_user_id', self.client.session)

    def test_maintenance_off_allows(self):
        set_maintenance(False)
        user = _make_user('notmaint', is_staff=True)
        self.client.force_login(user)
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 200)
