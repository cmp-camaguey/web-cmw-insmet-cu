import os
import shutil
import stat
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import Permission, User
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.core.env_config import (
    EDITABLE_FIELDS,
    as_bool,
    build_environment,
    effective_value,
    same_value,
    secret_status,
    strip_url_credentials,
)
from apps.core.env_file import is_writable, read_env_file, update_env_file
from apps.core.models import ActivityLog, SiteConfiguration
from config.settings.base import resolve_ldap_enabled

CANNOT_DROP_PRIVILEGES = hasattr(os, 'geteuid') and os.geteuid() == 0


def _disable_maintenance():
    SiteConfiguration.objects.get_or_create(defaults={'maintenance_mode': False})


class LdapEnabledResolutionTests(SimpleTestCase):
    """El interruptor de LDAP no puede apagar la autenticación por accidente."""

    def test_explicit_flag_wins_over_the_server_uri(self):
        self.assertTrue(resolve_ldap_enabled({'LDAP_ENABLED': 'True', 'LDAP_SERVER_URI': ''}))
        self.assertFalse(
            resolve_ldap_enabled({'LDAP_ENABLED': 'False', 'LDAP_SERVER_URI': 'ldap://dc'})
        )

    def test_absent_flag_falls_back_to_the_server_uri(self):
        self.assertTrue(resolve_ldap_enabled({'LDAP_SERVER_URI': 'ldap://dc'}))
        self.assertFalse(resolve_ldap_enabled({}))

    def test_truthy_forms_are_accepted(self):
        for value in ('1', 'true', 'True', 'yes', 'on'):
            self.assertTrue(resolve_ldap_enabled({'LDAP_ENABLED': value}))


class EnvFileTests(TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tempdir, ignore_errors=True)
        self.path = Path(self.tempdir) / '.env'

    def _write(self, text, mode=0o600):
        self.path.write_text(text, encoding='utf-8')
        self.path.chmod(mode)

    def test_read_missing_file_returns_empty(self):
        self.assertEqual(read_env_file(self.path), {})

    def test_read_skips_comments_and_blank_lines(self):
        self._write('# comentario\n\nDEBUG=True\nDB_HOST=127.0.0.1\n')
        self.assertEqual(read_env_file(self.path), {'DEBUG': 'True', 'DB_HOST': '127.0.0.1'})

    def test_read_unquotes_values(self):
        self._write("DEFAULT_FROM_EMAIL='Centro <x@y.cu>'\n")
        self.assertEqual(read_env_file(self.path)['DEFAULT_FROM_EMAIL'], 'Centro <x@y.cu>')

    def test_update_preserves_comments_order_and_unknown_keys(self):
        original = (
            '# =====\n# sección\n# =====\nDEBUG=True\nDB_HOST=127.0.0.1\nCLAVE_AJENA=no-tocar\n'
        )
        self._write(original)

        changed = update_env_file(self.path, {'DEBUG': 'False'})

        self.assertEqual(changed, ['DEBUG'])
        self.assertEqual(
            self.path.read_text(encoding='utf-8'),
            original.replace('DEBUG=True', 'DEBUG=False'),
        )

    def test_update_appends_missing_key(self):
        self._write('DEBUG=True\n')
        changed = update_env_file(self.path, {'NOTIFICATIONS_MAX_VISIBLE': '25'})
        self.assertEqual(changed, ['NOTIFICATIONS_MAX_VISIBLE'])
        self.assertEqual(
            self.path.read_text(encoding='utf-8'),
            'DEBUG=True\nNOTIFICATIONS_MAX_VISIBLE=25\n',
        )

    def test_update_does_not_touch_commented_key(self):
        self._write('# NOTIFICATIONS_ENABLED=False\n')
        update_env_file(self.path, {'NOTIFICATIONS_ENABLED': 'True'})
        text = self.path.read_text(encoding='utf-8')
        self.assertIn('# NOTIFICATIONS_ENABLED=False', text)
        self.assertIn('\nNOTIFICATIONS_ENABLED=True\n', text)

    def test_update_preserves_inline_comment(self):
        self._write('DEBUG=True  # ojo con esto\n')
        update_env_file(self.path, {'DEBUG': 'False'})
        self.assertEqual(self.path.read_text(encoding='utf-8'), 'DEBUG=False  # ojo con esto\n')

    def test_update_quotes_values_with_spaces(self):
        self._write('SITE_URL=http://localhost:8000/\n')
        update_env_file(self.path, {'SITE_URL': 'https://cmp.cu/con espacio'})
        self.assertIn(
            "SITE_URL='https://cmp.cu/con espacio'", self.path.read_text(encoding='utf-8')
        )

    def test_update_rejects_newlines(self):
        self._write('DEBUG=True\n')
        with self.assertRaises(ValueError):
            update_env_file(self.path, {'SITE_URL': 'http://x.cu/\notra'})
        self.assertEqual(self.path.read_text(encoding='utf-8'), 'DEBUG=True\n')

    def test_update_preserves_file_mode(self):
        self._write('DEBUG=True\n', mode=0o640)
        update_env_file(self.path, {'DEBUG': 'False'})
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)

    def test_update_preserves_crlf(self):
        self.path.write_bytes(b'DEBUG=True\r\nDB_HOST=127.0.0.1\r\n')
        update_env_file(self.path, {'DEBUG': 'False'})
        self.assertEqual(self.path.read_bytes(), b'DEBUG=False\r\nDB_HOST=127.0.0.1\r\n')

    def test_is_writable_true_for_normal_file(self):
        self._write('DEBUG=True\n')
        self.assertTrue(is_writable(self.path))

    def test_is_writable_false_when_directory_is_read_only(self):
        if CANNOT_DROP_PRIVILEGES:
            self.skipTest('root ignora los permisos del directorio')
        self._write('DEBUG=True\n')
        self.path.parent.chmod(0o500)
        self.addCleanup(self.path.parent.chmod, 0o700)
        self.assertFalse(is_writable(self.path))


