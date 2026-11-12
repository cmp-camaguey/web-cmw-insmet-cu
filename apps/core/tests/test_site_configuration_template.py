from io import BytesIO

from django.contrib.auth.models import User
from django.core.files import File
from django.test import TestCase
from django.urls import reverse

from apps.core.models import SiteConfiguration


def _make_image(name):
    """PNG-ish file of minimal size, good enough for FieldFile.url rendering."""
    from PIL import Image

    buf = BytesIO()
    Image.new('RGB', (16, 16), '#2b4b9b').save(buf, format='PNG')
    buf.seek(0)
    return name, buf


class SiteConfigurationTemplateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        SiteConfiguration.objects.get_or_create(defaults={'maintenance_mode': False})
        cls.user = User.objects.create_superuser(
            'site_admin',
            'site_admin@example.com',
            'password',
            first_name='Site',
            last_name='Admin',
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.url = reverse('core:site_configuration')

    def _permission(self):
        from django.contrib.auth.models import Permission

        return Permission.objects.get(
            content_type__app_label='core', codename='change_siteconfiguration'
        )

    def _get_edit_page(self):
        site = SiteConfiguration.get_instance()
        site.maintenance_mode = False
        site.save()
        return self.client.get(self.url)

    def test_uses_form_layout_shell(self):
        response = self._get_edit_page()
        self.assertEqual(response.status_code, 200)
        # The page must use the standard form shell (multipart + novalidate),
        # obtained by extending layouts/form.html.
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, 'novalidate')

    def test_section_cards_rendered(self):
        response = self._get_edit_page()
        # Section titles as set by the form_card includes.
        self.assertContains(response, 'Colores y tema')
        self.assertContains(response, 'Identidad')
        self.assertContains(response, 'Horario y región')

    def test_primary_and_theme_base_side_by_side(self):
        response = self._get_edit_page()
        # Both color/theme field widgets render with their ids.
        self.assertContains(response, 'id_primary_color')
        self.assertContains(response, 'id_theme_base')
        # Both select/color fields sit in side-by-side col-md-6 cells.
        self.assertGreaterEqual(response.content.count(b'col-md-6'), 2)

    def test_theme_font_and_radius_fields_rendered(self):
        response = self._get_edit_page()
        self.assertContains(response, 'id_theme_font')
        self.assertContains(response, 'id_theme_radius')
        # Font and radius are admin-only, so their options come from the model.
        self.assertContains(response, 'Sans-serif')
        self.assertContains(response, 'Radio de esquina')

    def test_primary_color_uses_coloris_picker(self):
        response = self._get_edit_page()
        # primary_color is a coloris-driven text input (not the native swatch).
        self.assertContains(response, 'data-coloris')
        self.assertContains(response, 'dist/libs/coloris/coloris.min.js')
        self.assertContains(response, 'dist/libs/coloris/coloris.min.css')

    def test_reset_theme_defaults_button_and_modal(self):
        response = self._get_edit_page()
        # The reset button lives in the page-header title_actions. Its label was
        # shortened to "Restablecer" in 75eca8e, so assert the label anchored to
        # its own element instead of a bare word that could match anywhere.
        self.assertContains(response, 'data-bs-target="#reset-theme-modal"')
        self.assertContains(response, 'id="reset-theme-modal-title">Restablecer<')
        self.assertContains(response, 'reset-theme-modal')
        self.assertContains(response, 'reset_theme_defaults')

    def test_maintenance_card_present_for_superuser(self):
        response = self._get_edit_page()
        self.assertContains(response, 'Modo de mantenimiento')
        self.assertContains(response, 'Desactivado')
        self.assertContains(response, 'id="maintenance_mode"')
        self.assertContains(response, 'toggle_maintenance')

    def test_maintenance_card_absent_for_non_superuser(self):
        from django.contrib.auth.models import Permission, User

        user = User.objects.create_user(
            'no_sudo',
            'no_sudo@example.com',
            'password',
            first_name='No',
            last_name='Sudo',
        )
        perm = Permission.objects.get(
            content_type__app_label='core', codename='change_siteconfiguration'
        )
        user.user_permissions.add(perm)
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Modo de mantenimiento')

    def test_brand_logo_hint_present(self):
        response = self._get_edit_page()
        self.assertContains(
            response,
            'Formatos: JPG, PNG, GIF. No se admiten SVG. Dejar en blanco si no desea '
            'cambiar el logo actual.',
        )

    def test_favicon_preview_and_link_when_set(self):
        site = SiteConfiguration.get_instance()
        name, buf = _make_image('favicon.png')
        site.favicon = File(buf, name=name)
        site.save()
        response = self._get_edit_page()
        self.assertContains(response, 'id_favicon')
        self.assertContains(response, 'Ver imagen')
        self.assertContains(response, site.favicon.url)
        self.assertContains(response, 'data-fslightbox')

    def test_favicon_hint_present_when_unset(self):
        site = SiteConfiguration.get_instance()
        site.favicon.delete(save=True)
        response = self._get_edit_page()
        self.assertContains(
            response,
            'Formatos: JPG, PNG, GIF. No se admiten SVG. Dejar en blanco si no desea '
            'cambiar el favicon actual.',
        )

    def test_logo_and_favicon_side_by_side(self):
        response = self._get_edit_page()
        self.assertContains(response, 'id_brand_logo')
        self.assertContains(response, 'id_favicon')
        # brand_logo and favicon each live in their own col-md-6 cell, side by side.
        self.assertGreaterEqual(response.content.count(b'id_brand_logo'), 1)
        self.assertIn(b'id_brand_logo', response.content)
        self.assertIn(b'id_favicon', response.content)

    def test_no_clearable_widget_cartel(self):
        """Django's ClearableFileInput 'Current:'/'Borrar' box must NOT render."""
        site = SiteConfiguration.get_instance()
        name, buf = _make_image('brand_logo.png')
        site.brand_logo = File(buf, name=name)
        name2, buf2 = _make_image('favicon.png')
        site.favicon = File(buf2, name=name2)
        site.save()
        response = self._get_edit_page()
        self.assertNotContains(response, 'Actualmente:')
        self.assertNotContains(response, 'currently')

    def test_logo_eliminar_button_present_when_set(self):
        site = SiteConfiguration.get_instance()
        name, buf = _make_image('brand_logo.png')
        site.brand_logo = File(buf, name=name)
        site.save()
        response = self._get_edit_page()
        self.assertContains(response, 'name="delete_logo"')
        self.assertContains(response, 'Eliminar')

    def test_favicon_eliminar_button_present_when_set(self):
        site = SiteConfiguration.get_instance()
        name, buf = _make_image('favicon.png')
        site.favicon = File(buf, name=name)
        site.save()
        response = self._get_edit_page()
        self.assertContains(response, 'name="delete_favicon"')
        self.assertContains(response, 'Eliminar')

    def test_delete_logo_action_removes_file(self):
        site = SiteConfiguration.get_instance()
        name, buf = _make_image('brand_logo.png')
        site.brand_logo = File(buf, name=name)
        site.save()
        self.assertTrue(site.brand_logo)
        response = self.client.post(self.url, {'delete_logo': 'Eliminar'})
        self.assertRedirects(response, self.url)
        site.refresh_from_db()
        self.assertFalse(bool(site.brand_logo))

    def test_delete_favicon_action_removes_file(self):
        site = SiteConfiguration.get_instance()
        name, buf = _make_image('favicon.png')
        site.favicon = File(buf, name=name)
        site.save()
        self.assertTrue(site.favicon)
        response = self.client.post(self.url, {'delete_favicon': 'Eliminar'})
        self.assertRedirects(response, self.url)
        site.refresh_from_db()
        self.assertFalse(bool(site.favicon))
