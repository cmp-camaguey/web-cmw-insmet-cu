from django.test import TestCase

from apps.core.forms.site_configuration import SiteConfigurationForm
from apps.core.models import SiteConfiguration


def _valid_data():
    return {
        'primary_color': '#2b4b9b',
        'theme_base': 'gray',
        'theme_font': 'sans-serif',
        'theme_radius': '1',
        'timezone_mode': 'auto',
        'utc_offset_hours': '-5',
        'time_format': '24',
    }


class SiteConfigurationFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.instance = SiteConfiguration.get_instance()

    def test_valid_form_accepts_lowercase_hex(self):
        form = SiteConfigurationForm(data=_valid_data(), instance=self.instance)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['primary_color'], '#2b4b9b')

    def test_valid_form_accepts_uppercase_hex(self):
        data = _valid_data()
        data['primary_color'] = '#2B4B9B'
        form = SiteConfigurationForm(data=data, instance=self.instance)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['primary_color'], '#2B4B9B')

    def test_named_color_rejected(self):
        data = _valid_data()
        data['primary_color'] = 'red'
        form = SiteConfigurationForm(data=data, instance=self.instance)
        self.assertFalse(form.is_valid())
        self.assertIn('primary_color', form.errors)

    def test_short_hex_rejected(self):
        data = _valid_data()
        data['primary_color'] = '#12345'
        form = SiteConfigurationForm(data=data, instance=self.instance)
        self.assertFalse(form.is_valid())
        self.assertIn('primary_color', form.errors)

    def test_long_hex_rejected(self):
        data = _valid_data()
        data['primary_color'] = '#1234567'
        form = SiteConfigurationForm(data=data, instance=self.instance)
        self.assertFalse(form.is_valid())
        self.assertIn('primary_color', form.errors)

    def test_missing_required_primary_color_rejected(self):
        data = _valid_data()
        data['primary_color'] = ''
        form = SiteConfigurationForm(data=data, instance=self.instance)
        self.assertFalse(form.is_valid())
        self.assertIn('primary_color', form.errors)