class EnvConfigTests(SimpleTestCase):
    def test_readonly_groups_are_only_the_boot_critical_ones(self):
        environment = build_environment(environ={}, file_values={})
        self.assertEqual(
            [group['key'] for group in environment['groups']],
            ['app', 'domain', 'database', 'cache'],
        )

    def test_editable_groups_cover_email_ldap_updates_and_ftp(self):
        groups = []
        for spec in EDITABLE_FIELDS:
            if spec.group not in groups:
                groups.append(spec.group)
        self.assertEqual(groups, ['app', 'notifications', 'email', 'ldap', 'updates', 'ftp'])

    def test_email_ldap_and_updates_are_editable(self):
        editable = {spec.key for spec in EDITABLE_FIELDS}
        for key in (
            'EMAIL_HOST',
            'EMAIL_PORT',
            'EMAIL_USE_TLS',
            'EMAIL_USE_SSL',
            'EMAIL_HOST_USER',
            'EMAIL_HOST_PASSWORD',
            'DEFAULT_FROM_EMAIL',
            'LDAP_ENABLED',
            'LDAP_SERVER_URI',
            'LDAP_BIND_DN',
            'LDAP_BIND_PASSWORD',
            'LDAP_USER_SEARCH_BASE',
            'UPDATE_REPO_URL',
            'UPDATE_BRANCH_REF',
            'UPDATE_CHECK_TTL',
            'UPDATE_CHECK_TIMEOUT',
            'FTP_OBS_PASS',
        ):
            self.assertIn(key, editable)

    def test_boot_critical_settings_stay_out_of_the_whitelist(self):
        editable = {spec.key for spec in EDITABLE_FIELDS}
        for key in (
            'DEBUG',
            'PRODUCTION',
            'SECRET_KEY',
            'ENCRYPTION_KEY',
            'EMAIL_BACKEND',
            'DB_PASS',
            'ALLOWED_HOSTS',
            'CSRF_TRUSTED_ORIGINS',
            'CORS_ALLOWED_ORIGINS',
            'USE_REDIS_CACHE',
            'REDIS_URL',
        ):
            self.assertNotIn(key, editable)

    def test_ldap_effective_values_come_from_the_environment(self):
        environ = {'LDAP_SERVER_URI': 'ldap://dc.cmp.cu:389'}
        self.assertEqual(
            effective_value('LDAP_SERVER_URI', environ=environ), 'ldap://dc.cmp.cu:389'
        )

    def test_ldap_start_tls_falls_back_to_its_default(self):
        self.assertEqual(effective_value('LDAP_START_TLS', environ={}), 'True')

    def test_domain_group_shows_external_hostname(self):
        environment = build_environment(
            environ={'EXTERNAL_HOSTNAME': 'https://cmp.camaguey.cu'}, file_values={}
        )
        domain = next(group for group in environment['groups'] if group['key'] == 'domain')
        values = {item['label']: item['value'] for item in domain['items']}
        self.assertEqual(values['Host externo'], 'https://cmp.camaguey.cu')

    def test_secret_status_never_includes_values(self):
        secrets = secret_status(environ={'ENCRYPTION_KEY': 'clave-de-prueba'})
        for entry in secrets:
            self.assertEqual(set(entry), {'key', 'label', 'configured'})
        rendered = repr(secrets)
        self.assertNotIn('clave-de-prueba', rendered)

    def test_secret_status_reports_configured_flags(self):
        secrets = {entry['key']: entry['configured'] for entry in secret_status(environ={})}
        self.assertTrue(secrets['SECRET_KEY'])
        self.assertFalse(secrets['ENCRYPTION_KEY'])

    def test_editable_secrets_are_not_listed_as_readonly_secrets(self):
        keys = {entry['key'] for entry in secret_status(environ={})}
        self.assertNotIn('EMAIL_HOST_PASSWORD', keys)
        self.assertNotIn('LDAP_BIND_PASSWORD', keys)
        self.assertNotIn('FTP_OBS_PASS', keys)

    def test_redis_url_credentials_are_stripped(self):
        self.assertEqual(
            strip_url_credentials('redis://:secreto@cache.local:6379/1'),
            'redis://***@cache.local:6379/1',
        )

    def test_redis_url_without_credentials_is_unchanged(self):
        self.assertEqual(
            strip_url_credentials('redis://cache.local:6379/1'),
            'redis://cache.local:6379/1',
        )

    @override_settings(
        CACHES={
            'default': {
                'BACKEND': 'django.core.cache.backends.redis.RedisCache',
                'LOCATION': 'redis://cache.local:6379/1',
            }
        }
    )
    def test_cache_group_reflects_effective_backend(self):
        environment = build_environment(environ={}, file_values={})
        cache = next(group for group in environment['groups'] if group['key'] == 'cache')
        values = {item['label']: item['value'] for item in cache['items']}
        self.assertEqual(values['Motor'], 'Redis')

    def test_effective_value_renders_bools_and_ints(self):
        self.assertEqual(effective_value('NOTIFICATIONS_ENABLED'), 'True')
        self.assertEqual(effective_value('NOTIFICATIONS_MAX_VISIBLE'), '10')

    def test_same_value_compares_bools_case_insensitively(self):
        spec = next(field for field in EDITABLE_FIELDS if field.kind == 'bool')
        self.assertTrue(same_value(spec, 'true', 'True'))
        self.assertFalse(same_value(spec, 'False', 'True'))

    def test_same_value_compares_ints(self):
        spec = next(field for field in EDITABLE_FIELDS if field.key == 'NOTIFICATIONS_MAX_VISIBLE')
        self.assertTrue(same_value(spec, ' 10 ', '10'))
        self.assertFalse(same_value(spec, '11', '10'))

    def test_pending_only_when_file_differs_from_effective(self):
        environment = build_environment(environ={}, file_values={'NOTIFICATIONS_MAX_VISIBLE': '25'})
        self.assertTrue(environment['pending']['NOTIFICATIONS_MAX_VISIBLE'])
        self.assertFalse(environment['pending']['NOTIFICATIONS_ENABLED'])

    def test_as_bool_accepts_common_forms(self):
        for value in ('1', 'true', 'True', 'yes', 'on'):
            self.assertTrue(as_bool(value))
        for value in ('0', 'false', '', None):
            self.assertFalse(as_bool(value))


