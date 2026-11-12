import datetime as dt

from django.conf import settings
from django.contrib.auth.models import Permission, User
from django.template import Context, Template
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.forms.site_configuration import TimezoneConfigurationForm
from apps.core.middleware import SiteTimezoneMiddleware
from apps.core.models import ActivityLog, SiteConfiguration
from apps.core.timezone_utils import (
    get_request_time_format,
    reset_request_time_format,
    set_request_time_format,
)


def _disable_maintenance():
    SiteConfiguration.objects.get_or_create(defaults={'maintenance_mode': False})


def _permission():
    return Permission.objects.get(
        content_type__app_label='core', codename='change_siteconfiguration'
    )


class SiteConfigurationTimezoneModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _disable_maintenance()

    def test_auto_timezone_uses_default_timezone(self):
        site = SiteConfiguration(timezone_mode='auto')
        self.assertEqual(site.get_timezone(), timezone.get_default_timezone())

    def test_manual_timezone_uses_fixed_offset(self):
        site = SiteConfiguration(timezone_mode='manual', utc_offset_hours=-3)
        tz = site.get_timezone()
        self.assertEqual(tz.utcoffset(None), dt.timedelta(hours=-3))
        self.assertEqual(tz.tzname(None), 'UTC-03:00')

    def test_manual_timezone_can_add_hours(self):
        site = SiteConfiguration(timezone_mode='manual', utc_offset_hours=2)
        self.assertEqual(site.get_timezone().utcoffset(None), dt.timedelta(hours=2))

    def test_time_format_24h(self):
        site = SiteConfiguration(time_format='24')
        self.assertEqual(site.get_time_format(), 'H:i')

    def test_time_format_12h(self):
        site = SiteConfiguration(time_format='12')
        self.assertEqual(site.get_time_format(), 'h:i A')


class TimezoneConfigurationFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _disable_maintenance()
        cls.instance = SiteConfiguration.get_instance()

    def test_manual_mode_with_offset_is_valid(self):
        form = TimezoneConfigurationForm(
            data={
                'timezone_mode': 'manual',
                'utc_offset_hours': '-3',
                'time_format': '12',
            },
            instance=self.instance,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['utc_offset_hours'], -3)

    def test_manual_mode_without_offset_is_rejected(self):
        form = TimezoneConfigurationForm(
            data={'timezone_mode': 'manual', 'time_format': '24'},
            instance=self.instance,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('utc_offset_hours', form.errors)

    def test_auto_mode_without_offset_keeps_stored_value(self):
        self.instance.utc_offset_hours = -5
        self.instance.save()
        form = TimezoneConfigurationForm(
            data={'timezone_mode': 'auto', 'time_format': '24'},
            instance=self.instance,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['utc_offset_hours'], -5)

    def test_offset_outside_range_is_rejected(self):
        form = TimezoneConfigurationForm(
            data={
                'timezone_mode': 'manual',
                'utc_offset_hours': '99',
                'time_format': '24',
            },
            instance=self.instance,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('utc_offset_hours', form.errors)

    def test_invalid_time_format_is_rejected(self):
        form = TimezoneConfigurationForm(
            data={'timezone_mode': 'auto', 'time_format': '13'},
            instance=self.instance,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('time_format', form.errors)


class TimezoneConfigurationViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _disable_maintenance()
        cls.url = reverse('core:timezone_configuration')

    def _user(self, username, permission=True):
        user = User.objects.create_user(
            username,
            f'{username}@example.com',
            'password',
            first_name='Time',
            last_name='Zone',
        )
        if permission:
            user.user_permissions.add(_permission())
        return user

    def test_anonymous_get_redirects_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_authenticated_without_permission_gets_403(self):
        self.client.force_login(self._user('tz_no_perm', permission=False))
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_authenticated_with_permission_gets_200(self):
        self.client.force_login(self._user('tz_perm'))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Horario y región')
        self.assertContains(response, 'id_timezone_mode')
        self.assertContains(response, 'id_utc_offset_hours')
        self.assertContains(response, 'id_time_format')
        self.assertContains(response, 'Automático')
        self.assertContains(response, '24 horas')
        self.assertContains(response, '12 horas (AM/PM)')
        self.assertEqual(response.context['server_timezone'], settings.TIME_ZONE)

    def test_menu_links_to_timezone_section(self):
        self.client.force_login(self._user('tz_menu'))
        response = self.client.get(reverse('core:site_configuration'))
        self.assertContains(response, 'Horario y región')
        self.assertContains(response, self.url)

    def test_post_saves_values_and_redirects(self):
        self.client.force_login(self._user('tz_post'))
        response = self.client.post(
            self.url,
            {
                'timezone_mode': 'manual',
                'utc_offset_hours': '-4',
                'time_format': '12',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.url)
        site = SiteConfiguration.get_instance()
        self.assertEqual(site.timezone_mode, 'manual')
        self.assertEqual(site.utc_offset_hours, -4)
        self.assertEqual(site.time_format, '12')
        self.assertTrue(
            ActivityLog.objects.filter(
                message__icontains='horario y región',
            ).exists()
        )


class SiteTimezoneMiddlewareTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _disable_maintenance()
        cls.site = SiteConfiguration.get_instance()

    def setUp(self):
        self.factory = RequestFactory()
        self.site.timezone_mode = 'manual'
        self.site.utc_offset_hours = -3
        self.site.time_format = '12'
        self.site.save()

    def _run(self):
        request = self.factory.get('/')
        request.site_config = self.site
        seen = {}

        def get_response(req):
            seen['tz'] = timezone.get_current_timezone()
            seen['format'] = get_request_time_format()
            return 'ok'

        result = SiteTimezoneMiddleware(get_response)(request)
        return result, seen

    def test_activates_manual_timezone_and_format_during_request(self):
        result, seen = self._run()
        self.assertEqual(result, 'ok')
        self.assertEqual(seen['tz'].utcoffset(None), dt.timedelta(hours=-3))
        self.assertEqual(seen['format'], 'h:i A')

    def test_deactivates_timezone_and_format_after_request(self):
        self._run()
        self.assertEqual(timezone.get_current_timezone(), timezone.get_default_timezone())
        self.assertEqual(get_request_time_format(), 'H:i')


class LocalDateTimeFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _disable_maintenance()

    def _render(self, template, time_format='24', offset=-5):
        value = dt.datetime(2026, 11, 9, 18, 30, tzinfo=dt.UTC)
        site = SiteConfiguration(time_format=time_format, timezone_mode='manual')
        site.utc_offset_hours = offset
        timezone.activate(site.get_timezone())
        set_request_time_format(site.get_time_format())
        try:
            return Template('{% load my_filters %}' + template).render(Context({'value': value}))
        finally:
            timezone.deactivate()
            reset_request_time_format()

    def test_24h_format(self):
        rendered = self._render('{{ value|local_datetime:"d/m/Y" }}')
        self.assertEqual(rendered, '09/11/2026 13:30')

    def test_12h_format(self):
        rendered = self._render('{{ value|local_datetime:"d/m/Y" }}', time_format='12')
        self.assertEqual(rendered, '09/11/2026 01:30 PM')

    def test_12h_format_with_seconds(self):
        rendered = self._render('{{ value|local_datetime_seconds:"d/m/Y" }}', time_format='12')
        self.assertEqual(rendered, '09/11/2026 01:30:00 PM')

    def test_offset_can_change_the_day(self):
        value = dt.datetime(2026, 11, 10, 2, 0, tzinfo=dt.UTC)
        site = SiteConfiguration(timezone_mode='manual', utc_offset_hours=-5)
        timezone.activate(site.get_timezone())
        set_request_time_format('H:i')
        try:
            rendered = Template('{% load my_filters %}{{ value|local_datetime:"d/m/Y" }}').render(
                Context({'value': value})
            )
        finally:
            timezone.deactivate()
            reset_request_time_format()
        self.assertEqual(rendered, '09/11/2026 21:00')

    def test_local_time_filter(self):
        rendered = self._render('{{ value|local_time }}')
        self.assertEqual(rendered, '13:30')
