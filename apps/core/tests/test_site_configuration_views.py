from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.core.models import ActivityLog, SiteConfiguration


def _valid_data():
    return {
        'primary_color': '#2b4b9b',
        'theme_base': 'gray',
        'theme_font': 'sans-serif',
        'theme_radius': '1',
    }


def disable_maintenance_mode():
    SiteConfiguration.objects.get_or_create(defaults={'maintenance_mode': False})


class SiteConfigurationViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.url = reverse('core:site_configuration')
        cls.other_user = User.objects.create_user(
            'worker',
            'worker@example.com',
            'password',
            first_name='Worker',
            last_name='User',
        )

    def _permissions(self, app_label, codename):
        from django.contrib.auth.models import Permission

        perm = Permission.objects.get(content_type__app_label=app_label, codename=codename)
        return [perm]

    def test_anonymous_get_redirects_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_authenticated_without_permission_gets_403(self):
        self.client.force_login(self.other_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_authenticated_with_permission_gets_200(self):
        user = User.objects.create_user(
            'admin_site',
            'admin_site@example.com',
            'password',
            first_name='Admin',
            last_name='Site',
        )
        user.user_permissions.add(*self._permissions('core', 'change_siteconfiguration'))
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_post_valid_updates_singleton_and_redirects(self):
        user = User.objects.create_user(
            'admin_site2',
            'admin_site2@example.com',
            'password',
            first_name='Admin',
            last_name='Site',
        )
        user.user_permissions.add(*self._permissions('core', 'change_siteconfiguration'))
        self.client.force_login(user)
        singleton_before = SiteConfiguration.get_instance()
        singleton_before.primary_color = '#123456'
        singleton_before.save()
        data = _valid_data()
        data['primary_color'] = '#2b4b9b'
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('core:site_configuration'))
        singleton_after = SiteConfiguration.get_instance()
        self.assertEqual(singleton_after.primary_color, '#2b4b9b')
        self.assertEqual(singleton_after.pk, singleton_before.pk)

    def test_post_invalid_hex_rejected(self):
        user = User.objects.create_user(
            'admin_site3',
            'admin_site3@example.com',
            'password',
            first_name='Admin',
            last_name='Site',
        )
        user.user_permissions.add(*self._permissions('core', 'change_siteconfiguration'))
        self.client.force_login(user)
        data = _valid_data()
        data['primary_color'] = '#12345'
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'formato #RRGGBB')

    def test_reset_theme_defaults_restores_defaults(self):
        user = User.objects.create_user(
            'admin_site4',
            'admin_site4@example.com',
            'password',
            first_name='Admin',
            last_name='Site',
        )
        user.user_permissions.add(*self._permissions('core', 'change_siteconfiguration'))
        self.client.force_login(user)
        site = SiteConfiguration.get_instance()
        site.primary_color = '#123456'
        site.theme_base = 'zinc'
        site.theme_font = 'serif'
        site.theme_radius = '2'
        site.save()
        response = self.client.post(self.url, {'reset_theme_defaults': 'Restablecer'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('core:site_configuration'))
        site.refresh_from_db()
        self.assertEqual(site.primary_color, '#2b4b9b')
        self.assertEqual(site.theme_base, 'neutral')
        self.assertEqual(site.theme_font, 'sans-serif')
        self.assertEqual(site.theme_radius, '1')

    def test_toggle_maintenance_non_superuser_forbidden(self):
        user = User.objects.create_user(
            'worker_perm',
            'worker_perm@example.com',
            'password',
            first_name='Worker',
            last_name='Perm',
        )
        user.user_permissions.add(*self._permissions('core', 'change_siteconfiguration'))
        self.client.force_login(user)
        site = SiteConfiguration.get_instance()
        site.maintenance_mode = False
        site.save()
        response = self.client.post(self.url, {'toggle_maintenance': '1', 'maintenance_mode': '1'})
        self.assertEqual(response.status_code, 403)
        site.refresh_from_db()
        self.assertFalse(site.maintenance_mode)

    def test_toggle_maintenance_superuser_enables(self):
        user = User.objects.create_superuser(
            'admin_sudo',
            'admin_sudo@example.com',
            'password',
            first_name='Admin',
            last_name='Sudo',
        )
        self.client.force_login(user)
        site = SiteConfiguration.get_instance()
        site.maintenance_mode = False
        site.save()
        response = self.client.post(self.url, {'toggle_maintenance': '1', 'maintenance_mode': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'ok': True, 'maintenance_mode': True})
        site.refresh_from_db()
        self.assertTrue(site.maintenance_mode)
        entry = ActivityLog.objects.filter(
            object_id=str(site.pk), action_flag=ActivityLog.ACTIVITY_MAINTENANCE
        ).latest('action_time')
        self.assertEqual(entry.get_action_flag_display(), 'Mantenimiento')

    def test_toggle_maintenance_superuser_disables(self):
        user = User.objects.create_superuser(
            'admin_sudo2',
            'admin_sudo2@example.com',
            'password',
            first_name='Admin',
            last_name='Sudo2',
        )
        self.client.force_login(user)
        site = SiteConfiguration.get_instance()
        site.maintenance_mode = True
        site.save()
        response = self.client.post(self.url, {'toggle_maintenance': '1', 'maintenance_mode': '0'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'ok': True, 'maintenance_mode': False})
        site.refresh_from_db()
        self.assertFalse(site.maintenance_mode)