class EnvironmentConfigViewTests(TestCase):
    def setUp(self):
        _disable_maintenance()
        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tempdir, ignore_errors=True)
        self.env_path = Path(self.tempdir) / '.env'
        self.env_path.write_text(
            '# comentario\nDEBUG=True\nDB_PASS=supersecreto\n', encoding='utf-8'
        )
        self.env_path.chmod(0o600)
        override = override_settings(ENV_FILE_PATH=self.env_path)
        override.enable()
        self.addCleanup(override.disable)

        self.url = reverse('core:environment_config')
        self.superuser = User.objects.create_user(
            username='root',
            password='x',
            email='root@cmp.cu',
            first_name='Root',
            last_name='User',
            is_staff=True,
            is_superuser=True,
        )
        self.staff = User.objects.create_user(
            username='staff',
            password='x',
            email='staff@cmp.cu',
            first_name='Staff',
            last_name='User',
            is_staff=True,
        )

    def _payload(self, **overrides):
        """Lo que enviaría el navegador con el formulario recién cargado.

        Se construye desde el `initial` real en vez de una lista fija: si se
        escribieran valores a mano, un cambio en el `.env` del entorno de
        desarrollo haría que los tests guardaran claves que nadie tocó.
        """
        form = self.client.get(self.url).context['form']
        data = {}
        for spec in EDITABLE_FIELDS:
            value = form.initial.get(spec.key)
            if spec.kind == 'bool':
                if value:
                    data[spec.key] = 'on'
            elif value is None:
                data[spec.key] = ''
            else:
                data[spec.key] = str(value)
        data.update(overrides)
        return data

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_staff_without_superuser_gets_403(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_superuser_gets_the_page(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Entorno del servidor')

    def test_page_never_renders_secret_values(self):
        self.env_path.write_text(
            '# comentario\nDEBUG=True\n'
            'DB_PASS=supersecreto\n'
            'EMAIL_HOST_PASSWORD=claveSmtpSecreta\n'
            'FTP_OBS_PASS=claveFtpSecreta\n',
            encoding='utf-8',
        )
        self.client.force_login(self.superuser)
        response = self.client.get(self.url)
        self.assertNotContains(response, 'supersecreto')
        self.assertNotContains(response, 'claveSmtpSecreta')
        self.assertNotContains(response, 'claveFtpSecreta')
        self.assertNotContains(response, effective_value('SECRET_KEY'))

    def test_password_fields_are_reported_as_configured_without_the_value(self):
        self.env_path.write_text('EMAIL_HOST_PASSWORD=claveSmtpSecreta\n', encoding='utf-8')
        self.client.force_login(self.superuser)
        response = self.client.get(self.url)
        self.assertContains(response, 'Configurada')
        self.assertNotContains(response, 'claveSmtpSecreta')

    def test_blank_password_does_not_erase_the_stored_one(self):
        self.env_path.write_text('EMAIL_HOST_PASSWORD=claveVieja\n', encoding='utf-8')
        self.client.force_login(self.superuser)

        self.client.post(self.url, self._payload(NOTIFICATIONS_MAX_VISIBLE='25'))

        text = self.env_path.read_text(encoding='utf-8')
        self.assertIn('EMAIL_HOST_PASSWORD=claveVieja', text)

    def test_new_password_is_written(self):
        self.env_path.write_text('EMAIL_HOST_PASSWORD=claveVieja\n', encoding='utf-8')
        self.client.force_login(self.superuser)

        self.client.post(self.url, self._payload(EMAIL_HOST_PASSWORD='claveNueva'))

        text = self.env_path.read_text(encoding='utf-8')
        self.assertIn('EMAIL_HOST_PASSWORD=claveNueva', text)
        self.assertNotIn('claveVieja', text)

    def test_email_section_and_ldap_section_are_editable(self):
        self.client.force_login(self.superuser)
        before = self.env_path.read_text(encoding='utf-8')

        response = self.client.post(
            self.url,
            self._payload(
                EMAIL_HOST='smtp.cmp.cu',
                EMAIL_PORT='2525',
                EMAIL_USE_TLS='on',
                EMAIL_USE_SSL='',
                LDAP_SERVER_URI='ldap://dc.cmp.cu:389',
                LDAP_USER_SEARCH_BASE='ou=users,dc=cmp,dc=cu',
                UPDATE_CHECK_TTL='600',
            ),
        )

        self.assertEqual(response.status_code, 302)
        text = self.env_path.read_text(encoding='utf-8')
        self.assertIn('EMAIL_HOST=smtp.cmp.cu', text)
        self.assertIn('EMAIL_PORT=2525', text)
        self.assertIn('LDAP_SERVER_URI=ldap://dc.cmp.cu:389', text)
        self.assertIn('UPDATE_CHECK_TTL=600', text)
        self.assertIn('# comentario', text)
        self.assertIn('DB_PASS=supersecreto', text)
        self.assertNotEqual(before, text)

    def test_post_rejects_starttls_and_implicit_tls_together(self):
        self.client.force_login(self.superuser)
        before = self.env_path.read_text(encoding='utf-8')

        response = self.client.post(
            self.url,
            self._payload(EMAIL_PORT='587', EMAIL_USE_TLS='on', EMAIL_USE_SSL='on'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context['form'], 'EMAIL_USE_SSL', self._tls_conflict_message()
        )
        self.assertEqual(self.env_path.read_text(encoding='utf-8'), before)

    def test_post_rejects_port_465_without_implicit_tls(self):
        self.client.force_login(self.superuser)
        before = self.env_path.read_text(encoding='utf-8')

        response = self.client.post(self.url, self._payload(EMAIL_PORT='465', EMAIL_USE_SSL=''))

        self.assertEqual(response.status_code, 200)
        self.assertIn('EMAIL_USE_SSL', response.context['form'].errors)
        self.assertEqual(self.env_path.read_text(encoding='utf-8'), before)

    def test_post_rejects_ldap_server_without_search_base(self):
        self.client.force_login(self.superuser)
        before = self.env_path.read_text(encoding='utf-8')

        response = self.client.post(
            self.url,
            self._payload(
                LDAP_ENABLED='on',
                LDAP_SERVER_URI='ldap://dc.cmp.cu:389',
                LDAP_USER_SEARCH_BASE='',
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('LDAP_USER_SEARCH_BASE', response.context['form'].errors)
        self.assertEqual(self.env_path.read_text(encoding='utf-8'), before)

    def test_post_rejects_ldap_enabled_without_server(self):
        self.client.force_login(self.superuser)
        before = self.env_path.read_text(encoding='utf-8')

        response = self.client.post(
            self.url,
            self._payload(LDAP_ENABLED='on', LDAP_SERVER_URI=''),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('LDAP_SERVER_URI', response.context['form'].errors)
        self.assertEqual(self.env_path.read_text(encoding='utf-8'), before)

    def test_ldap_toggle_is_written(self):
        self.client.force_login(self.superuser)

        self.client.post(
            self.url,
            self._payload(
                LDAP_ENABLED='on',
                LDAP_SERVER_URI='ldap://dc.cmp.cu:389',
                LDAP_USER_SEARCH_BASE='ou=users,dc=cmp,dc=cu',
            ),
        )

        text = self.env_path.read_text(encoding='utf-8')
        self.assertIn('LDAP_ENABLED=True', text)

    @staticmethod
    def _tls_conflict_message():
        return (
            'STARTTLS y TLS implícito no pueden estar activos a la vez: el sitio '
            'no arrancaría. Use el 587 con STARTTLS o el 465 con TLS implícito.'
        )

    def test_form_fields_render_placeholders(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.url)
        self.assertContains(response, 'placeholder="Ej.: https://cmp.camaguey.cu/"')
        self.assertContains(response, 'placeholder="Ej.: smtp.cmp.cu"')
        self.assertContains(response, 'placeholder="Ej.: ldap://dc.cmp.cu:389"')

    def test_password_placeholder_does_not_reveal_the_stored_value(self):
        self.env_path.write_text('EMAIL_HOST_PASSWORD=claveSmtpSecreta\n', encoding='utf-8')
        self.client.force_login(self.superuser)
        response = self.client.get(self.url)
        self.assertContains(response, 'placeholder="Ej.: contraseña nueva"')
        self.assertNotContains(response, 'claveSmtpSecreta')

    def test_page_does_not_render_variable_names(self):
        """La página habla en castellano: no publica identificadores de `settings`."""
        self.client.force_login(self.superuser)
        html = self.client.get(self.url).content.decode()

        for name in (
            'SECRET_KEY',
            'ENCRYPTION_KEY',
            'DB_PASS',
            'ALLOWED_HOSTS',
            'CSRF_TRUSTED_ORIGINS',
            'CORS_ALLOWED_ORIGINS',
            'CONN_MAX_AGE',
            'EMAIL_BACKEND',
        ):
            self.assertNotIn(name, html)
        self.assertContains(self.client.get(self.url), 'Clave secreta de Django')

    def test_readonly_labels_are_not_identifiers(self):
        environment = build_environment(environ={}, file_values={})
        for group in environment['groups']:
            for item in group['items']:
                self.assertNotRegex(item['label'], r'^[A-Z0-9_]+$')

    def test_engine_names_are_not_module_paths(self):
        environment = build_environment(environ={}, file_values={})
        groups = {group['key']: group for group in environment['groups']}
        for key in ('database', 'cache'):
            values = {item['label']: item['value'] for item in groups[key]['items']}
            self.assertNotIn('.', values['Motor'])

    def test_field_width_follows_the_catalog(self):
        """Los valores largos ocupan la fila; los cortos comparten columna."""
        self.client.force_login(self.superuser)
        html = self.client.get(self.url).content.decode()

        self.assertEqual(self._column_of(html, 'SITE_URL'), 'col-12')
        self.assertEqual(self._column_of(html, 'LDAP_SERVER_URI'), 'col-12')
        self.assertEqual(self._column_of(html, 'EMAIL_PORT'), 'col-md-6')
        self.assertEqual(self._column_of(html, 'UPDATE_CHECK_TTL'), 'col-md-6')

    def test_help_text_comes_before_the_running_value(self):
        """El orden es etiqueta, campo, ayuda y por último el valor en ejecución.

        Con la ayuda debajo del valor, el operador lee primero un dato que no
        puede cambiar y después la explicación del campo que sí edita.
        """
        self.client.force_login(self.superuser)
        html = self.client.get(self.url).content.decode()

        start = html.index('id="id_EMAIL_PORT"')
        block = html[start : html.index('id="id_', start + 1)]
        self.assertIn('El 587 usa STARTTLS', block)
        self.assertLess(block.index('El 587 usa STARTTLS'), block.index('En ejecución'))

    @staticmethod
    def _column_of(html: str, key: str) -> str:
        """Clase del contenedor de grilla que envuelve al campo `key`."""
        index = html.index(f'id="id_{key}"')
        start = html.rindex('class="col-', 0, index) + len('class="')
        return html[start : html.index('"', start)]

    def test_page_does_not_render_server_paths(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.url)
        for value in (
            getattr(settings, 'EMAIL_FILE_PATH', ''),
            getattr(settings, 'UPDATE_DEPLOYED_SHA_FILE', ''),
            getattr(settings, 'UPDATE_STATUS_FILE', ''),
            getattr(settings, 'UPDATE_REQUEST_FILE', ''),
            getattr(settings, 'UPDATE_PROGRESS_FILE', ''),
        ):
            if value:
                self.assertNotContains(response, str(value))

    def test_post_writes_only_the_changed_key(self):
        self.client.force_login(self.superuser)
        response = self.client.post(self.url, self._payload(NOTIFICATIONS_MAX_VISIBLE='25'))
        self.assertEqual(response.status_code, 302)

        text = self.env_path.read_text(encoding='utf-8')
        self.assertIn('NOTIFICATIONS_MAX_VISIBLE=25', text)
        self.assertIn('# comentario', text)
        self.assertIn('DEBUG=True', text)
        self.assertIn('DB_PASS=supersecreto', text)
        self.assertNotIn('SITE_URL=', text)
        self.assertNotIn('FTP_OBS_HOST=', text)

    def test_post_with_invalid_value_does_not_touch_the_file(self):
        self.client.force_login(self.superuser)
        before = self.env_path.read_text(encoding='utf-8')

        response = self.client.post(self.url, self._payload(NOTIFICATIONS_MAX_VISIBLE='0'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.env_path.read_text(encoding='utf-8'), before)

    def test_post_rejects_non_http_site_url(self):
        self.client.force_login(self.superuser)
        before = self.env_path.read_text(encoding='utf-8')

        response = self.client.post(self.url, self._payload(SITE_URL='ftp://cmp.cu/'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.env_path.read_text(encoding='utf-8'), before)

    def test_post_logs_the_change_without_values(self):
        self.client.force_login(self.superuser)
        self.client.post(self.url, self._payload(NOTIFICATIONS_MAX_VISIBLE='25'))

        entry = ActivityLog.objects.get(object_repr='Entorno del servidor')
        self.assertEqual(entry.user, self.superuser)
        self.assertIn('NOTIFICATIONS_MAX_VISIBLE', entry.message)
        self.assertNotIn('25', entry.message)

    def test_post_when_file_is_not_writable_does_not_touch_it(self):
        if CANNOT_DROP_PRIVILEGES:
            self.skipTest('root ignora los permisos del directorio')
        self.client.force_login(self.superuser)
        before = self.env_path.read_text(encoding='utf-8')
        self.env_path.parent.chmod(0o500)
        self.addCleanup(self.env_path.parent.chmod, 0o700)

        response = self.client.post(self.url, self._payload(NOTIFICATIONS_MAX_VISIBLE='25'))

        self.assertEqual(response.status_code, 302)
        self.env_path.parent.chmod(0o700)
        self.assertEqual(self.env_path.read_text(encoding='utf-8'), before)
        self.assertFalse(ActivityLog.objects.filter(object_repr='Entorno del servidor').exists())

    def test_menu_shows_the_link_only_for_superusers(self):
        self.staff.user_permissions.add(
            Permission.objects.get(
                content_type__app_label='core', codename='change_siteconfiguration'
            )
        )
        template = 'includes/dashboard/menu/_configuracion.html'

        request = RequestFactory().get('/')
        request.user = self.superuser
        self.assertIn('Entorno del servidor', render_to_string(template, request=request))

        request.user = self.staff
        self.assertNotIn('Entorno del servidor', render_to_string(template, request=request))
